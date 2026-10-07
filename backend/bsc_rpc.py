"""Read native EVM balances and an explicit ERC-20 watchlist without an indexer or wallet key."""
from decimal import Decimal
import re
import time

import httpx

from . import config
from .adapters import SyncError, dec, holding, prices


class RPCFailure(Exception):
    pass


def uint(value, word=False):
    pattern = r'0x[0-9a-fA-F]{64}' if word else r'0x[0-9a-fA-F]{1,64}'
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise RPCFailure()
    return int(value[2:], 16)


def decode_symbol(value):
    """Support standard ABI strings and legacy bytes32; metadata is optional."""
    if not isinstance(value, str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2}){1,256}', value):
        raise ValueError('Invalid symbol')
    raw = bytes.fromhex(value[2:])
    if len(raw) == 32:
        text = raw.rstrip(b'\0').decode('utf-8')
    else:
        offset = int.from_bytes(raw[:32], 'big')
        if offset != 32 or len(raw) < 64:
            raise ValueError('Invalid ABI offset')
        length = int.from_bytes(raw[32:64], 'big')
        if not 1 <= length <= 64 or 64 + length > len(raw):
            raise ValueError('Invalid ABI length')
        text = raw[64:64 + length].decode('utf-8')
    if not text or len(text) > 32 or not text.isprintable():
        raise ValueError('Invalid symbol')
    return text


class RPC:
    def __init__(self, http, url, deadline=None):
        self.http, self.url, self.id = http, url, 0
        self.deadline = deadline

    def call(self, method, params):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise RPCFailure()
        self.id += 1
        response = self.http.post(self.url, json={'jsonrpc': '2.0', 'id': self.id,
                                                 'method': method, 'params': params})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get('jsonrpc') != '2.0' or data.get('id') != self.id or 'error' in data or 'result' not in data:
            # RPC errors/URLs may contain provider credentials; never expose them.
            raise RPCFailure()
        return data['result']


def read_balances(http, url, address, contracts, deadline=None, chain='bsc'):
    network = config.EVM_CHAINS[chain]
    prefix = ('bsc' if chain == 'bsc' else 'evm') + ':' + str(network['id']) + ':'
    rpc = RPC(http, url, deadline)
    if uint(rpc.call('eth_chainId', [])) != network['id']:
        raise RPCFailure()
    block = hex(uint(rpc.call('eth_blockNumber', [])))
    native = uint(rpc.call('eth_getBalance', [address, block]))
    rows = [holding(network['symbol'], Decimal(native) / Decimal(10) ** 18, network['name'], prefix + 'native')] if native else []
    warnings = []
    for contract in contracts:
        # All calls use the same block, including metadata. A bad/reverting
        # contract fails the complete snapshot rather than looking like zero.
        try:
            quantity = uint(rpc.call('eth_call', [{'to': contract, 'data': '0x70a08231' + address[2:].zfill(64)}, block]), word=True)
            decimals = uint(rpc.call('eth_call', [{'to': contract, 'data': '0x313ce567'}, block]), word=True)
            if decimals > 255:
                raise RPCFailure()
        except RPCFailure:
            raise RPCFailure(f'Не вдалося прочитати ERC-20 контракт {contract}. Перевірте адресу та доступність RPC.') from None
        if not quantity:
            continue
        try:
            symbol = decode_symbol(rpc.call('eth_call', [{'to': contract, 'data': '0x95d89b41'}, block]))
        except (RPCFailure, ValueError):
            symbol = contract[:8] + '…' + contract[-4:]
            warnings.append(f'Символ токена {contract} недоступний; баланс прочитано за контрактом.')
        rows.append(holding(symbol, Decimal(quantity) / Decimal(10) ** decimals, network['name'], prefix + contract))
    return rows, warnings, block


_token_price_cache = {}


