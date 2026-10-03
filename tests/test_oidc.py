import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import httpx
import jwt
import pytest
from sqlalchemy import select

from backend import auth, config
from backend.db import Session, Identity, OAuthState, User, now
from backend.main import app
from backend.security import digest, unseal


@pytest.fixture
def oidc(monkeypatch):
    for p in ('GOOGLE', 'TELEGRAM'):
        monkeypatch.setattr(config, p+'_CLIENT_ID', 'test-client')
        monkeypatch.setattr(config, p+'_CLIENT_SECRET', 'test-secret')
    auth.key_cache.clear()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='key-1', alg='RS256', use='sig')
    return key, jwk


def start(client, provider='google', link=False):
    client.headers['origin'] = config.APP_ORIGIN
    response = client.post('/api/auth/'+provider+('/link' if link else '/start'))
    assert response.status_code == 200, response.text
    params = {k:v[0] for k,v in parse_qs(urlparse(response.json()['url']).query).items()}
    with Session() as db:
        flow = db.get(OAuthState, digest(params['state']))
        payload = unseal(flow.payload)
    assert params['code_challenge'] == base64.urlsafe_b64encode(hashlib.sha256(payload['verifier'].encode()).digest()).rstrip(b'=').decode()
    assert params['redirect_uri'] == config.APP_ORIGIN+'/api/auth/'+provider+'/callback'
    return params, payload


def provider_mock(monkeypatch, oidc, provider, payload, claims=None, subject='subject-1', bad_signature=False):
    key, jwk = oidc
    values = {'iss':'https://accounts.google.com' if provider=='google' else 'https://oauth.telegram.org',
              'aud':'test-client', 'sub':subject, 'nonce':payload['nonce'], 'iat':now(), 'exp':now()+300, 'name':'Test User'}
    values.update(claims or {})
    if bad_signature:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(values, key, algorithm='RS256', headers={'kid':'key-1'})
    real_client = httpx.Client
    calls = []
    def handler(request):
        calls.append(request)
        if str(request.url) == auth.PROVIDERS[provider]['jwks']:
            assert request.method == 'GET'
            return httpx.Response(200, json={'keys':[jwk]})
        assert str(request.url) == auth.PROVIDERS[provider]['token']
        assert request.method == 'POST'
        body = parse_qs(request.content.decode())
        assert body['code_verifier'] == [payload['verifier']]
        assert body['redirect_uri'] == [config.APP_ORIGIN+'/api/auth/'+provider+'/callback']
        if provider == 'telegram':
            assert request.headers['authorization'].startswith('Basic ')
        else:
            assert body['client_secret'] == ['test-secret']
        return httpx.Response(200, json={'id_token':token,'access_token':'never-persist-me'})
    monkeypatch.setattr(auth.httpx,'Client',lambda **kw: real_client(transport=httpx.MockTransport(handler),**kw))
    return calls


def finish(client, params, provider='google'):
    return client.get('/api/auth/'+provider+'/callback',
                      params={'code':'one-time-code','state':params['state']}, follow_redirects=False)


@pytest.mark.parametrize('provider',['google','telegram'])
def test_oidc_login_verified_identity_and_replay(client, oidc, monkeypatch, provider):
    params, payload = start(client, provider)
    calls = provider_mock(monkeypatch, oidc, provider, payload)
    result = finish(client, params, provider)
    assert result.status_code == 303 and result.headers['location'] == config.APP_ORIGIN+'/'
    assert 'one-time-code' not in result.text
    user = client.get('/api/session').json()['user']
    assert user['id'] != 'test-user' and user['providers'] == [provider]
    assert client.get('/api/portfolio').json()['sources'] == []
    assert len(calls) == 2
    assert 'auth_error' in finish(client, params, provider).headers['location']
    assert len(calls) == 2


@pytest.mark.parametrize('claims',[
    {'aud':'wrong-client'}, {'iss':'https://evil.example'}, {'exp':1},
    {'nonce':'wrong-nonce'}, {'sub':''}, {'iat':9999999999}, {'azp':'other-client'},
])
def test_bad_id_tokens_rejected(client, oidc, monkeypatch, claims):
    params, payload = start(client)
    provider_mock(monkeypatch, oidc, 'google', payload, claims)
    assert 'auth_error=failed' in finish(client, params).headers['location']
    assert client.get('/api/session').status_code == 401


def test_wrong_signature_rejected(client, oidc, monkeypatch):
    params, payload = start(client)
    provider_mock(monkeypatch, oidc, 'google', payload, bad_signature=True)
    assert 'auth_error=failed' in finish(client, params).headers['location']


def test_state_bound_to_browser_and_provider(client, oidc):
    params, _ = start(client)
    with TestClient(app) as stranger:
        assert 'auth_error=failed' in finish(stranger, params).headers['location']
    assert 'auth_error=failed' in finish(client, params, 'telegram').headers['location']


