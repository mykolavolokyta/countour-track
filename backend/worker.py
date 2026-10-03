"""One persistent worker. Start after the API has initialized the database."""
import logging
import signal
import threading

from sqlalchemy import select

from .db import Session, Source, now
from .services import sync_source

stop = threading.Event()


def tick():
    with Session() as db:
        ids = list(db.scalars(select(Source.id).where(Source.active.is_(True), Source.next_sync <= now(),
                                                    Source.lease_until <= now()).order_by(Source.next_sync).limit(20)))
    for source_id in ids:
        if stop.is_set():
            break
        sync_source(source_id)


def run():
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        try:
            tick()
        except Exception:
            logging.error('Worker cycle failed; retrying in 15 seconds (details redacted).')
        stop.wait(15)


if __name__ == '__main__':
    run()
