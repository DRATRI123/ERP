"""Test fixtures.

The whole suite runs against SQLite by default. Set ``ERP_TEST_DATABASE_URL`` to run the
*same* tests against PostgreSQL, e.g.::

    ERP_TEST_DATABASE_URL=postgresql+psycopg://erp:pw@localhost/erp_test pytest
"""

from __future__ import annotations

import os
import secrets
import tempfile
from collections.abc import Iterator
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="erp-tests-"))
os.environ["ERP_ENV"] = "test"
os.environ["ERP_SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ["ERP_DATABASE_URL"] = os.environ.get(
    "ERP_TEST_DATABASE_URL", f"sqlite:///{_TMP / 'test.db'}"
)

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from argon2 import PasswordHasher  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import security  # noqa: E402
from app.core.config import BACKEND_DIR, get_settings  # noqa: E402
from app.db.models import metadata  # noqa: E402
from app.db.session import SessionLocal, build_engine, set_engine  # noqa: E402
from app.main import create_app  # noqa: E402

# Production Argon2 parameters are deliberately slow; tests use cheap ones.
security._hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)

OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "Strong-Pass-2026!"
USER_PASSWORD = "Member-Pass-2026!"
# Valid GSTIN (correct mod-36 check digit) for tests.
VALID_GSTIN = "27AAPFU0939F1ZV"


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


def _reset_schema(engine: Engine) -> None:
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    else:
        path = engine.url.database
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    eng = build_engine()
    _reset_schema(eng)
    command.upgrade(alembic_config(), "head")  # schema comes from migrations, not create_all
    set_engine(eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _clean_db(engine: Engine) -> Iterator[None]:
    yield
    with engine.begin() as conn:
        for table in reversed(metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


# ---------------------------------------------------------------------------- helpers


class Api:
    """Thin helper around TestClient for readable tests."""

    def __init__(self, client: TestClient) -> None:
        self.c = client

    def login(self, email: str, password: str) -> dict:
        r = self.c.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return r.json()

    def auth(self, email: str, password: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.login(email, password)['access_token']}"}

    def create_business(self, headers: dict, **kw) -> dict:
        body = {"name": "Test Business", "state_code": "27", **kw}
        r = self.c.post("/api/v1/businesses", json=body, headers=headers)
        assert r.status_code == 201, r.text
        return r.json()

    def role_id(self, headers: dict, business_id: str, code: str) -> str:
        r = self.c.get(f"/api/v1/businesses/{business_id}/roles", headers=headers)
        assert r.status_code == 200, r.text
        return next(x["id"] for x in r.json() if x["code"] == code)

    def add_member(self, headers: dict, business_id: str, email: str, role: str, **kw) -> dict:
        body = {"email": email, "full_name": email.split("@")[0].title(),
                "role_id": self.role_id(headers, business_id, role), **kw}
        r = self.c.post(f"/api/v1/businesses/{business_id}/members", json=body, headers=headers)
        assert r.status_code == 201, r.text
        return r.json()

    def member_session(
        self, headers: dict, business_id: str, email: str, role: str, **kw
    ) -> tuple[dict[str, str], dict]:
        """Invite a new user, accept the invite and return (auth headers, member)."""
        out = self.add_member(headers, business_id, email, role, **kw)
        if out["invite_token"]:
            r = self.c.post("/api/v1/auth/accept-invite",
                            json={"token": out["invite_token"], "password": USER_PASSWORD})
            assert r.status_code == 204, r.text
        return self.auth(email, USER_PASSWORD), out["member"]


@pytest.fixture
def api(client: TestClient) -> Api:
    return Api(client)


@pytest.fixture
def owner(engine: Engine):
    from app.modules.users.service import create_owner

    s = SessionLocal()
    try:
        u = create_owner(s, email=OWNER_EMAIL, full_name="Owner", password=OWNER_PASSWORD)
        s.commit()
        return u
    finally:
        s.close()


@pytest.fixture
def owner_headers(api: Api, owner) -> dict[str, str]:
    return api.auth(OWNER_EMAIL, OWNER_PASSWORD)
