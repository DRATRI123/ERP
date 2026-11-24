"""Engine and session management.

SQLite is configured for safe single-server operation: WAL journal, enforced foreign keys and
a busy timeout. Transactions that may write start with ``BEGIN IMMEDIATE`` so concurrent writers
queue on the busy timeout instead of failing mid-transaction with "database is locked" when a
read lock is upgraded. Read-only work (GET requests) uses a deferred ``BEGIN`` so WAL readers
never block each other or writers. Write intent defaults to True (CLI, workers, tests) and is
switched off per request by the HTTP middleware for safe methods.
"""

from __future__ import annotations

import threading
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

    # SQLite allows one writer at a time. Waiting writers otherwise poll inside SQLite's busy
    # handler with growing sleeps, which is unfair under load: some requests starve until the
    # busy timeout and fail with "database is locked". Writers of this process therefore queue
    # on a lock first (FIFO-ish, no polling) and only then BEGIN IMMEDIATE; SQLite's own
    # locking still arbitrates between processes (API, worker, CLI). The lock is released when
    # the transaction ends; if it cannot be had within the busy timeout the transaction goes
    # ahead anyway and SQLite decides, so a leak can never deadlock writes.
    writer = threading.Lock()
    wait_s = busy_timeout_ms / 1000

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Any) -> None:
        if not sqlite_write_intent.get():
            conn.exec_driver_sql("BEGIN")
            return
        held = writer.acquire(timeout=wait_s)
        try:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        except BaseException:
            if held:
                writer.release()
            raise
        conn.info["erp_writer"] = held

    def _end(conn: Any) -> None:
        if conn.info.pop("erp_writer", False):
            writer.release()

    event.listen(engine, "commit", _end)
    event.listen(engine, "rollback", _end)


def build_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    url = settings.database_url
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False},
                               pool_size=max(settings.db_pool_size, 40), max_overflow=10)
        _configure_sqlite(engine, settings.sqlite_busy_timeout_ms)
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_size=settings.db_pool_size,
                               max_overflow=settings.db_max_overflow,
                               pool_timeout=settings.db_pool_timeout_s)
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


def SessionLocal() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def read_only() -> Iterator[None]:
    """Run the enclosed database work in a deferred (read) transaction on SQLite."""
    token = sqlite_write_intent.set(False)
    try:
        yield
    finally:
        sqlite_write_intent.reset(token)


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
