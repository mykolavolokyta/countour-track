from contextlib import asynccontextmanager
from decimal import Decimal
import re
import logging
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, delete

from . import config
from .adapters import collect
from .privatbank import normalize_iban
from .db import Session, User, Identity, WebSession, Source, now, init_db
from .security import require_user, seal
from .auth import router as auth_router
from .services import history, portfolio, save_snapshot, source_view


class RedactAuthQuery(logging.Filter):
    def filter(self, record):
        # Uvicorn logs the full URL by default; OAuth codes must not enter access logs.
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            if isinstance(args[2], str) and args[2].startswith('/api/auth/'):
                args[2] = args[2].split('?', 1)[0]
                record.args = tuple(args)
        return True


auth_log_filter = RedactAuthQuery()


@asynccontextmanager
async def lifespan(app):
    access = logging.getLogger('uvicorn.access')
    if auth_log_filter not in access.filters:
        access.addFilter(auth_log_filter)
    init_db()
    yield


app = FastAPI(title='Contour API', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(auth_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's default error embeds submitted inputs, including secrets.
    if any('token_contracts' in e['loc'] for e in exc.errors()):
        return JSONResponse(status_code=422, content={'detail': 'Перевірте контракти токенів: до 20 різних адрес 0x із 40 шістнадцятковими символами, без нульової адреси.'})
    if any('iban' in e['loc'] for e in exc.errors()):
        return JSONResponse(status_code=422, content={'detail': 'Перевірте IBAN: потрібен український рахунок із правильною контрольною сумою.'})
    return JSONResponse(status_code=422, content={'detail': 'Перевірте поля форми: назву, адресу, суму та інтервал.'})


@app.middleware('http')
async def private_responses(request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class SourceInput(Input):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal['binance', 'bybit', 'bsc', 'manual', 'privatbank']
    bank_token: Optional[str] = Field(default=None, min_length=1, max_length=4096, pattern=r'^[\x21-\x7e]+$')
    iban: Optional[str] = Field(default=None, max_length=64)
    asset_category: Literal['cash', 'crypto', 'other'] = 'cash'
    locked: bool = False
    api_key: Optional[str] = Field(default=None, max_length=512)
    api_secret: Optional[str] = Field(default=None, max_length=512)
    address: Optional[str] = Field(default=None, pattern=r'^0x[0-9a-fA-F]{40}$')
    symbol: Optional[str] = Field(default=None, pattern=r'^[A-Z0-9._-]{1,20}$')
    quantity: Optional[Decimal] = Field(default=None, ge=0, max_digits=60, decimal_places=30, allow_inf_nan=False)
    price_usd: Optional[Decimal] = Field(default=None, ge=0, max_digits=30, decimal_places=15, allow_inf_nan=False)
    futures: list[Literal['usdm', 'coinm']] = Field(default_factory=lambda: ['usdm'], max_length=2)
    interval_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    provider: Literal['rpc', 'moralis'] = 'rpc'
    token_contracts: list[str] = Field(default_factory=list, max_length=20)

    @field_validator('iban')
    @classmethod
    def validate_iban(cls, value):
        return normalize_iban(value) if value is not None else None

    @field_validator('token_contracts')
    @classmethod
    def validate_contracts(cls, contracts):
        normalized = [c.strip().lower() for c in contracts]
        if any(not re.fullmatch(r'0x[0-9a-f]{40}', c) or int(c[2:], 16) == 0 for c in normalized):
            raise ValueError('Invalid token contract')
        if len(set(normalized)) != len(normalized):
            raise ValueError('Duplicate token contract')
        return normalized

    @model_validator(mode='after')
    def required_fields(self):
        if self.kind == 'privatbank' and not self.iban:
            raise ValueError('IBAN required')
        if self.kind == 'bsc' and not self.address:
            raise ValueError('Address required')
        if self.kind == 'manual' and (not self.symbol or self.quantity is None):
            raise ValueError('Manual amount required')
        if self.bank_token and self.kind != 'privatbank':
            raise ValueError('Bank credentials require bank source')
        if (self.api_key or self.api_secret) and self.kind not in ('binance', 'bybit'):
            raise ValueError('Exchange credentials require exchange source')
        if bool(self.api_key) != bool(self.api_secret):
            raise ValueError('Both credentials required')
        if len(self.futures) != len(set(self.futures)):
            raise ValueError('Duplicate future accounts')
        return self

    def source_config(self):
        if self.kind == 'privatbank':
            return {'iban': self.iban}
        if self.kind == 'bsc':
            return {'address': self.address.lower(), 'provider': self.provider,
                    'token_contracts': self.token_contracts}
        if self.kind == 'manual':
            return {'symbol': self.symbol, 'quantity': str(self.quantity),
                    'asset_category': self.asset_category, 'locked': self.locked,
                    'price_usd': str(self.price_usd) if self.price_usd is not None else None}
        return {'futures': self.futures} if self.kind == 'binance' else {'account_mode': 'UTA2'}


class SettingsInput(Input):
    interval_minutes: int = Field(ge=5, le=1440)


@app.get('/api/health')
def health():
    return {'ok': True}


@app.get('/api/session')
def session(user=Depends(require_user)):
    with Session() as db:
        identities = list(db.scalars(select(Identity.provider).where(Identity.user_id == user['user_id'])))
    return {'csrf': user['csrf'], 'bsc_ready': bool(config.BSC_RPC_URLS),
            'moralis_ready': bool(config.MORALIS_API_KEY),
            'user': {'id': user['user_id'], 'name': user['name'], 'providers': identities}}



@app.post('/api/logout')
def logout(response: Response, user=Depends(require_user)):
    with Session() as db:
        db.execute(delete(WebSession).where(WebSession.id == user['session_id']))
        db.commit()
    response.delete_cookie('portfolio_session', path='/', secure=config.COOKIE_SECURE, httponly=True, samesite='lax')
    return {'ok': True}


@app.post('/api/logout-all')
def logout_all(response: Response, user=Depends(require_user)):
    with Session() as db:
        db.execute(delete(WebSession).where(WebSession.user_id == user['user_id']))
        db.commit()
    response.delete_cookie('portfolio_session', path='/', secure=config.COOKIE_SECURE, httponly=True, samesite='lax')
    return {'ok': True}


@app.get('/api/portfolio')
def get_portfolio(user=Depends(require_user)):
    with Session() as db:
        return portfolio(db, user['user_id'])


@app.get('/api/history')
def get_history(days: int = Query(default=30, ge=1, le=365), user=Depends(require_user)):
    with Session() as db:
        return history(db, days, user['user_id'])


def apply_input(source, body):
    new_config = body.source_config()
    if source.kind == 'manual' and source.config:
        for field, default in (('asset_category', 'cash'), ('locked', False)):
            if field not in body.model_fields_set:
                new_config[field] = source.config.get(field, default)
    # Older clients don't submit provider fields. Preserve existing coverage on
    # edits rather than silently replacing all indexed tokens with BNB only.
    if source.kind == 'bsc' and source.config:
        if 'provider' not in body.model_fields_set:
            new_config['provider'] = source.config.get('provider', 'moralis')
        if 'token_contracts' not in body.model_fields_set:
            new_config['token_contracts'] = source.config.get('token_contracts', [])
    source.name, source.config, source.interval_minutes = body.name, new_config, body.interval_minutes
    if source.kind in ('binance', 'bybit') and body.api_key:
        source.credentials = seal({'api_key': body.api_key, 'api_secret': body.api_secret})
    if source.kind == 'privatbank' and body.bank_token:
        source.credentials = seal({'bank_token': body.bank_token})
    source.next_sync, source.status, source.error, source.failures = now(), 'pending', None, 0
    source.lease_until, source.lease_token = 0, None


def check_duplicate(db, body, user_id, source_id=None):
    if body.kind == 'privatbank':
        for other in db.scalars(select(Source).where(Source.kind == 'privatbank', Source.user_id == user_id, Source.active.is_(True))):
            if other.id != source_id and other.config.get('iban') == body.iban:
                raise HTTPException(409, 'Цей IBAN уже додано. Дублювання рахунку подвоїть баланс.')
    if body.kind == 'bsc':
        for other in db.scalars(select(Source).where(Source.kind == 'bsc', Source.user_id == user_id, Source.active.is_(True))):
            if other.id != source_id and other.config['address'] == body.address.lower():
                raise HTTPException(409, 'Ця BSC-адреса вже додана.')


def record_manual_change(db, source):
    if source.kind != 'manual':
        return
    db.flush()
    holdings, positions, warnings = collect(source, {})
    save_snapshot(db, source, holdings, positions, warnings)
    source.last_success = now()
    source.status = 'ok'
    interval = source.interval_minutes or db.get(User, source.user_id).interval_minutes
    source.next_sync = now() + interval * 60


@app.post('/api/sources', status_code=201)
def create_source(body: SourceInput, user=Depends(require_user)):
    if body.kind == 'privatbank' and not body.bank_token:
        raise HTTPException(422, 'Додайте повний токен API «Автоклієнт».')
    if body.kind in ('binance', 'bybit') and not body.api_key:
        raise HTTPException(422, 'Додайте API-ключ і секрет лише для читання.')
    with Session() as db:
        check_duplicate(db, body, user['user_id'])
        source = Source(kind=body.kind, user_id=user['user_id'])
        apply_input(source, body)
        db.add(source)
        record_manual_change(db, source)
        db.commit()
        return source_view(source, db.get(User, user['user_id']).interval_minutes)


@app.put('/api/sources/{source_id}')
def edit_source(source_id: str, body: SourceInput, user=Depends(require_user)):
    with Session() as db:
        source = db.scalar(select(Source).where(Source.id == source_id, Source.user_id == user['user_id'], Source.active.is_(True)).with_for_update())
        if not source:
            raise HTTPException(404, 'Джерело не знайдено.')
        if source.kind != body.kind:
            raise HTTPException(422, 'Тип джерела змінити не можна.')
        if source.lease_until > now():
            raise HTTPException(409, 'Триває оновлення. Спробуйте за хвилину.')
        check_duplicate(db, body, user['user_id'], source_id)
        apply_input(source, body)
        record_manual_change(db, source)
        db.commit()
        return source_view(source, db.get(User, user['user_id']).interval_minutes)


@app.post('/api/sources/{source_id}/refresh')
def refresh_source(source_id: str, user=Depends(require_user)):
    with Session() as db:
        source = db.scalar(select(Source).where(Source.id == source_id, Source.user_id == user['user_id'], Source.active.is_(True)).with_for_update())
        if not source:
            raise HTTPException(404, 'Джерело не знайдено.')
        if source.lease_until > now() or (source.last_attempt and now() - source.last_attempt < 60):
            raise HTTPException(429, 'Оновлювати можна раз на хвилину. Спробуйте трохи пізніше.')
        source.next_sync, source.status = now(), 'pending'
        db.commit()
    return {'ok': True}


@app.delete('/api/sources/{source_id}')
def archive_source(source_id: str, user=Depends(require_user)):
    with Session() as db:
        source = db.scalar(select(Source).where(Source.id == source_id, Source.user_id == user['user_id'], Source.active.is_(True)).with_for_update())
        if not source:
            raise HTTPException(404, 'Джерело не знайдено.')
        if source.lease_until > now():
            raise HTTPException(409, 'Зачекайте завершення оновлення.')
        source.active, source.credentials = False, None
        save_snapshot(db, source, [])
        db.commit()
    return {'ok': True}


@app.put('/api/settings')
def update_settings(body: SettingsInput, user=Depends(require_user)):
    with Session() as db:
        db.get(User, user['user_id']).interval_minutes = body.interval_minutes
        for source in db.scalars(select(Source).where(Source.active.is_(True), Source.user_id == user['user_id'], Source.interval_minutes.is_(None))):
            if source.status == 'ok' and source.lease_until <= now():
                source.next_sync = max(now(), (source.last_success or now()) + body.interval_minutes * 60)
        db.commit()
    return {'interval_minutes': body.interval_minutes}
