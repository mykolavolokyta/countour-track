"""OIDC authorization code + PKCE. Provider tokens never reach the frontend or DB."""
import base64
import hashlib
import hmac
import secrets
import threading
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from . import config
from .db import Session, Identity, OAuthState, User, WebSession, now
from .security import check_origin, create_session, digest, rate_limit, read_session, require_user, seal, unseal

router = APIRouter()
PROVIDERS = {
    'google': {
        'authorize': 'https://accounts.google.com/o/oauth2/v2/auth',
        'token': 'https://oauth2.googleapis.com/token',
        'jwks': 'https://www.googleapis.com/oauth2/v3/certs',
        'issuer': ['https://accounts.google.com', 'accounts.google.com'],
    },
    'telegram': {
        'authorize': 'https://oauth.telegram.org/auth',
        'token': 'https://oauth.telegram.org/token',
        'jwks': 'https://oauth.telegram.org/.well-known/jwks.json',
        'issuer': 'https://oauth.telegram.org',
    },
}
key_cache = {}
key_lock = threading.Lock()


def credentials(provider):
    if provider not in PROVIDERS:
        raise HTTPException(404, 'Невідомий спосіб входу.')
    client_id = getattr(config, provider.upper() + '_CLIENT_ID')
    secret = getattr(config, provider.upper() + '_CLIENT_SECRET')
    if not client_id or not secret or not config.ENCRYPTION_KEY:
        raise HTTPException(503, 'Цей спосіб входу ще не налаштовано на сервері.')
    return client_id, secret


def provider_flags():
    return {p: bool(getattr(config, p.upper() + '_CLIENT_ID') and
                    getattr(config, p.upper() + '_CLIENT_SECRET') and config.ENCRYPTION_KEY) for p in PROVIDERS}


@router.get('/api/auth/providers')
def providers():
    return dict(provider_flags(), registration=config.ALLOW_REGISTRATION)


def redirect_uri(provider):
    return config.APP_ORIGIN + '/api/auth/' + provider + '/callback'


def begin(provider, request, response, link_session=None):
    client_id, _ = credentials(provider)
    check_origin(request)
    rate_limit(request, 'oidc-start')
    state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
    with Session() as db:
        db.execute(delete(OAuthState).where(OAuthState.expires_at <= now()))
        db.add(OAuthState(id=digest(state), browser_hash=digest(browser), provider=provider,
                         payload=seal({'nonce': nonce, 'verifier': verifier, 'link_session': link_session}),
                         expires_at=now()+600))
        db.commit()
    response.set_cookie('portfolio_oauth', browser, max_age=600, path='/api/auth',
                        httponly=True, secure=config.COOKIE_SECURE, samesite='lax')
    params = {'client_id': client_id, 'redirect_uri': redirect_uri(provider), 'response_type': 'code',
              'scope': 'openid profile', 'state': state, 'nonce': nonce,
              'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode(),
              'code_challenge_method': 'S256'}
    if provider == 'google':
        params['prompt'] = 'select_account'
    return {'url': PROVIDERS[provider]['authorize'] + '?' + urlencode(params)}


@router.post('/api/auth/{provider}/start')
def login_start(provider: str, request: Request, response: Response):
    return begin(provider, request, response)


@router.post('/api/auth/{provider}/link')
def link_start(provider: str, request: Request, response: Response, user=Depends(require_user)):
    return begin(provider, request, response, user['session_id'])


@router.delete('/api/auth/{provider}/link')
def unlink_provider(provider: str, user=Depends(require_user)):
    if provider not in PROVIDERS:
        raise HTTPException(404, 'Невідомий спосіб входу.')
    with Session() as db:
        db.execute(update(User).where(User.id == user['user_id']).values(interval_minutes=User.interval_minutes))
        identities = list(db.scalars(select(Identity).where(Identity.user_id == user['user_id'])))
        identity = next((i for i in identities if i.provider == provider), None)
        if not identity:
            raise HTTPException(404, 'Спосіб входу не підключено.')
        if len(identities) <= 1:
            raise HTTPException(409, 'Не можна від’єднати останній спосіб входу.')
        db.delete(identity)
        # Revoke other sessions, including any pending link flows bound to them.
        db.execute(delete(WebSession).where(WebSession.user_id == user['user_id'], WebSession.id != user['session_id']))
        db.commit()
    return {'ok': True}


def signing_key(provider, kid):
    # Endpoints are fixed, never taken from a JWT jku/x5u claim.
    with key_lock:
        cached_at, keys = key_cache.get(provider, (0, []))
        found = [k for k in keys if k.get('kid') == kid]
        if cached_at < now()-300 or not found:
            with httpx.Client(timeout=10, follow_redirects=False) as http:
                response = http.get(PROVIDERS[provider]['jwks'])
                response.raise_for_status()
                keys = response.json()['keys']
            if not isinstance(keys, list) or len(keys) > 50:
                raise ValueError('Invalid JWKS')
            key_cache[provider] = (now(), keys)
            found = [k for k in keys if k.get('kid') == kid]
        if len(found) != 1 or found[0].get('kty') != 'RSA' or found[0].get('use', 'sig') != 'sig':
            raise ValueError('Unknown signing key')
        return jwt.PyJWK.from_dict(found[0], algorithm='RS256').key


