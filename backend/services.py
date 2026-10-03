from decimal import Decimal
import uuid

import ccxt
import httpx
from sqlalchemy import select, update

from .adapters import collect, dec, SyncError
from .db import Session, User, Snapshot, Source, now
from .security import unseal


def source_view(source, interval=60):
    return {'id': source.id, 'name': source.name, 'kind': source.kind, 'config': source.config,
            'interval_minutes': source.interval_minutes, 'effective_interval': source.interval_minutes or interval,
            'status': source.status, 'error': source.error, 'last_success': source.last_success,
            'last_attempt': source.last_attempt, 'next_sync': source.next_sync,
            'has_credentials': bool(source.credentials)}


def save_snapshot(db, source, holdings, positions=None, warnings=None):
    snapshot = Snapshot(source_id=source.id, holdings=holdings, positions=positions or [], warnings=warnings or [],
                        total_usd=str(sum((dec(h['value_usd']) for h in holdings if h['value_usd'] is not None), Decimal(0))),
                        unpriced=sum(h['value_usd'] is None for h in holdings))
    db.add(snapshot)
    return snapshot


def sync_source(source_id):
    token = uuid.uuid4().hex
    with Session() as db:
        # Durable compare-and-set lease: API and worker cannot claim the same source.
        claimed = db.execute(update(Source).where(Source.id == source_id, Source.active.is_(True),
                                                  Source.lease_until <= now(), Source.next_sync <= now())
                             .values(lease_until=now() + 900, lease_token=token, last_attempt=now(), status='syncing'))
        db.commit()
        if not claimed.rowcount:
            return False
        source = db.get(Source, source_id)
        try:
            credentials = unseal(source.credentials) if source.credentials else {}
            holdings, positions, warnings = collect(source, credentials)
            error = None
        except SyncError as exc:
            error = str(exc)
        except (ccxt.AuthenticationError, ccxt.PermissionDenied):
            error = 'Доступ відхилено. Перевірте API-ключ, права читання та дозволену IP-адресу.'
        except (ccxt.RateLimitExceeded, ccxt.DDoSProtection):
            error = 'Ліміт API. Оновлення буде повторено пізніше.'
        except (ccxt.NetworkError, httpx.HTTPError):
            error = 'Сервіс тимчасово недоступний або відхилив запит. Попередні дані збережено.'
        except Exception:
            # Provider exception strings can contain signed URLs and API keys.
            error = 'Не вдалося обробити відповідь сервісу. Попередні дані збережено.'
        # Lock before final write; expired/reassigned leases may never publish.
        current = db.execute(select(Source).where(Source.id == source_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
        if current.lease_token != token or not current.active:
            return False
        interval = current.interval_minutes or db.get(User, current.user_id).interval_minutes
        if error is None:
            save_snapshot(db, current, holdings, positions, warnings)
            current.last_success, current.failures = now(), 0
            current.status, current.error = 'ok', None
            current.next_sync = now() + interval * 60
        else:
            current.failures += 1
            current.status, current.error = 'error', error
            current.next_sync = now() + min(60 * (2 ** min(current.failures, 8)), interval * 60)
        current.lease_until, current.lease_token = 0, None
        db.commit()
    return error is None


def portfolio(db, user_id):
    interval = db.get(User, user_id).interval_minutes
    sources = db.scalars(select(Source).where(Source.active.is_(True), Source.user_id == user_id).order_by(Source.created_at, Source.name)).all()
    result, holdings, positions, total, unpriced = [], [], [], Decimal(0), 0
    for source in sources:
        row = source_view(source, interval)
        snapshot = db.scalar(select(Snapshot).where(Snapshot.source_id == source.id).order_by(Snapshot.id.desc()).limit(1))
        row.update(total_usd=snapshot.total_usd if snapshot else None, warnings=snapshot.warnings if snapshot else [],
                   unpriced=snapshot.unpriced if snapshot else 0,
                   stale=source.last_success is None or now() - source.last_success > row['effective_interval'] * 60 + 60)
        if snapshot:
            total += dec(snapshot.total_usd)
            unpriced += snapshot.unpriced
            holdings.extend(dict(h, source_id=source.id, source_name=source.name, kind=source.kind,
                                 observed_at=snapshot.taken_at, stale=row['stale']) for h in snapshot.holdings)
            positions.extend(dict(p, source_id=source.id, source_name=source.name,
                                  observed_at=snapshot.taken_at) for p in snapshot.positions)
        result.append(row)
    return {'sources': result, 'holdings': holdings, 'positions': positions, 'total_usd': str(total),
            'locked_usd': str(sum((dec(h['value_usd']) for h in holdings if h.get('locked') and h['value_usd'] is not None), Decimal(0))),
            'locked_count': sum(bool(h.get('locked')) for h in holdings),
            'locked_unpriced': sum(bool(h.get('locked')) and h['value_usd'] is None for h in holdings),
            'unpriced': unpriced, 'incomplete': unpriced > 0 or any(s['last_success'] is None for s in result),
            'interval_minutes': interval, 'as_of': now()}


def history(db, days, user_id):
    start = now() - days * 86400
    states, points = {}, []
    # Seed each source at the left boundary, then replay only events in the range.
    for source_id in db.scalars(select(Source.id).where(Source.user_id == user_id)).all():
        previous = db.scalar(select(Snapshot).where(Snapshot.source_id == source_id, Snapshot.taken_at < start)
                             .order_by(Snapshot.taken_at.desc(), Snapshot.id.desc()).limit(1))
        if previous:
            states[source_id] = (dec(previous.total_usd), previous.unpriced)
    def point(at):
        return {'at': at, 'value_usd': str(sum((v[0] for v in states.values()), Decimal(0))),
                'unpriced': sum(v[1] for v in states.values())}
    if states:
        points.append(point(start))
    for snapshot in db.scalars(select(Snapshot).join(Source, Snapshot.source_id == Source.id).where(Snapshot.taken_at >= start, Source.user_id == user_id).order_by(Snapshot.taken_at, Snapshot.id)):
        states[snapshot.source_id] = (dec(snapshot.total_usd), snapshot.unpriced)
        p = point(snapshot.taken_at)
        if points and points[-1]['at'] == snapshot.taken_at:
            points[-1] = p
        else:
            points.append(p)
    return points
