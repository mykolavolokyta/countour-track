from decimal import Decimal

import pytest
from sqlalchemy import select

from backend import adapters, services
from backend.adapters import binance_futures, binance_funding, bybit_unified, holding, SyncError
from backend.db import Session, Snapshot, Source, now
from backend.security import seal


def test_futures_equity_includes_pnl_once():
    rows = binance_futures({'assets': [
        {'asset':'USDT','walletBalance':'1000','marginBalance':'1125','unrealizedProfit':'125'},
        {'asset':'BTC','walletBalance':'0.1','marginBalance':'0.09','unrealizedProfit':'-0.01'},
    ]}, 'Futures')
    assert [r['quantity'] for r in rows] == ['1125','0.09']
    assert rows[0]['pnl'] == '125'


def test_unified_equity_and_negative_liability():
    rows = bybit_unified({'totalEquity':'975','coin':[
        {'coin':'USDT','equity':'1000','walletBalance':'900','usdValue':'1000','unrealisedPnl':'100'},
        {'coin':'USDC','equity':'-25','walletBalance':'0','usdValue':'-25','unrealisedPnl':'0'},
    ]})
    assert sum(Decimal(h['value_usd']) for h in rows) == Decimal('975')
    assert rows[0]['quantity'] == '1000'


def test_inconsistent_unified_response_fails_closed():
    with pytest.raises(SyncError):
        bybit_unified({'totalEquity':'2000','coin':[{'coin':'USDT','equity':'1000','usdValue':'1000'}]})


def test_funding_includes_locked_frozen_and_withdrawing():
    rows = binance_funding([{'asset':'USDT','free':'1.1','locked':'2.2','freeze':'3.3','withdrawing':'4.4'}])
    assert rows[0]['quantity'] == '11.0'


def test_manual_edits_are_immediate_and_historical(member):
    body = {'kind':'manual','name':'Готівка','symbol':'USD','quantity':'100.000000000000000001'}
    response = member.post('/api/sources', json=body)
    assert response.status_code == 201
    source_id = response.json()['id']
    assert member.get('/api/portfolio').json()['total_usd'] == '100.000000000000000001'
    assert member.put(f'/api/sources/{source_id}', json=dict(body, quantity='200')).status_code == 200
    with Session() as db:
        snapshots = db.scalars(select(Snapshot).order_by(Snapshot.id)).all()
        assert [s.total_usd for s in snapshots] == ['100.000000000000000001','200']
    assert member.delete(f'/api/sources/{source_id}').status_code == 200
    assert member.get('/api/portfolio').json()['total_usd'] == '0'
    with Session() as db:
        assert db.get(Source, source_id).active is False
        assert len(db.scalars(select(Snapshot)).all()) == 3


def test_custom_valuation_and_unknown_asset_are_explicit(member):
    member.post('/api/sources', json={'kind':'manual','name':'Інше','symbol':'CUSTOM','quantity':'5'})
    p = member.get('/api/portfolio').json()
    assert p['incomplete'] and p['unpriced'] == 1
    assert p['holdings'][0]['value_usd'] is None
    member.post('/api/sources', json={'kind':'manual','name':'Інше 2','symbol':'CUSTOM','quantity':'3','price_usd':'2.10'})
    assert member.get('/api/portfolio').json()['total_usd'] == '6.30'


def test_failures_preserve_snapshot_and_redact_secrets(member, monkeypatch):
    res = member.post('/api/sources', json={'kind':'binance','name':'Binance','api_key':'sensitive-key','api_secret':'sensitive-secret'})
    source_id = res.json()['id']
    assert 'sensitive-key' not in res.text
    with Session() as db:
        assert 'sensitive-key' not in db.get(Source, source_id).credentials
    monkeypatch.setattr(services, 'collect', lambda *_: ([holding('USD','1000','Futures',usd='1000')], [{'symbol':'BTC','notional':'100000'}], []))
    assert services.sync_source(source_id)
    assert member.get('/api/portfolio').json()['total_usd'] == '1000'
    with Session() as db:
        db.get(Source, source_id).next_sync = now()-1
        db.commit()
    def fail(*_):
        raise RuntimeError('https://api.example?apiKey=sensitive-key&secret=sensitive-secret')
    monkeypatch.setattr(services, 'collect', fail)
    assert services.sync_source(source_id) is False
    p = member.get('/api/portfolio')
    assert 'sensitive' not in p.text
    assert p.json()['total_usd'] == '1000'
    assert p.json()['sources'][0]['status'] == 'error'
    with Session() as db:
        assert len(db.scalars(select(Snapshot)).all()) == 1
        assert db.get(Source, source_id).next_sync > now()


def test_sync_lease_and_refresh_throttle(member, monkeypatch):
    res = member.post('/api/sources', json={'kind':'bsc','name':'BSC','address':'0x'+'1'*40})
    source_id = res.json()['id']
    with Session() as db:
        s=db.get(Source,source_id)
        s.lease_until=now()+500
        s.last_attempt=now()
        db.commit()
    def unexpected(*_):
        pytest.fail('Must not call provider while another worker owns the lease')
    monkeypatch.setattr(services, 'collect', unexpected)
    assert services.sync_source(source_id) is False
    assert member.post(f'/api/sources/{source_id}/refresh').status_code == 429
    assert member.delete(f'/api/sources/{source_id}').status_code == 409