def test_expired_state_and_provider_cancellation(client, oidc):
    params, _ = start(client)
    with Session() as db:
        db.get(OAuthState, digest(params['state'])).expires_at = now()-1
        db.commit()
    assert 'auth_error=failed' in finish(client, params).headers['location']
    params, _ = start(client)
    response = client.get('/api/auth/google/callback', params={'state':params['state'],'error':'access_denied'}, follow_redirects=False)
    assert 'auth_error=failed' in response.headers['location']


def test_link_second_provider_then_login(member, oidc, monkeypatch):
    member.post('/api/sources',json={'kind':'manual','name':'Savings','symbol':'USD','quantity':'100'})
    params, payload = start(member, 'telegram', link=True)
    with monkeypatch.context() as m:
        provider_mock(m, oidc, 'telegram', payload)
        assert 'auth=linked' in finish(member, params, 'telegram').headers['location']
    assert sorted(member.get('/api/session').json()['user']['providers']) == ['google', 'telegram']
    assert member.post('/api/logout').status_code == 200
    params, payload = start(member, 'telegram')
    provider_mock(monkeypatch, oidc, 'telegram', payload)
    assert 'auth_error' not in finish(member, params, 'telegram').headers['location']
    assert member.get('/api/portfolio').json()['total_usd'] == '100'


def test_link_cannot_take_another_users_identity(member, oidc, monkeypatch):
    with Session() as db:
        db.add(User(id='someone', name='Someone'))
        db.flush()
        db.add(Identity(user_id='someone', provider='google', subject='subject-1'))
        db.commit()
    params, payload = start(member, link=True)
    provider_mock(monkeypatch, oidc, 'google', payload)
    assert 'auth_error=identity_used' in finish(member, params).headers['location']
    assert member.get('/api/session').json()['user']['id'] == 'test-user'


def test_link_requires_same_live_session(member, oidc):
    params, _ = start(member, link=True)
    assert member.post('/api/logout').status_code == 200
    assert 'auth_error=failed' in finish(member, params).headers['location']


def test_registration_disabled_and_start_origin(client, oidc, monkeypatch):
    assert client.post('/api/auth/google/start',headers={'origin':'https://evil.example'}).status_code == 403
    monkeypatch.setattr(config,'ALLOW_REGISTRATION',False)
    params, payload = start(client)
    provider_mock(monkeypatch, oidc, 'google', payload)
    assert 'auth_error=registration_closed' in finish(client, params).headers['location']
    assert client.get('/api/session').status_code == 401


def test_provider_secrets_not_exposed(client, oidc):
    result = client.get('/api/auth/providers')
    assert result.json()['google'] and result.json()['telegram']
    assert 'test-secret' not in result.text


def test_link_both_providers_keeps_single_portfolio(member, oidc, monkeypatch):
    for provider in ('google', 'telegram'):
        params, payload = start(member, provider, link=True)
        with monkeypatch.context() as m:
            provider_mock(m, oidc, provider, payload, subject='member-google' if provider=='google' else 'subject-1')
            assert 'auth=linked' in finish(member, params, provider).headers['location']
    session = member.get('/api/session').json()
    assert sorted(session['user']['providers']) == ['google','telegram']
    assert session['user']['id'] == 'test-user'


def test_link_start_requires_csrf(member, oidc):
    member.headers.pop('x-csrf-token')
    assert member.post('/api/auth/google/link').status_code == 403


def test_oidc_rate_limit(client, oidc):
    client.headers['origin'] = config.APP_ORIGIN
    for _ in range(20):
        assert client.post('/api/auth/google/start').status_code == 200
    assert client.post('/api/auth/google/start').status_code == 429


def test_secure_cookie_flags(client, oidc, monkeypatch):
    params, payload = start(client)
    provider_mock(monkeypatch, oidc, 'google', payload)
    monkeypatch.setattr(config, 'COOKIE_SECURE', True)
    result = finish(client, params)
    header = result.headers.get_list('set-cookie')[0].lower()
    assert 'httponly' in header and 'secure' in header and 'samesite=lax' in header


def test_cannot_unlink_last_provider(member, oidc):
    assert member.delete('/api/auth/google/link').status_code == 409
    assert member.get('/api/session').json()['user']['providers'] == ['google']


def test_unlink_provider_preserves_other_accounts(member, oidc):
    from backend.db import WebSession
    with Session() as db:
        db.add(Identity(user_id='test-user', provider='telegram', subject='tg-1'))
        db.add(WebSession(id=digest('old-device'), user_id='test-user', csrf='old', expires_at=now()+3600))
        db.commit()
    assert member.delete('/api/auth/telegram/link').status_code == 200
    assert member.get('/api/session').json()['user']['providers'] == ['google']
    with Session() as db:
        assert db.get(WebSession, digest('old-device')) is None
    assert member.delete('/api/auth/telegram/link').status_code == 404