def token_prices(contracts, chain='bsc'):
    platform = config.EVM_CHAINS[chain]['platform']
    result, missing = {}, []
    for contract in contracts:
        cached = _token_price_cache.get((platform, contract))
        if cached and time.monotonic() - cached[0] < 300:
            result[contract] = cached[1]
        else:
            missing.append(contract)
    if missing:
        try:
            with httpx.Client(timeout=15) as http:
                response = http.get('https://api.coingecko.com/api/v3/simple/token_price/' + platform,
                                    params={'contract_addresses': ','.join(missing), 'vs_currencies': 'usd'},
                                    headers={'x-cg-demo-api-key': config.COINGECKO_DEMO_API_KEY} if config.COINGECKO_DEMO_API_KEY else {})
                response.raise_for_status()
                data = response.json(parse_float=Decimal)
                for contract in missing:
                    value = data.get(contract, {}).get('usd')
                    if value is not None and dec(value) > 0:
                        result[contract] = dec(value)
                        _token_price_cache[(platform, contract)] = (time.monotonic(), result[contract])
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            pass
    return result


def bsc_rpc_data(address, contracts, chain='bsc'):
    if chain not in config.EVM_CHAINS:
        raise SyncError('Непідтримувана EVM-мережа.')
    network = config.EVM_CHAINS[chain]
    endpoints = config.BSC_RPC_URLS if chain == 'bsc' else config.EVM_RPC_URLS.get(chain, ())
    # Validate even when called from persisted config or outside the HTTP API.
    if not re.fullmatch(r'0x[0-9a-fA-F]{40}', address) or len(contracts) > 20:
        raise SyncError('Невірна EVM-адреса або понад 20 токенів у списку.')
    contracts = [c.lower() for c in contracts]
    if len(set(contracts)) != len(contracts) or any(not re.fullmatch(r'0x[0-9a-f]{40}', c) or int(c[2:], 16) == 0 for c in contracts):
        raise SyncError('Перевірте список ERC-20 контрактів: невірна адреса або повтор.')
    if not endpoints:
        raise SyncError('Не налаштовано EVM RPC. Задайте RPC_URLS відповідної мережі на сервері.')
    last_message = 'EVM RPC тимчасово недоступний або повернув іншу мережу. Попередні дані збережено.'
    deadline = time.monotonic() + 120
    with httpx.Client(timeout=10) as http:
        for endpoint in endpoints[:3]:
            if time.monotonic() >= deadline:
                break
            try:
                rows, warnings, block = read_balances(http, endpoint, address.lower(), contracts, deadline, chain)
                break
            except RPCFailure as exc:
                if str(exc):
                    last_message = str(exc)
            except (httpx.HTTPError, ValueError, TypeError):
                # Try the entire snapshot again on the next endpoint, never mix
                # rows from different nodes or carry a half-completed result.
                last_message = 'EVM RPC не прочитав усі баланси. Перевірте контракти токенів; попередні дані збережено.'
        else:
            raise SyncError(last_message)
        if time.monotonic() >= deadline:
            raise SyncError('EVM RPC не завершив оновлення за 2 хвилини. Попередні дані збережено.')
    native_price = prices([network['symbol']]).get(network['symbol']) if any(r['asset_id'].endswith(':native') for r in rows) else None
    contracts_with_balance = [r['asset_id'].split(':')[-1] for r in rows if not r['asset_id'].endswith(':native')]
    rates = token_prices(contracts_with_balance) if chain == 'bsc' else token_prices(contracts_with_balance, chain)
    for row in rows:
        contract = row['asset_id'].split(':')[-1]
        rate = native_price if contract == 'native' else rates.get(contract)
        if rate is not None:
            row['value_usd'] = str(dec(row['quantity']) * rate)
    warnings.append(f"RPC: {network['name']} · {network['symbol']} + {len(contracts)} контрактів зі списку. Інші токени, DeFi та NFT не включені. Блок {int(block, 16)}.")
    if any(r['value_usd'] is None for r in rows):
        warnings.append('Для частини активів немає ціни CoinGecko. Їхня кількість збережена, але оцінка портфеля неповна.')
    return rows, [], warnings