def exchange_identity(provider, code, payload):
    client_id, secret = credentials(provider)
    form = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri(provider),
            'client_id': client_id, 'code_verifier': payload['verifier']}
    # Telegram requires Basic; Google accepts client credentials in the form.
    auth = httpx.BasicAuth(client_id, secret) if provider == 'telegram' else None
    if provider == 'google':
        form['client_secret'] = secret
    with httpx.Client(timeout=15, follow_redirects=False) as http:
        response = http.post(PROVIDERS[provider]['token'], data=form, auth=auth)
        response.raise_for_status()
        token = response.json()['id_token']
    if not isinstance(token, str) or len(token) > 16384:
        raise ValueError('Invalid token')
    header = jwt.get_unverified_header(token)
    if header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str):
        raise ValueError('Unexpected token algorithm')
    claims = jwt.decode(token, signing_key(provider, header['kid']), algorithms=['RS256'],
                        audience=client_id, issuer=PROVIDERS[provider]['issuer'],
                        options={'require': ['exp', 'iat', 'iss', 'aud', 'sub', 'nonce']}, leeway=30)
    if not isinstance(claims['nonce'], str) or not hmac.compare_digest(claims['nonce'], payload['nonce']):
        raise ValueError('Nonce mismatch')
    if claims.get('azp', client_id) != client_id or (isinstance(claims['aud'], list) and len(claims['aud']) > 1 and 'azp' not in claims):
        raise ValueError('Authorized party mismatch')
    if not isinstance(claims['sub'], str) or not 1 <= len(claims['sub']) <= 255:
        raise ValueError('Invalid subject')
    name = claims.get('name')
    return claims['sub'], name[:120] if isinstance(name, str) and name.strip() else 'Користувач'


@router.get('/api/auth/{provider}/callback')
def callback(provider: str, request: Request):
    response = RedirectResponse(config.APP_ORIGIN + '/', status_code=303)
    try:
        credentials(provider)
        rate_limit(request, 'oidc-callback', 40)
        state = request.query_params.get('state', '')
        browser = request.cookies.get('portfolio_oauth', '')
        if not state or len(state) > 256 or not browser or len(browser) > 256:
            raise ValueError('Missing state')
        with Session() as db:
            flow = db.get(OAuthState, digest(state))
            if not flow or flow.provider != provider or flow.expires_at <= now() or not hmac.compare_digest(flow.browser_hash, digest(browser)):
                raise ValueError('Invalid state')
            payload = unseal(flow.payload)
            # Compare-and-delete consumes the state once, even across API processes.
            consumed = db.execute(delete(OAuthState).where(OAuthState.id == flow.id, OAuthState.expires_at > now()))
            db.commit()
            if consumed.rowcount != 1:
                raise ValueError('State already used')
        if request.query_params.get('error'):
            raise ValueError('Provider declined')
        code = request.query_params.get('code', '')
        if not code or len(code) > 4096:
            raise ValueError('Missing code')
        linking = read_session(request) if payload.get('link_session') else None
        if linking and linking['session_id'] != payload['link_session']:
            raise ValueError('Link session changed')
        subject, name = exchange_identity(provider, code, payload)
        with Session() as db:
            identity = db.scalar(select(Identity).where(Identity.provider == provider, Identity.subject == subject))
            if linking:
                account = db.scalar(select(User).where(User.id == linking['user_id']).with_for_update())
                live_session = db.get(WebSession, linking['session_id'])
                if not account or not account.active or not live_session or live_session.expires_at <= now():
                    raise ValueError('Link session expired')
                if identity and identity.user_id != linking['user_id']:
                    raise HTTPException(409, 'identity_used')
                other = db.scalar(select(Identity).where(Identity.user_id == linking['user_id'], Identity.provider == provider))
                if other and other.subject != subject:
                    raise HTTPException(409, 'identity_used')
                if not identity:
                    db.add(Identity(user_id=linking['user_id'], provider=provider, subject=subject))
                db.commit()
                response.headers['location'] = config.APP_ORIGIN + '/?auth=linked'
            else:
                if identity:
                    user = db.get(User, identity.user_id)
                else:
                    if not config.ALLOW_REGISTRATION:
                        raise HTTPException(403, 'registration_closed')
                    user = User(name=name)
                    db.add(user)
                    db.flush()
                    db.add(Identity(user_id=user.id, provider=provider, subject=subject))
                if not user or not user.active:
                    raise ValueError('Account inactive')
                old = request.cookies.get('portfolio_session')
                if old:
                    db.execute(delete(WebSession).where(WebSession.id == digest(old)))
                create_session(db, user.id, response)
                db.commit()
    except HTTPException as exc:
        reason = exc.detail if exc.detail in ('identity_used', 'registration_closed') else 'failed'
        response = RedirectResponse(config.APP_ORIGIN + '/?auth_error=' + reason, status_code=303)
    except (ValueError, KeyError, TypeError, InvalidToken, httpx.HTTPError, jwt.PyJWTError, IntegrityError):
        # No provider responses, authorization codes, or tokens in error output.
        response = RedirectResponse(config.APP_ORIGIN + '/?auth_error=failed', status_code=303)
    response.delete_cookie('portfolio_oauth', path='/api/auth', secure=config.COOKIE_SECURE,
                           httponly=True, samesite='lax')
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response
