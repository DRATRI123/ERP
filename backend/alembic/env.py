"""Alembic environment. Works identically for SQLite and PostgreSQL.

``render_as_batch`` lets ALTER-style migrations run on SQLite (which cannot ALTER most
constraints) by copying the table; on PostgreSQL it emits normal ALTER statements.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Engine

from app.core.config import get_settings
from app.db.models import metadata
from app.db.session import build_engine

config = context.config


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=metadata, literal_binds=True,
                      render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # supplied programmatically (tests)
        _run(connection)
        return
    settings = get_settings().model_copy(update={"database_url": _url()})
    engine: Engine = build_engine(settings)
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    sqlite = connection.dialect.name == "sqlite"
    if sqlite:
        # Batch migrations rebuild tables (copy, DROP, rename). With foreign keys enforced,
        # dropping a table that other rows reference fails, so enforcement is paused for the
        # run, and the result is verified with foreign_key_check before it is committed.
        # The PRAGMA only works outside a transaction, hence the raw DBAPI connection.
        connection.connection.dbapi_connection.execute("PRAGMA foreign_keys=OFF")
    context.configure(connection=connection, target_metadata=metadata, render_as_batch=True,
                      compare_type=True)
    try:
        with context.begin_transaction():
            context.run_migrations()
            if sqlite:
                broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise RuntimeError(f"Migration left foreign key violations: {broken[:10]}")
    finally:
        if sqlite:
            connection.connection.dbapi_connection.execute("PRAGMA foreign_keys=ON")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
