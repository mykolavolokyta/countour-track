import hashlib
import hmac
import json
import secrets

from cryptography.fernet import Fernet
from fastapi import HTTPException, Request

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from . import config
from .db import Session, User, WebSession, AuthRateLimit, now


def cipher():
    if not config.ENCRYPTION_KEY:
        raise HTTPException(503, 'Виконайте початкове налаштування сервера.')
    return Fernet(config.ENCRYPTION_KEY.encode())


def seal(value):
    return cipher().encrypt(json.dumps(value).encode()).decode()


def unseal(value):
    return json.loads(cipher().decrypt(value.encode(), ttl=None))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def check_origin(request):
    if request.headers.get('origin') != config.APP_ORIGIN:
        raise HTTPException(403, 'Невірне джерело запиту.')


def rate_limit(request, category, limit=20):
    # Shared DB counter works across API processes. Forwarded headers are not trusted here.
    current = now()
    key = category + ':' + digest(request.client.host if request.client else 'unknown') + ':' + str(current // 300)
    with Session() as db:
        db.execute(delete(AuthRateLimit).where(AuthRateLimit.expires_at <= current))
        result = db.execute(update(AuthRateLimit).where(AuthRateLimit.id == key).values(count=AuthRateLimit.count + 1))
        if not result.rowcount:
            try:
                with db.begin_nested():
                    db.add(AuthRateLimit(id=key, count=1, expires_at=(current // 300 + 1) * 300))
                    db.flush()
            except IntegrityError:
                db.execute(update(AuthRateLimit).where(AuthRateLimit.id == key).values(count=AuthRateLimit.count + 1))
        count = db.get(AuthRateLimit, key).count
        db.commit()
    if count > limit:
        raise HTTPException(429, 'Забагато спроб. Спробуйте через 5 хвилин.')


def create_session(db, user_id, response):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.execute(delete(WebSession).where(WebSession.expires_at <= now()))
    db.add(WebSession(id=digest(token), user_id=user_id, csrf=csrf, expires_at=now()+43200))
    response.set_cookie('portfolio_session', token, httponly=True, secure=config.COOKIE_SECURE,
                        samesite='lax', max_age=43200, path='/')
    return csrf


def read_session(request):
    raw = request.cookies.get('portfolio_session', '')
    if not raw or len(raw) > 256:
        raise HTTPException(401, 'Увійдіть у свій портфель.')
    with Session() as db:
        session = db.get(WebSession, digest(raw))
        user = db.get(User, session.user_id) if session else None
        if not session or session.expires_at <= now() or not user or not user.active:
            raise HTTPException(401, 'Увійдіть у свій портфель.')
        return {'user_id': user.id, 'name': user.name, 'csrf': session.csrf, 'session_id': session.id}


def require_user(request: Request):
    session = read_session(request)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        check_origin(request)
        if not hmac.compare_digest(request.headers.get('x-csrf-token', '').encode(), session['csrf'].encode()):
            raise HTTPException(403, 'Оновіть сторінку та спробуйте ще раз.')
    return session
