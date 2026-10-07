"""Read-only adapters. No order, withdrawal, or transfer methods are exposed."""
from decimal import Decimal, DefaultContext, getcontext
import time

import ccxt
import httpx

from . import config

DefaultContext.prec = 100
getcontext().prec = 100


def dec(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Non-finite provider amount')
    return result


def holding(symbol, quantity, account, asset_id=None, usd=None, pnl=None):
    return {'asset_id': asset_id or 'exchange:' + symbol, 'symbol': symbol,
            'quantity': str(dec(quantity)), 'account': account,
            'value_usd': str(dec(usd)) if usd is not None else None,
            'pnl': str(dec(pnl)) if pnl is not None else None}


class SyncError(Exception):
    """Only these carefully authored messages can reach the user."""


def binance_spot(rows):
    return [holding(r['asset'], dec(r['free']) + dec(r['locked']), 'Spot') for r in rows
            if dec(r['free']) + dec(r['locked']) != 0]


def binance_funding(rows):
    result = []
    for r in rows:
        amount = sum((dec(r.get(k, '0')) for k in ('free', 'locked', 'freeze', 'withdrawing')), Decimal(0))
        if amount:
            result.append(holding(r['asset'], amount, 'Funding'))
    return result


def binance_futures(data, account):
    # Asset-level marginBalance includes unrealised PnL. Never add positions or
    # account-level totalMarginBalance on top (multi-asset totals have other units).
    return [holding(a['asset'], a['marginBalance'], account, pnl=a['unrealizedProfit'])
            for a in data['assets'] if dec(a['marginBalance']) != 0 or dec(a['unrealizedProfit']) != 0]


def bybit_unified(data):
    result = []
    for a in data['coin']:
        if dec(a['equity']) != 0:
            result.append(holding(a['coin'], a['equity'], 'Unified · Spot + Futures',
                                  usd=a['usdValue'] if a.get('usdValue') not in (None, '') else None,
                                  pnl=a.get('unrealisedPnl') or '0'))
    # In isolated mode account-wide totals are absent. Coin equity still applies.
    if data.get('totalEquity') not in (None, '') and all(h['value_usd'] is not None for h in result):
        difference = dec(data['totalEquity']) - sum((dec(h['value_usd']) for h in result), Decimal(0))
        if abs(difference) > Decimal('0.05'):
            raise SyncError('Bybit: сума активів не збігається з equity. Попередній баланс збережено.')
    return result


def exchange_data(kind, credentials, options):
    client = getattr(ccxt, kind)({'apiKey': credentials['api_key'], 'secret': credentials['api_secret'],
                                 'enableRateLimit': True, 'timeout': 20000})
    def request(path, api, method, params=None):
        # Raw endpoint access preserves exchange-specific accounting while still
        # applying CCXT's endpoint weight, signer, error mapping and throttler.
        cost = client.api[api][method.lower()][path]
        cost_config = cost if isinstance(cost, dict) else {'cost': cost}
        return client.request(path, api, method, params or {}, config=cost_config)
    holdings, positions, warnings = [], [], []
    if kind == 'binance':
        permissions = request('account/apiRestrictions', 'sapi', 'GET')
        if any(permissions.get(k) for k in ('enableWithdrawals', 'enableSpotAndMarginTrading', 'enableFutures', 'enableMargin', 'permitsUniversalTransfer', 'enableInternalTransfer')):
            raise SyncError('Binance: потрібен ключ лише для читання, без торгівлі, переказів і виведення.')
        holdings.extend(binance_spot(request('account', 'private', 'GET')['balances']))
        holdings.extend(binance_funding(request('asset/get-funding-asset', 'sapi', 'POST')))
        for mode in options['futures']:
            api = 'fapiPrivateV3' if mode == 'usdm' else 'dapiPrivate'
            label = 'Futures USDⓈ-M' if mode == 'usdm' else 'Futures COIN-M'
            data = request('account', api, 'GET')
            holdings.extend(binance_futures(data, label))
            # Position details are informative only and never enter portfolio totals.
            risk = request('positionRisk', api, 'GET')
            for p in risk:
                if dec(p['positionAmt']) == 0:
                    continue
                settle = p.get('marginAsset')
                if not settle:
                    settle = next((a['asset'] for a in data['assets'] if p['symbol'].startswith(a['asset'])), '—') if mode == 'coinm' else ('USDC' if p['symbol'].endswith('USDC') else 'USDT')
                positions.append({'symbol': p['symbol'], 'account': label,
                                  'side': p['positionSide'] if p['positionSide'] != 'BOTH' else ('LONG' if dec(p['positionAmt']) > 0 else 'SHORT'),
                                  'size': str(abs(dec(p['positionAmt']))), 'entry': p['entryPrice'],
                                  'mark': p['markPrice'], 'pnl': p['unRealizedProfit'], 'settle': settle,
                                  'leverage': p.get('leverage') or None,
                                  'liquidation': p.get('liquidationPrice') or None})
    else:
        def get(path, params=None):
            response = request(path, 'private', 'GET', params)
            if int(response['retCode']) != 0:
                raise SyncError('Bybit відхилив запит. Перевірте права ключа й тип рахунку.')
            return response['result']
        if int(get('v5/user/query-api')['readOnly']) != 1:
            raise SyncError('Bybit: потрібен API-ключ у режимі Read-only.')
        account = get('v5/account/info')
        if int(account['unifiedMarginStatus']) not in (5, 6):
            raise SyncError('Ця версія підтримує Bybit UTA 2.0. Classic та UTA 1.0 потребують окремої інтеграції.')
        wallets = get('v5/account/wallet-balance', {'accountType': 'UNIFIED'})['list']
        if len(wallets) != 1:
            raise SyncError('Bybit не повернув очікуваний Unified Account.')
        holdings.extend(bybit_unified(wallets[0]))
        funds = get('v5/asset/transfer/query-account-coins-balance', {'accountType': 'FUND'})
        for a in funds['balance']:
            if dec(a['walletBalance']):
                holdings.append(holding(a['coin'], a['walletBalance'], 'Funding'))
        for params in ({'category': 'linear', 'settleCoin': 'USDT'}, {'category': 'linear', 'settleCoin': 'USDC'}, {'category': 'inverse'}):
            seen = set()
            while True:
                data = get('v5/position/list', dict(params, limit=200))
                for p in data['list']:
                    if dec(p['size']) == 0:
                        continue
                    settle = params.get('settleCoin') or p['symbol'].split('USD')[0]
                    positions.append({'symbol': p['symbol'], 'account': 'Unified', 'side': 'LONG' if p['side'] == 'Buy' else 'SHORT',
                                      'size': p['size'], 'entry': p['avgPrice'], 'mark': p['markPrice'],
                                      'pnl': p['unrealisedPnl'], 'settle': settle, 'leverage': p.get('leverage') or None,
                                      'liquidation': p.get('liqPrice') or None})
                cursor = data.get('nextPageCursor')
                if not cursor:
                    break
                if cursor in seen or len(seen) > 100:
                    raise SyncError('Bybit: не вдалося отримати всі сторінки позицій.')
                seen.add(cursor)
                params = dict(params, cursor=cursor)
    return holdings, positions, warnings


# Explicit identity map: unknown exchange symbols remain unpriced rather than
# guessing which of multiple same-symbol tokens CoinGecko means.
COINS = {'BTC': 'bitcoin', 'ETH': 'ethereum', 'BNB': 'binancecoin', 'SOL': 'solana',
         'USDT': 'tether', 'USDC': 'usd-coin', 'FDUSD': 'first-digital-usd', 'DAI': 'dai',
         'XRP': 'ripple', 'DOGE': 'dogecoin', 'ADA': 'cardano', 'AVAX': 'avalanche-2',
         'LINK': 'chainlink', 'DOT': 'polkadot', 'LTC': 'litecoin', 'TRX': 'tron',
         'TON': 'the-open-network', 'SUI': 'sui', 'ARB': 'arbitrum', 'OP': 'optimism',
         'AAVE': 'aave', 'UNI': 'uniswap', 'CAKE': 'pancakeswap-token', 'BCH': 'bitcoin-cash'}
_price_cache = {}


def prices(symbols):
    result = {'USD': Decimal(1)}
    missing = []
    for symbol in set(symbols):
        cached = _price_cache.get(symbol)
        if cached and time.monotonic() - cached[0] < 300:
            result[symbol] = cached[1]
        elif symbol in COINS:
            missing.append(symbol)
    with httpx.Client(timeout=20) as http:
        if missing:
            try:
                response = http.get('https://api.coingecko.com/api/v3/simple/price',
                                    headers={'x-cg-demo-api-key': config.COINGECKO_DEMO_API_KEY} if config.COINGECKO_DEMO_API_KEY else {},
                                    params={'ids': ','.join(COINS[s] for s in missing), 'vs_currencies': 'usd'})
                response.raise_for_status()
                data = response.json(parse_float=Decimal)
                for symbol in missing:
                    value = data.get(COINS[symbol], {}).get('usd')
                    if value is not None and dec(value) > 0:
                        result[symbol] = dec(value)
                        _price_cache[symbol] = (time.monotonic(), result[symbol])
            except (httpx.HTTPError, ValueError, KeyError):
                pass  # Missing prices are explicit, never interpreted as zero.
        if any(s in symbols for s in ('UAH', 'EUR')):
            try:
                response = http.get('https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json')
                response.raise_for_status()
                rates = {r['cc']: dec(r['rate']) for r in response.json(parse_float=Decimal)}
                result['UAH'] = Decimal(1) / rates['USD']
                result['EUR'] = rates['EUR'] / rates['USD']
            except (httpx.HTTPError, ValueError, KeyError, ZeroDivisionError):
                pass
    return result


def value_holdings(holdings):
    rates = prices([h['symbol'] for h in holdings if h['value_usd'] is None and not h['asset_id'].startswith(('bsc:', 'evm:'))])
    for h in holdings:
        if h['value_usd'] is None and not h['asset_id'].startswith(('bsc:', 'evm:')) and h['symbol'] in rates:
            h['value_usd'] = str(dec(h['quantity']) * rates[h['symbol']])
    return holdings


def bsc_data(address):
    if not config.MORALIS_API_KEY:
        raise SyncError('Для BSC додайте MORALIS_API_KEY у налаштування сервера (.env).')
    result, seen, spam = [], set(), 0
    params = {'chain': 'bsc', 'limit': 100}
    with httpx.Client(timeout=25) as http:
        while True:
            response = http.get(f'https://deep-index.moralis.io/api/v2.2/wallets/{address}/tokens',
                                headers={'X-API-Key': config.MORALIS_API_KEY}, params=params)
            response.raise_for_status()
            data = response.json(parse_float=Decimal)
            for token in data['result']:
                if token.get('possible_spam'):
                    spam += 1
                    continue
                quantity = dec(token['balance']) / (Decimal(10) ** int(token['decimals']))
                if not quantity:
                    continue
                contract = 'native' if token.get('native_token') else token['token_address'].lower()
                unit_price = token.get('usd_price')
                value = quantity * dec(unit_price) if unit_price is not None and dec(unit_price) > 0 else None
                result.append(holding(token['symbol'], quantity, 'BSC', 'bsc:56:' + contract, usd=value))
            cursor = data.get('cursor')
            if not cursor:
                break
            if cursor in seen or len(seen) > 100:
                raise SyncError('BSC: не вдалося отримати всі сторінки токенів.')
            seen.add(cursor)
            params['cursor'] = cursor
    warnings = [f'Приховано потенційних спам-токенів: {spam}.'] if spam else []
    warnings.append('BSC: токени на адресі; DeFi, NFT і заблоковані в протоколах кошти не включені.')
    return result, [], warnings


def collect(source, credentials):
    if source.kind == 'manual':
        c = source.config
        rows = [holding(c['symbol'], c['quantity'], 'Ручний облік', 'manual:' + c['symbol'],
                        usd=dec(c['quantity']) * dec(c['price_usd']) if c.get('price_usd') is not None else None)]
        warnings = ['Оцінка за заданою вручну ціною.'] if c.get('price_usd') is not None else []
        rows[0]['asset_category'] = c.get('asset_category', 'cash')
        rows[0]['locked'] = c.get('locked', False)
        if rows[0]['locked']:
            warnings.append('Позначено вручну як заблокований актив. Оцінка включена в портфель, доступ до коштів не підтверджено.')
        return value_holdings(rows), [], warnings
    if source.kind == 'privatbank':
        from .privatbank import privatbank_data
        return privatbank_data(source.config['iban'], credentials)
    if source.kind == 'bsc':
        if source.config.get('provider', 'moralis') == 'rpc':
            if source.config.get('chain') == 'all':
                from .evm_wallet import evm_wallet_data
                return evm_wallet_data(source.config['address'], source.config.get('contracts_by_chain', {}))
            from .bsc_rpc import bsc_rpc_data
            return bsc_rpc_data(source.config['address'], source.config.get('token_contracts', []), source.config.get('chain', 'bsc'))
        return bsc_data(source.config['address'])
    rows, positions, warnings = exchange_data(source.kind, credentials, source.config)
    return value_holdings(rows), positions, warnings
