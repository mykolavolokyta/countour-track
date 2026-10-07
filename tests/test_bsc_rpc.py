import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from backend import adapters, bsc_rpc, config, services
from backend.db import Session, Source, now

ADDRESS = '0x' + 'a' * 40
TOKEN = '0x' + 'b' * 40
OTHER = '0x' + 'c' * 40


def word(n):
    return '0x' + format(n, '064x')


def abi_string(value):
    b = value.encode()
    return '0x' + format(32, '064x') + format(len(b), '064x') + b.hex().ljust(64, '0')


@pytest.fixture
def rpc(monkeypatch):
    calls = []
    def default(payload):
        if payload['method'] == 'eth_chainId': return '0x38'
        if payload['method'] == 'eth_blockNumber': return '0x100'
        if payload['method'] == 'eth_getBalance': return hex(2 * 10**18)
        call = payload['params'][0]
        if call['data'].startswith('0x70a08231'): return word(1234567)
        if call['data'] == '0x313ce567': return word(6)
        if call['data'] == '0x95d89b41': return abi_string('USDT')
        raise AssertionError(payload)
    state = {'respond': default}
    def handler(request):
        body = json.loads(request.content)
        calls.append((request.url.host, body))
        result = state['respond'](body)
        if isinstance(result, dict):
            return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], **result})
        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': result})
    real_client = httpx.Client
    monkeypatch.setattr(bsc_rpc.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(config, 'BSC_RPC_URLS', ('https://first.example', 'https://second.example'))
    monkeypatch.setattr(config, 'MORALIS_API_KEY', '')
    monkeypatch.setattr(bsc_rpc, 'prices', lambda _: {'BNB': Decimal('600')})
    monkeypatch.setattr(bsc_rpc, 'token_prices', lambda _: {TOKEN: Decimal('0.99')})
    return calls, state, default


def test_reads_exact_units_one_block_no_moralis(rpc):
    calls, _, _ = rpc
    rows, positions, warnings = bsc_rpc.bsc_rpc_data(ADDRESS, [TOKEN])
    assert rows[0]['value_usd'] == '1200'
    assert rows[1]['quantity'] == '1.234567'
    assert rows[1]['value_usd'] == '1.22222133'
    assert rows[1]['asset_id'] == 'bsc:56:' + TOKEN
    assert not positions and '1 контрактів' in warnings[-1]
    assert all(body['params'][-1] == '0x100' for _, body in calls if body['method'] in ('eth_getBalance', 'eth_call'))
    balances = [body for _, body in calls if body['method'] == 'eth_call' and body['params'][0]['data'].startswith('0x70a08231')]
    assert balances[0]['params'][0]['data'] == '0x70a08231' + '0' * 24 + ADDRESS[2:]


def test_native_only_watchlist(rpc):
    calls, _, _ = rpc
    rows, _, warnings = bsc_rpc.bsc_rpc_data(ADDRESS, [])
    assert len(rows) == 1 and '0 контрактів' in warnings[-1]
    assert not any(b['method'] == 'eth_call' for _, b in calls)


def test_fallback_restarts_snapshot_after_wrong_chain(rpc):
    calls, state, default = rpc
    count = 0
    def respond(body):
        nonlocal count
        if body['method'] == 'eth_chainId':
            count += 1
            return '0x1' if count == 1 else '0x38'
        return default(body)
    state['respond'] = respond
    assert bsc_rpc.bsc_rpc_data(ADDRESS, [TOKEN])[0][0]['quantity'] == '2'
    assert calls[0][0] == 'first.example'
    assert all(host == 'second.example' for host, _ in calls[1:])


def test_reverting_token_keeps_entire_previous_snapshot(member, rpc, monkeypatch):
    source_id = member.post('/api/sources', json={'kind':'bsc','name':'RPC','address':ADDRESS,'token_contracts':[TOKEN]}).json()['id']
    assert services.sync_source(source_id)
    before = member.get('/api/portfolio').json()['total_usd']
    _, state, default = rpc
    def respond(body):
        if body['method'] == 'eth_call': return {'error':{'code':-32000, 'message':'secret-provider-token'}}
        return default(body)
    state['respond'] = respond
    with Session() as db:
        db.get(Source, source_id).next_sync = now() - 1
        db.commit()
    assert services.sync_source(source_id) is False
    p = member.get('/api/portfolio')
    assert p.json()['total_usd'] == before
    assert p.json()['sources'][0]['status'] == 'error'
    assert 'secret-provider-token' not in p.text


@pytest.mark.parametrize('bad', ['0x', word(256), 'not-hex'])
def test_bad_decimals_never_become_zero_or_assumed_18(rpc, bad):
    _, state, default = rpc
    state['respond'] = lambda b: bad if b['method']=='eth_call' and b['params'][0]['data']=='0x313ce567' else default(b)
    with pytest.raises(adapters.SyncError):
        bsc_rpc.bsc_rpc_data(ADDRESS, [TOKEN])


def test_optional_symbol_falls_back_to_contract(rpc):
    _, state, default = rpc
    state['respond'] = lambda b: '0x' if b['method']=='eth_call' and b['params'][0]['data']=='0x95d89b41' else default(b)
    rows, _, warnings = bsc_rpc.bsc_rpc_data(ADDRESS, [TOKEN])
    assert rows[1]['quantity'] == '1.234567'
    assert rows[1]['symbol'].startswith('0xbb')
    assert 'Символ' in warnings[0]


def test_symbol_decoding():
    assert bsc_rpc.decode_symbol(abi_string('USDT')) == 'USDT'
    assert bsc_rpc.decode_symbol('0x' + b'BNB'.hex().ljust(64,'0')) == 'BNB'
    with pytest.raises(ValueError): bsc_rpc.decode_symbol('0x' + 'ff' * 100)


def test_zero_balance_and_missing_prices_are_distinct(rpc,monkeypatch):
    _, state, default = rpc
    def respond(b):
        if b['method']=='eth_call' and b['params'][0]['to']==OTHER and b['params'][0]['data'].startswith('0x70a08231'):
            return word(0)
        return default(b)
    state['respond']=respond
    monkeypatch.setattr(bsc_rpc,'token_prices',lambda _: {})
    rows, _, warnings = bsc_rpc.bsc_rpc_data(ADDRESS, [TOKEN,OTHER])
    assert len(rows)==2 and rows[1]['value_usd'] is None
    assert 'неповна' in warnings[-1]


def test_api_persists_provider_and_validates_contracts(member):
    body={'kind':'bsc','name':'RPC','address':ADDRESS,'token_contracts':[TOKEN.upper().replace('0X','0x')]}
    result=member.post('/api/sources',json=body)
    assert result.status_code==201
    assert result.json()['config']=={'address':ADDRESS,'provider':'rpc','token_contracts':[TOKEN],'chain':'bsc'}
    for contracts in ([TOKEN,TOKEN.upper()],['bad'],['0x'+'0'*40],[TOKEN]*21):
        result=member.post('/api/sources',json=dict(body,token_contracts=contracts))
        assert result.status_code==422 and 'контракти' in result.json()['detail']


def test_legacy_moralis_preserved_until_explicit_switch(member,monkeypatch):
    with Session() as db:
        source=Source(user_id='test-user',name='Legacy',kind='bsc',config={'address':ADDRESS})
        db.add(source)
        db.commit()
        source_id=source.id
    called=[]
    monkeypatch.setattr(adapters,'bsc_data',lambda address: called.append(address))
    adapters.collect(SimpleNamespace(kind='bsc',config={'address':ADDRESS}),{})
    assert called==[ADDRESS]
    body={'kind':'bsc','name':'Renamed','address':ADDRESS}
    result=member.put('/api/sources/'+source_id,json=body)
    assert result.json()['config']['provider']=='moralis'
    result=member.put('/api/sources/'+source_id,json=dict(body,provider='rpc',token_contracts=[TOKEN]))
    assert result.json()['config']['provider']=='rpc'
    assert result.json()['config']['token_contracts']==[TOKEN]


def test_contract_pricing_and_rate_limit(monkeypatch):
    bsc_rpc._token_price_cache.clear()
    calls=[]
    state={'status':200}
    def handler(request):
        calls.append(request)
        return httpx.Response(state['status'],json={TOKEN:{'usd':'1.1234567890123456789'}})
    client=httpx.Client
    monkeypatch.setattr(bsc_rpc.httpx,'Client',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    monkeypatch.setattr(config,'COINGECKO_DEMO_API_KEY','test-demo-key')
    assert bsc_rpc.token_prices([TOKEN])=={TOKEN:Decimal('1.1234567890123456789')}
    assert calls[0].url.params['contract_addresses']==TOKEN
    assert 'binance-smart-chain' in calls[0].url.path
    assert calls[0].headers['x-cg-demo-api-key']=='test-demo-key'
    bsc_rpc.token_prices([TOKEN])
    assert len(calls)==1
    bsc_rpc._token_price_cache.clear()
    state['status']=429
    assert bsc_rpc.token_prices([TOKEN])=={}


@pytest.mark.parametrize('chain', ['ethereum', 'arbitrum', 'optimism', 'base'])
def test_evm_network_balance_and_valuation(chain, rpc, monkeypatch):
    calls, state, default = rpc
    network = config.EVM_CHAINS[chain]
    state['respond'] = lambda b: hex(network['id']) if b['method'] == 'eth_chainId' else default(b)
    monkeypatch.setattr(bsc_rpc, 'prices', lambda symbols: {'ETH': Decimal('2000')})
    monkeypatch.setattr(bsc_rpc, 'token_prices', lambda contracts, selected: {TOKEN: Decimal('1')} if selected == chain else {})
    rows, _, warnings = adapters.collect(SimpleNamespace(kind='bsc', config={
        'address': ADDRESS, 'chain': chain, 'provider': 'rpc', 'token_contracts': [TOKEN]}), {})
    assert rows[0]['symbol'] == 'ETH' and rows[0]['value_usd'] == '4000'
    assert rows[0]['asset_id'] == f"evm:{network['id']}:native"
    assert rows[1]['asset_id'] == f"evm:{network['id']}:{TOKEN}"
    assert rows[1]['value_usd'] == '1.234567'
    assert rows[1]['account'] == network['name']
    assert network['name'] in warnings[-1]


def test_evm_wrong_chain_is_rejected(rpc):
    with pytest.raises(adapters.SyncError):
        bsc_rpc.bsc_rpc_data(ADDRESS, [], 'ethereum')


def test_same_address_different_networks_and_immutable_chain(member):
    body = {'kind': 'bsc', 'name': 'Wallet', 'address': ADDRESS}
    bsc = member.post('/api/sources', json=body)
    eth = member.post('/api/sources', json=dict(body, chain='ethereum'))
    assert bsc.status_code == eth.status_code == 201
    assert member.post('/api/sources', json=dict(body, chain='ethereum')).status_code == 409
    assert member.post('/api/sources', json=dict(body, chain='unsupported')).status_code == 422
    assert member.post('/api/sources', json=dict(body, chain='base', provider='moralis')).status_code == 422
    source_id = eth.json()['id']
    # An older client omitting chain must not turn Ethereum into BSC or cause
    # a spurious duplicate conflict with the existing BSC source.
    updated = member.put('/api/sources/' + source_id, json=dict(body, name='Renamed'))
    assert updated.status_code == 200 and updated.json()['config']['chain'] == 'ethereum'
    assert member.put('/api/sources/' + source_id, json=dict(body, chain='base')).status_code == 422
    assert member.put('/api/sources/' + source_id, json=dict(body, provider='moralis')).status_code == 422


def test_evm_contract_price_cache_is_network_scoped(monkeypatch):
    bsc_rpc._token_price_cache.clear()
    calls = []
    def handler(request):
        calls.append(request.url.path)
        value = '2' if request.url.path.endswith('/ethereum') else '3'
        return httpx.Response(200, json={TOKEN: {'usd': value}})
    client = httpx.Client
    monkeypatch.setattr(bsc_rpc.httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    assert bsc_rpc.token_prices([TOKEN], 'ethereum')[TOKEN] == Decimal('2')
    assert bsc_rpc.token_prices([TOKEN], 'base')[TOKEN] == Decimal('3')
    assert bsc_rpc.token_prices([TOKEN], 'ethereum')[TOKEN] == Decimal('2')
    assert len(calls) == 2


def test_unpriced_evm_token_does_not_borrow_symbol_price(monkeypatch):
    monkeypatch.setattr(adapters, 'prices', lambda symbols: {'USDT': Decimal('1')})
    rows = [adapters.holding('USDT', Decimal('100'), 'Ethereum', 'evm:1:' + TOKEN)]
    adapters.value_holdings(rows)
    assert rows[0]['value_usd'] is None