def test_intervals_persist_and_override(member):
    a=member.post('/api/sources',json={'kind':'manual','name':'A','symbol':'USD','quantity':'1'}).json()['id']
    b=member.post('/api/sources',json={'kind':'manual','name':'B','symbol':'USD','quantity':'2','interval_minutes':15}).json()['id']
    assert member.put('/api/settings',json={'interval_minutes':120}).status_code == 200
    sources={s['id']:s for s in member.get('/api/portfolio').json()['sources']}
    assert sources[a]['effective_interval']==120
    assert sources[b]['effective_interval']==15
    assert sources[a]['next_sync']-sources[a]['last_success']==7200
    assert member.put('/api/settings',json={'interval_minutes':0}).status_code==422


def test_bsc_contract_identity_never_uses_symbol_price(monkeypatch):
    monkeypatch.setattr(adapters,'prices',lambda _: {'USDT':Decimal(1)})
    rows=adapters.value_holdings([holding('USDT','500','BSC','bsc:56:unknown')])
    assert rows[0]['value_usd'] is None


def test_history_seeds_before_range_and_retains_archives(member):
    a=member.post('/api/sources',json={'kind':'manual','name':'A','symbol':'USD','quantity':'100'}).json()['id']
    with Session() as db:
        db.scalar(select(Snapshot).where(Snapshot.source_id==a)).taken_at=now()-10*86400
        db.commit()
    member.post('/api/sources',json={'kind':'manual','name':'B','symbol':'USD','quantity':'50'})
    points=member.get('/api/history?days=7').json()
    assert points[0]['value_usd']=='100'
    assert points[-1]['value_usd']=='150'
    member.delete(f'/api/sources/{a}')
    assert member.get('/api/history?days=7').json()[-1]['value_usd']=='50'


def test_wallet_validation_and_duplicates(member):
    assert member.post('/api/sources',json={'kind':'bsc','name':'bad','address':'not-an-address'}).status_code==422
    b={'kind':'bsc','name':'BSC','address':'0x'+'a'*40}
    assert member.post('/api/sources',json=b).status_code==201
    assert member.post('/api/sources',json=dict(b,address='0x'+'A'*40)).status_code==409
    assert member.post('/api/sources',json={'kind':'manual','name':'Cash','symbol':'USD','quantity':'NaN'}).status_code==422


def test_binance_and_bybit_write_keys_are_rejected(monkeypatch):
    class Binance:
        api={'sapi':{'get':{'account/apiRestrictions':1}}}
        def request(self,*_,**kwargs): return {'enableSpotAndMarginTrading':True}
    monkeypatch.setattr(adapters.ccxt,'binance',lambda _:Binance())
    with pytest.raises(SyncError,match='лише для читання'):
        adapters.exchange_data('binance',{'api_key':'x','api_secret':'y'},{'futures':[]})
    class Bybit:
        api={'private':{'get':{'v5/user/query-api':1}}}
        def request(self,*_,**kwargs): return {'retCode':0,'result':{'readOnly':0}}
    monkeypatch.setattr(adapters.ccxt,'bybit',lambda _:Bybit())
    with pytest.raises(SyncError,match='Read-only'):
        adapters.exchange_data('bybit',{'api_key':'x','api_secret':'y'},{})


def test_locked_manual_crypto_classification_and_totals(member):
    body = {'kind': 'manual', 'name': 'Біржа без API', 'symbol': 'CUSTOM',
            'quantity': '50', 'price_usd': '2', 'asset_category': 'crypto', 'locked': True}
    response = member.post('/api/sources', json=body)
    assert response.status_code == 201
    source_id = response.json()['id']
    p = member.get('/api/portfolio').json()
    assert p['total_usd'] == p['locked_usd'] == '100'
    assert p['locked_count'] == 1 and p['locked_unpriced'] == 0
    assert p['holdings'][0]['asset_category'] == 'crypto'
    # An older client editing quantity must not clear newly introduced metadata.
    edit = {k: v for k, v in body.items() if k not in ('locked', 'asset_category')}
    assert member.put('/api/sources/' + source_id, json=edit).status_code == 200
    assert member.get('/api/portfolio').json()['holdings'][0]['locked'] is True
    assert member.put('/api/sources/' + source_id, json=dict(body, locked=False)).status_code == 200
    p = member.get('/api/portfolio').json()
    assert p['locked_usd'] == '0' and p['total_usd'] == '100'
    with Session() as db:
        snapshots = db.scalars(select(Snapshot).where(Snapshot.source_id == source_id).order_by(Snapshot.id)).all()
        assert snapshots[0].holdings[0]['locked'] is True
        assert snapshots[-1].holdings[0]['locked'] is False


def test_unpriced_locked_asset_counted_separately(member):
    member.post('/api/sources', json={'kind': 'manual', 'name': 'Locked', 'symbol': 'UNKNOWN',
               'quantity': '50', 'asset_category': 'crypto', 'locked': True})
    p = member.get('/api/portfolio').json()
    assert p['locked_count'] == p['locked_unpriced'] == 1
    assert p['locked_usd'] == p['total_usd'] == '0'
    assert p['incomplete'] is True
