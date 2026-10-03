import time
import uuid

from sqlalchemy import Boolean, Column, Integer, JSON, String, Text, ForeignKey, create_engine, event, inspect, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATABASE_URL

engine = create_engine(DATABASE_URL, **({'connect_args': {'check_same_thread': False, 'timeout': 30}} if DATABASE_URL.startswith('sqlite') else {'pool_pre_ping': True}))
if DATABASE_URL.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def sqlite_setup(connection, _):
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA foreign_keys=ON')

Session = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


def now():
    return int(time.time())


class User(Base):
    __tablename__ = 'users'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(120), nullable=False, default='Користувач')
    active = Column(Boolean, nullable=False, default=True)
    interval_minutes = Column(Integer, nullable=False, default=60)
    created_at = Column(Integer, nullable=False, default=now)


class Identity(Base):
    __tablename__ = 'identities'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey('users.id'), nullable=False, index=True)
    provider = Column(String, nullable=False)
    subject = Column(String(255), nullable=False)
    __table_args__ = (UniqueConstraint('provider', 'subject'), UniqueConstraint('user_id', 'provider'))


class WebSession(Base):
    __tablename__ = 'web_sessions'
    id = Column(String(64), primary_key=True)  # SHA-256 of the random browser token
    user_id = Column(String, ForeignKey('users.id'), nullable=False, index=True)
    csrf = Column(String, nullable=False)
    expires_at = Column(Integer, nullable=False, index=True)


class OAuthState(Base):
    __tablename__ = 'oauth_states'
    id = Column(String(64), primary_key=True)
    browser_hash = Column(String(64), nullable=False)
    provider = Column(String, nullable=False)
    payload = Column(Text, nullable=False)  # encrypted nonce, PKCE verifier, link session
    expires_at = Column(Integer, nullable=False, index=True)


class AuthRateLimit(Base):
    __tablename__ = 'auth_rate_limits'
    id = Column(String, primary_key=True)
    count = Column(Integer, nullable=False, default=0)
    expires_at = Column(Integer, nullable=False, index=True)


class SchemaVersion(Base):
    __tablename__ = 'schema_versions'
    version = Column(Integer, primary_key=True)


class Source(Base):
    __tablename__ = 'sources'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey('users.id'), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    kind = Column(String, nullable=False)
    config = Column(JSON, nullable=False, default=dict)
    credentials = Column(Text, nullable=True)
    interval_minutes = Column(Integer, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    status = Column(String, nullable=False, default='pending')
    error = Column(String, nullable=True)
    failures = Column(Integer, nullable=False, default=0)
    last_success = Column(Integer, nullable=True)
    last_attempt = Column(Integer, nullable=True)
    next_sync = Column(Integer, nullable=False, default=now, index=True)
    lease_until = Column(Integer, nullable=False, default=0)
    lease_token = Column(String, nullable=True)
    created_at = Column(Integer, nullable=False, default=now)


class Snapshot(Base):
    __tablename__ = 'snapshots'
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(String, ForeignKey('sources.id'), nullable=False, index=True)
    taken_at = Column(Integer, nullable=False, default=now, index=True)
    holdings = Column(JSON, nullable=False)
    positions = Column(JSON, nullable=False, default=list)
    warnings = Column(JSON, nullable=False, default=list)
    total_usd = Column(String, nullable=False)
    unpriced = Column(Integer, nullable=False, default=0)


def init_db():
    schema = inspect(engine)
    if schema.has_table('sources'):
        columns = {c['name'] for c in schema.get_columns('sources')}
        if 'user_id' not in columns or schema.has_table('settings') or not schema.has_table('schema_versions'):
            raise RuntimeError('Unsupported database schema. Configure a new empty DATABASE_URL; see README.md.')
        with Session() as db:
            versions = [v.version for v in db.query(SchemaVersion).all()]
            if versions != [2]:
                raise RuntimeError('Unsupported database version. This release requires a fresh multi-user database.')
    Base.metadata.create_all(engine)
    with Session() as db:
        if not db.get(SchemaVersion, 2):
            db.add(SchemaVersion(version=2))
        db.commit()
