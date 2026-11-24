"""Engine and session management.

SQLite is configured for safe single-server operation: WAL journal, enforced foreign keys and
a busy timeout. Transactions that may write start with ``BEGIN IMMEDIATE`` so concurrent writers
queue on the busy timeout instead of failing mid-transaction with "database is locked" when a
read lock is upgraded. Read-only work (GET requests) uses a deferred ``BEGIN`` so WAL readers
never block each other or writers. Write intent defaults to True (CLI, workers, tests) and is
switched off per request by the HTTP middleware for safe methods.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

import app.db.models  # noqa: F401  - register every model so all foreign keys resolve
from app.core.config import Settings, get_settings

sqlite_write_intent: ContextVar[bool] = ContextVar("sqlite_write_intent", default=True)


def _configure_sqlite(engine: Engine, busy_timeout_ms: int) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: Any, _record: Any) -> None:
        # Take manual control of transactions so we can issue BEGIN IMMEDIATE.
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        if engine.url.database not in (None, "", ":memory:"):
            cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE" if sqlite_write_intent.get() else "BEGIN")


def build_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    url = settings.database_url
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})
        _configure_sqlite(engine, settings.sqlite_busy_timeout_ms)
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)
    return engine


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = build_engine()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def set_engine(engine: Engine) -> None:
    """Replace the global engine (used by tests and the CLI)."""
    global _engine, _SessionLocal
    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def SessionLocal() -> Session:  # noqa: N802
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    """One unit of work: commit on success, roll back on any exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
