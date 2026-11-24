"""Database portability, SQLite safety settings, migrations, backups."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Column, MetaData, Table, Uuid, create_engine, func, insert, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from app.core.config import get_settings
from app.db.maintenance import backup_sqlite, check_integrity
from app.db.models import metadata
from app.db.types import Money, Quantity, UTCDateTime
from tests.conftest import alembic_config


def _scratch_table(engine, name: str) -> Table:
    md = MetaData()
    t = Table(
        name, md,
        Column("id", Uuid, primary_key=True, default=uuid.uuid4),
        Column("amount", Money()),
        Column("qty", Quantity()),
        Column("at", UTCDateTime),
    )
    md.drop_all(engine)
    md.create_all(engine)
    return t


def test_migrations_match_models(engine):
    """The schema produced by Alembic must equal the ORM models (no un-migrated changes)."""
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}),
                                metadata)
    assert diff == []


def test_migration_downgrade_and_upgrade_roundtrip(engine):
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    with engine.connect() as conn:
        tables = set(engine.dialect.get_table_names(conn)) - {"alembic_version"}
    assert tables == set()
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert {"users", "businesses", "memberships"} <= set(engine.dialect.get_table_names(conn))


def test_money_is_exact_and_sums_exactly(engine):
    t = _scratch_table(engine, "scratch_money")
    values = [Decimal("0.1"), Decimal("0.2"), Decimal("1234567890.1234"), Decimal("-0.0001")]
    with engine.begin() as conn:
        for v in values:
            conn.execute(insert(t).values(amount=v, qty=Decimal("1.5")))
    with engine.connect() as conn:
        stored = sorted(conn.scalars(select(t.c.amount)))
        total = conn.scalar(select(func.sum(t.c.amount)))
    assert stored == sorted(values)
    assert all(isinstance(v, Decimal) for v in stored)
    assert Decimal(total) == sum(values) == Decimal("1234567890.4233")


@pytest.mark.parametrize("bad", [0.1, Decimal("1.23456")])
def test_money_rejects_floats_and_excess_precision(engine, bad):
    t = _scratch_table(engine, "scratch_money_bad")
    with pytest.raises(StatementError), engine.begin() as conn:
        conn.execute(insert(t).values(amount=bad))


def test_utc_datetime_roundtrip_and_rejects_naive(engine):
    t = _scratch_table(engine, "scratch_dt")
    ist = timezone(timedelta(hours=5, minutes=30))
    local = datetime(2026, 4, 1, 9, 30, tzinfo=ist)
    with engine.begin() as conn:
        conn.execute(insert(t).values(at=local))
    with engine.connect() as conn:
        got = conn.scalar(select(t.c.at))
    assert got == local and got.tzinfo == UTC
    with pytest.raises(StatementError), engine.begin() as conn:
        conn.execute(insert(t).values(at=datetime(2026, 1, 1)))


def test_foreign_keys_are_enforced(engine):
    from app.modules.businesses.models import Branch

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(Branch.__table__).values(
            id=uuid.uuid4(), business_id=uuid.uuid4(), code="X", name="Orphan", kind="branch",
            is_active=True, country="IN", created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC)))


def test_only_one_owner_possible_at_database_level(engine):
    from app.modules.users.models import User

    def row(email):
        now = datetime.now(UTC)
        return dict(id=uuid.uuid4(), email=email, full_name="x", is_owner=True, status="active",
                    failed_login_count=0, created_at=now, updated_at=now)

    with engine.begin() as conn:
        conn.execute(insert(User.__table__).values(row("a@x.com")))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(User.__table__).values(row("b@x.com")))


# ---------------------------------------------------------------------------- SQLite specifics

sqlite_only = pytest.mark.skipif(not get_settings().is_sqlite, reason="SQLite-specific")


@sqlite_only
def test_sqlite_pragmas(engine):
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() >= 1000


@sqlite_only
def test_sqlite_concurrent_writers_serialize_instead_of_failing(engine):
    t = _scratch_table(engine, "scratch_concurrency")
    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            for _ in range(20):
                with engine.begin() as conn:
                    current = conn.scalar(select(func.count()).select_from(t))
                    conn.execute(insert(t).values(amount=Decimal(current)))
        except Exception as exc:  # pragma: no cover - surfaced by assertion
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    with engine.connect() as conn:
        amounts = sorted(conn.scalars(select(t.c.amount)))
    # BEGIN IMMEDIATE makes read-then-write atomic: every count observed was unique.
    assert amounts == [Decimal(i) for i in range(80)]


@sqlite_only
def test_sqlite_backup_is_consistent_and_verified(engine, tmp_path: Path, owner):
    dest = backup_sqlite(engine, tmp_path)
    assert dest.exists()
    copy = create_engine(f"sqlite:///{dest}")
    with copy.connect() as conn:
        assert conn.execute(text("SELECT email FROM users")).scalar() == "owner@example.com"
    copy.dispose()


def test_integrity_check_passes(engine):
    report = check_integrity(engine)
    assert report.ok, report.problems
