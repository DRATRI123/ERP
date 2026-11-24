"""Database maintenance: online backups and integrity checks."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, text


@dataclass
class IntegrityReport:
    ok: bool
    dialect: str
    problems: list[str] = field(default_factory=list)


def check_integrity(engine: Engine) -> IntegrityReport:
    """SQLite: ``PRAGMA integrity_check`` + ``foreign_key_check``. PostgreSQL: connectivity
    (PostgreSQL enforces FKs and checksums itself; use ``pg_amcheck`` for deep checks)."""
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            problems = [r[0] for r in conn.exec_driver_sql("PRAGMA integrity_check") if r[0] != "ok"]
            for row in conn.exec_driver_sql("PRAGMA foreign_key_check"):
                problems.append(f"foreign key violation: table={row[0]} rowid={row[1]} parent={row[2]}")
            return IntegrityReport(not problems, "sqlite", problems)
        conn.execute(text("SELECT 1"))
        return IntegrityReport(True, engine.dialect.name)


def backup_sqlite(engine: Engine, dest_dir: Path) -> Path:
    """Consistent online backup using SQLite's backup API (safe while the app is running,
    including with WAL). The copy is integrity-checked before being reported as successful."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Use pg_dump / PITR for PostgreSQL backups")
    src_path = engine.url.database
    if not src_path or src_path == ":memory:":
        raise RuntimeError("In-memory databases cannot be backed up")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_dir / f"{Path(src_path).stem}-{stamp}.db"
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Backup failed integrity check: {result}")
    finally:
        dst.close()
        src.close()
    return dest
