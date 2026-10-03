import os
from pathlib import Path
import tempfile

os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(tempfile.mkdtemp(prefix='contour-tests-')) / 'test.db')

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend import config
from backend.db import Base, Session, User, Identity, WebSession, engine, init_db, now
from backend.main import app
from backend.security import digest


@pytest.fixture(autouse=True)
def database(monkeypatch):
    Base.metadata.drop_all(engine)
    init_db()
    monkeypatch.setattr(config, 'ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setattr(config, 'APP_ORIGIN', 'http://localhost:3000')
    monkeypatch.setattr(config, 'COOKIE_SECURE', False)
    monkeypatch.setattr(config, 'ALLOW_REGISTRATION', True)
    for provider in ('GOOGLE', 'TELEGRAM'):
        monkeypatch.setattr(config, provider + '_CLIENT_ID', '')
        monkeypatch.setattr(config, provider + '_CLIENT_SECRET', '')


@pytest.fixture
def client():
    with TestClient(app) as test:
        yield test


@pytest.fixture
def member(client):
    with Session() as db:
        db.add(User(id='test-user', name='Test User'))
        db.flush()
        db.add(Identity(user_id='test-user', provider='google', subject='member-google'))
        db.add(WebSession(id=digest('test-session'), user_id='test-user',
                          csrf='test-csrf', expires_at=now()+3600))
        db.commit()
    client.cookies.set('portfolio_session', 'test-session')
    client.headers.update({'origin': config.APP_ORIGIN, 'x-csrf-token': 'test-csrf'})
    return client
