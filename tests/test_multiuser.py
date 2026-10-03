from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from backend import config, services
from backend.adapters import holding
from backend.db import Session, User, WebSession, Source, Snapshot, now
from backend.main import app
from backend.security import digest


@pytest.fixture
def second():
    with Session() as db:
        db.add(User(id='second', name='Другий'))
        db.flush()
        db.add(WebSession(id=digest('second-session'), user_id='second', csrf='second-csrf', expires_at=now()+3600))
        db.commit()
    with TestClient(app) as client:
        client.cookies.set('portfolio_session', 'second-session')
        client.headers.update({'origin': config.APP_ORIGIN, 'x-csrf-token': 'second-csrf'})
        yield client


def test_portfolios_sources_and_history_are_isolated(member, second):
    body = {'kind':'manual','name':'Secret cash','symbol':'USD','quantity':'123'}
    source_id = member.post('/api/sources', json=body).json()['id']
    other_id = second.post('/api/sources', json=dict(body, name='Other cash', quantity='789')).json()['id']
    p = second.get('/api/portfolio')
    assert p.json()['total_usd'] == '789'
    assert source_id not in p.text and 'Secret cash' not in p.text
    assert member.get('/api/portfolio').json()['total_usd'] == '123'
    for method, path, payload in [
        ('put', '/api/sources/'+source_id, body),
        ('post', '/api/sources/'+source_id+'/refresh', None),
        ('delete', '/api/sources/'+source_id, None),
    ]:
        kwargs = {'json': payload} if payload is not None else {}
        assert getattr(second, method)(path, **kwargs).status_code == 404
    # Both history branches: snapshots before the range and snapshots within it.
    with Session() as db:
        db.scalar(select(Snapshot).where(Snapshot.source_id == source_id)).taken_at = now()-10*86400
        db.commit()
    assert all(p['value_usd'] == '789' for p in second.get('/api/history?days=7').json())
    assert second.delete('/api/sources/'+other_id).status_code == 200
    assert member.get('/api/history?days=7').json()[-1]['value_usd'] == '123'


def test_per_user_duplicates_and_settings(member, second, monkeypatch):
    wallet = {'kind':'bsc','name':'Wallet','address':'0x'+'a'*40}
    first_id = member.post('/api/sources', json=wallet).json()['id']
    second_response = second.post('/api/sources', json=wallet)
    assert second_response.status_code == 201
    other_id = second_response.json()['id']
    assert second.post('/api/sources', json=wallet).status_code == 409
    assert second.put('/api/settings', json={'interval_minutes': 25}).status_code == 200
    assert member.get('/api/portfolio').json()['interval_minutes'] == 60
    monkeypatch.setattr(services, 'collect', lambda *_: ([holding('USD','10','Wallet',usd='10')], [], []))
    assert services.sync_source(first_id) and services.sync_source(other_id)
    with Session() as db:
        first, other = db.get(Source, first_id), db.get(Source, other_id)
        assert first.next_sync-first.last_success == 3600
        assert other.next_sync-other.last_success == 1500
    # Attempt to assign a source to another user is rejected by the request schema.
    assert second.post('/api/sources', json=dict(wallet, user_id='test-user')).status_code == 422


def test_logout_revokes_stolen_cookie(member):
    token = member.cookies.get('portfolio_session')
    assert member.post('/api/logout').status_code == 200
    member.cookies.set('portfolio_session', token)
    assert member.get('/api/session').status_code == 401


def test_disabled_user_is_rejected(member, second):
    with Session() as db:
        db.get(User, 'second').active = False
        db.commit()
    assert second.get('/api/session').status_code == 401
    assert member.get('/api/session').status_code == 200


def test_expired_session_is_rejected(second):
    with Session() as db:
        db.get(WebSession, digest('second-session')).expires_at = now()-1
        db.commit()
    assert second.get('/api/session').status_code == 401


def test_logout_all_is_scoped_to_user(member, second):
    with Session() as db:
        db.add(WebSession(id=digest('another-device'), user_id='test-user', csrf='another', expires_at=now()+3600))
        db.commit()
    assert member.post('/api/logout-all').status_code == 200
    member.cookies.set('portfolio_session', 'another-device')
    assert member.get('/api/session').status_code == 401
    assert second.get('/api/session').status_code == 200
