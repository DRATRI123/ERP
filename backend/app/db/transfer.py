"""Copy the whole ERP database from one engine to another (SQLite → PostgreSQL cutover).

How it stays exact:

* The target schema is built by the **same Alembic migrations** as production (never
  ``create_all``), and the source must be at the latest revision, so both schemas match.
* Rows are read and written through the application's column types: scaled-integer money on
  SQLite becomes ``NUMERIC`` on PostgreSQL, hex UUIDs become ``uuid``, text timestamps become
  ``timestamptz`` — the values the application sees are identical.
* Tables are copied parents-first. Self-referencing columns (reversal entries, sub-categories,
  account tree…) are inserted as NULL and filled in afterwards, so row order never matters.
* The source is read inside one transaction (a consistent snapshot) and everything is written
  in **one target transaction**: the copy either completes and verifies, or leaves nothing.
* Verification happens *before* commit: per-table row counts and an order-independent SHA-256
  checksum over every column of every row, compared between source and target, plus the books
  (trial balance integrity) and stock-ledger checks for every business on the target.

``dry_run`` performs the complete copy and verification and then rolls back: a rehearsal that
proves the cutover will work without changing the target.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Connection,
    Engine,
    MetaData,
    Table,
    bindparam,
    func,
    inspect,
    select,
    text,
)

from app.db.models import metadata as app_metadata
from app.db.session import sqlite_write_intent

SKIP_TABLES = {"alembic_version"}


class TransferError(Exception):
    pass


@dataclass
class TableReport:
    name: str
    rows: int
    source_checksum: str
    target_checksum: str = ""
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.source_checksum == self.target_checksum


@dataclass
class TransferReport:
    source: str
    target: str
    revision: str | None
    dry_run: bool
    tables: list[TableReport] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    seconds: float = 0.0
    committed: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems and all(t.ok for t in self.tables)

    @property
    def total_rows(self) -> int:
        return sum(t.rows for t in self.tables)

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "revision": self.revision,
                "dry_run": self.dry_run, "ok": self.ok, "committed": self.committed,
                "total_rows": self.total_rows, "seconds": round(self.seconds, 2),
                "problems": self.problems,
                "tables": [{"name": t.name, "rows": t.rows, "checksum": t.source_checksum,
                            "match": t.ok, "seconds": round(t.seconds, 3)} for t in self.tables]}


# ---------------------------------------------------------------------------- helpers

def _safe_url(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=True)


def _revision(conn: Connection) -> str | None:
    if not inspect(conn).has_table("alembic_version"):
        return None
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _head() -> str | None:
    from app.modules.system.monitor import head_revision

    return head_revision()


def _canon(value: Any) -> str:
    """Dialect-independent text form of a column value, as the application sees it."""
    if value is None:
        return "\x00"
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, Decimal):
        return format(value.normalize(), "f") if value else "0"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return value.hex
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float):
        return repr(value)
    return str(value)


class _Checksum:
    """Order-independent multiset hash: sum of per-row SHA-256 values mod 2^256. Row order
    differs between databases (collations), so ordering must not matter."""

    MOD = 1 << 256

    def __init__(self) -> None:
        self.total = 0
        self.rows = 0

    def add(self, row: Iterable[Any]) -> None:
        h = hashlib.sha256("\x1f".join(_canon(v) for v in row).encode()).digest()
        self.total = (self.total + int.from_bytes(h, "big")) % self.MOD
        self.rows += 1

    def hexdigest(self) -> str:
        return f"{self.rows}:{self.total:064x}"


def table_checksum(conn: Connection, table: Table, batch: int = 5000) -> str:
    cs = _Checksum()
    cols = list(table.columns)
    # Statement-level option: Connection.execution_options() would change the connection itself.
    result = conn.execute(select(*cols).execution_options(stream_results=True))
    for chunk in result.partitions(batch):
        for row in chunk:
            cs.add(row)
    return cs.hexdigest()


def _self_ref_columns(table: Table) -> list[str]:
    return [fk.parent.name for fk in table.foreign_keys
            if fk.column.table is table and fk.parent.nullable]


def _target_is_empty(conn: Connection, tables: list[Table]) -> list[str]:
    insp = inspect(conn)
    return [t.name for t in tables if insp.has_table(t.name)
            and conn.execute(select(func.count()).select_from(t)).scalar()]


def _reset_sequences(conn: Connection) -> None:
    """PostgreSQL: move any serial/identity sequence past the copied ids (the ERP uses UUIDs,
    but this keeps the utility correct for any integer key added later)."""
    if conn.dialect.name != "postgresql":
        return
    rows = conn.execute(text(
        "SELECT table_name, column_name, pg_get_serial_sequence(table_name, column_name) "
        "FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND pg_get_serial_sequence(table_name, column_name) IS NOT NULL")).all()
    for table, column, seq in rows:
        conn.execute(text(f'SELECT setval(:seq, COALESCE((SELECT MAX("{column}") FROM "{table}"), 0) + 1, false)'),
                     {"seq": seq})


# ---------------------------------------------------------------------------- main entry

def upgrade_target(target_url: str) -> None:
    from alembic import command
    from app.cli import _alembic_config

    cfg = _alembic_config()
    cfg.set_main_option("sqlalchemy.url", target_url.replace("%", "%%"))
    command.upgrade(cfg, "head")


def transfer(source: Engine, target: Engine, *, dry_run: bool = False, batch_size: int = 2000,
             metadata: MetaData | None = None, verify_business_books: bool = True,
             progress: Callable[[str, int], None] | None = None) -> TransferReport:
    """Copy every table from ``source`` to ``target`` (already migrated to head and empty)."""
    md = metadata or app_metadata
    tables = [t for t in md.sorted_tables if t.name not in SKIP_TABLES]
    started = time.perf_counter()
    report = TransferReport(_safe_url(source), _safe_url(target), None, dry_run)

    if source.url == target.url:
        raise TransferError("Source and target are the same database")

    with source.connect() as src, target.connect() as dst:
        # ------------------------------------------------------------ preflight
        src_rev, dst_rev, head = _revision(src), _revision(dst), _head()
        report.revision = src_rev
        if src_rev != head:
            raise TransferError(f"Source schema is at {src_rev}, not the latest ({head}). "
                                "Run `python -m app.cli migrate` on the source first.")
        if dst_rev != head:
            raise TransferError(f"Target schema is at {dst_rev}, not {head}; it must be created by "
                                "the migrations first (the CLI does this for you).")
        missing = [t.name for t in tables if not inspect(src).has_table(t.name)]
        if missing:
            raise TransferError(f"Source is missing tables: {', '.join(missing)}")
        if src.dialect.name == "sqlite":
            bad = [r[0] for r in src.exec_driver_sql("PRAGMA integrity_check") if r[0] != "ok"]
            bad += [f"foreign key violation in {r[0]}" for r in src.exec_driver_sql("PRAGMA foreign_key_check")]
            if bad:
                raise TransferError("Source failed its integrity check: " + "; ".join(bad[:5]))

        src.rollback()  # end the preflight's implicit transactions
        dst.rollback()
        # One read snapshot for the copy and the checksums. On SQLite a deferred BEGIN, so a
        # dry run does not block the running application's writers.
        token = sqlite_write_intent.set(False)
        try:
            src_tx = src.begin()
        finally:
            sqlite_write_intent.reset(token)
        dst_tx = dst.begin()
        try:
            non_empty = _target_is_empty(dst, tables)
            if non_empty:
                raise TransferError("Target already contains data in: " + ", ".join(non_empty[:10])
                                    + ". Use an empty database.")

            # -------------------------------------------------------- copy
            for table in tables:
                t0 = time.perf_counter()
                deferred = _self_ref_columns(table)
                pk = [c.name for c in table.primary_key.columns]
                # The back-fill UPDATE must not fire ``onupdate`` defaults (updated_at), or the
                # copy would differ from the source: write their original values explicitly.
                keep = [c.name for c in table.columns if c.onupdate is not None and c.name not in deferred]
                fill = deferred + keep
                backfill: list[dict[str, Any]] = []
                src_sum = _Checksum()
                result = src.execute(select(*table.columns).execution_options(stream_results=True))
                for chunk in result.partitions(batch_size):
                    rows = []
                    for row in chunk:
                        src_sum.add(row)
                        data = dict(row._mapping)
                        if deferred:
                            refs = {c: data[c] for c in deferred if data[c] is not None}
                            if refs:
                                backfill.append({**{f"pk_{k}": data[k] for k in pk},
                                                 **{f"v_{c}": data.get(c) for c in fill}})
                                for c in refs:
                                    data[c] = None
                        rows.append(data)
                    if rows:
                        dst.execute(table.insert(), rows)
                if backfill:
                    stmt = (table.update()
                            .where(*[table.c[k] == bindparam(f"pk_{k}") for k in pk])
                            .values({c: bindparam(f"v_{c}") for c in fill}))
                    for i in range(0, len(backfill), batch_size):
                        dst.execute(stmt, backfill[i:i + batch_size])
                report.tables.append(TableReport(table.name, src_sum.rows, src_sum.hexdigest(),
                                                 seconds=time.perf_counter() - t0))
                if progress:
                    progress(table.name, src_sum.rows)
            _reset_sequences(dst)

            # -------------------------------------------------------- verify (before commit)
            for tr in report.tables:
                tr.target_checksum = table_checksum(dst, md.tables[tr.name], batch_size)
                if not tr.ok:
                    report.problems.append(f"{tr.name}: target data differs from source")
            if verify_business_books and "businesses" in md.tables:
                report.problems += _verify_books(dst)

            if report.ok and not dry_run:
                dst_tx.commit()
                report.committed = True
            else:
                dst_tx.rollback()
        except BaseException:
            dst_tx.rollback()
            raise
        finally:
            src_tx.rollback()  # read-only
    report.seconds = time.perf_counter() - started
    return report


def _verify_books(conn: Connection) -> list[str]:
    """Double-entry and stock-ledger invariants, checked on the target inside the copy
    transaction (so a dry run checks them too)."""
    from sqlalchemy.orm import Session

    from app.modules.accounting.reports import verify_books
    from app.modules.businesses.models import Business
    from app.modules.inventory.engine import verify_balances

    problems: list[str] = []
    with Session(bind=conn, join_transaction_mode="create_savepoint") as s:
        for b in s.scalars(select(Business)):
            problems += [f"{b.name}: {p}" for p in verify_books(s, b.id)]
            problems += [f"{b.name}: stock {p}" for p in verify_balances(s, b.id)]
        s.rollback()
    return problems


def compare(source: Engine, target: Engine, *, metadata: MetaData | None = None) -> list[str]:
    """After a cutover (or at any time): list tables whose contents differ."""
    md = metadata or app_metadata
    out = []
    with source.connect() as a, target.connect() as b:
        if _revision(a) != _revision(b):
            out.append(f"schema revision differs: {_revision(a)} vs {_revision(b)}")
        for t in md.sorted_tables:
            if t.name in SKIP_TABLES:
                continue
            ca, cb = table_checksum(a, t), table_checksum(b, t)
            if ca != cb:
                out.append(f"{t.name}: {ca.split(':')[0]} vs {cb.split(':')[0]} rows, contents differ")
    return out
