from decimal import Decimal

import ccxt
import httpx
import pytest

from backend import adapters, services, config
from backend.db import Session, Source, now


def test_complete_binance_adapter_and_endpoint_weights(monkeypatch):
    client = ccxt.binance()
    calls=[]
    def request(path, api, method, params, config):
        calls.append((api,path,method,config))
        if path=='account/apiRestrictions': return {'enableReading':True}
        if path=='account' and api=='private': return {'balances':[{'asset':'BTC','free':'1','locked':'0.1'}]}
        if path=='asset/get-funding-asset': return [{'asset':'USDT','free':'10','locked':'2','freeze':'0','withdrawing':'0'}]
        if path=='account': return {'assets':[{'asset':'USDT' if api=='fapiPrivateV3' else 'BTC','marginBalance':'100','unrealizedProfit':'5'}]}
        if path=='positionRisk': return [{'symbol':'BTCUSDT','positionAmt':'0.1','positionSide':'BOTH','entryPrice':'90000','markPrice':'91000','unRealizedProfit':'100','marginAsset':'USDT','leverage':'3'}]
        raise AssertionError(path)
    monkeypatch.setattr(client,'request',request)
    monkeypatch.setattr(adapters.ccxt,'binance',lambda _:client)
    rows,positions,_=adapters.exchange_data('binance',{'api_key':'a','api_secret':'b'},{'futures':['usdm','coinm']})
    assert len(rows)==4 and len(positions)==2
    assert rows[0]['quantity']=='1.1'
    assert rows[1]['quantity']=='12'
    assert all(c[-1] for c in calls)
    assert not any('order' in c[1] for c in calls)


def test_complete_bybit_adapter_pages_positions_without_double_counting(monkeypatch):
    client=ccxt.bybit()
    calls=[]
    def request(path,api,method,params,config):
        calls.append((path,dict(params)))
        result={}
        if path=='v5/user/query-api': result={'readOnly':1}
        elif path=='v5/account/info': result={'unifiedMarginStatus':5}
        elif path=='v5/account/wallet-balance': result={'list':[{'totalEquity':'1050','coin':[{'coin':'USDT','equity':'1050','walletBalance':'1000','usdValue':'1050','unrealisedPnl':'50'}]}]}
        elif path=='v5/asset/transfer/query-account-coins-balance': result={'balance':[{'coin':'USDT','walletBalance':'100'}]}
        elif path=='v5/position/list':
            result={'list':[]}
            if params.get('settleCoin')=='USDT':
                if not params.get('cursor'):
                    result={'list':[{'symbol':'BTCUSDT','size':'0.01','side':'Buy','avgPrice':'100','markPrice':'105','unrealisedPnl':'50','leverage':'2'}], 'nextPageCursor':'page2'}
                else: result={'list':[], 'nextPageCursor':''}
        else: raise AssertionError(path)
        return {'retCode':0,'result':result}
    monkeypatch.setattr(client,'request',request)
    monkeypatch.setattr(adapters.ccxt,'bybit',lambda _:client)
    rows,positions,_=adapters.exchange_data('bybit',{'api_key':'a','api_secret':'b'},{})
    assert len(rows)==2 and rows[0]['value_usd']=='1050'
    assert rows[1]['quantity']=='100'
    assert len(positions)==1
    assert any(p.get('cursor')=='page2' for _,p in calls)


def test_bsc_pagination_native_spam_and_missing_price(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        assert request.headers['x-api-key']=='test-provider-key'
        if 'cursor=next' in str(request.url):
            return httpx.Response(200,json={'result':[{'symbol':'TOKEN','token_address':'0xABC','native_token':False,'balance':'1234567890123456789','decimals':18,'usd_price':None}], 'cursor':None})
        return httpx.Response(200,json={'result':[
            {'symbol':'BNB','native_token':True,'balance':'2000000000000000000','decimals':18,'usd_price':600},
            {'symbol':'SPAM','possible_spam':True},
        ],'cursor':'next'})
    transport=httpx.MockTransport(handler)
    real_client=httpx.Client
    monkeypatch.setattr(adapters.httpx,'Client',lambda **kwargs:real_client(transport=transport,**kwargs))
    monkeypatch.setattr(config,'MORALIS_API_KEY','test-provider-key')
    rows,positions,warnings=adapters.bsc_data('0x'+'1'*40)
    assert len(calls)==2 and len(rows)==2
    assert rows[0]['asset_id']=='bsc:56:native' and rows[0]['value_usd']=='1200'
    assert rows[1]['asset_id']=='bsc:56:0xabc'
    assert rows[1]['quantity']=='1.234567890123456789'
    assert rows[1]['value_usd'] is None
    assert 'спам' in warnings[0] and not positions


def test_expired_worker_cannot_overwrite_newer_edit(member,monkeypatch):
    source_id=member.post('/api/sources',json={'kind':'bsc','name':'Original','address':'0x'+'1'*40}).json()['id']
    def collect(source,credentials):
        with Session() as db:
            saved=db.get(Source,source.id)
            saved.lease_until=0
            saved.lease_token=None
            saved.name='New configuration'
            db.commit()
        return [adapters.holding('USD','100','Old snapshot',usd='100')],[],[]
    monkeypatch.setattr(services,'collect',collect)
    assert services.sync_source(source_id) is False
    p=member.get('/api/portfolio').json()
    assert p['total_usd']=='0' and p['holdings']==[]
    assert p['sources'][0]['name']=='New configuration'


def test_high_precision_manual_multiplication_in_api_thread(member):
    quantity='12345678901234567890.123456789012345678901234567890'
    price='1234567890.123456789012345'
    response=member.post('/api/sources',json={'kind':'manual','name':'Exact','symbol':'EXACT','quantity':quantity,'price_usd':price})
    assert response.status_code==201
    assert Decimal(member.get('/api/portfolio').json()['total_usd'])==Decimal(quantity)*Decimal(price)
