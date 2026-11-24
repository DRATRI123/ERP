"""Test fixtures.

The whole suite runs against SQLite by default. Set ``ERP_TEST_DATABASE_URL`` to run the
*same* tests against PostgreSQL, e.g.::

    ERP_TEST_DATABASE_URL=postgresql+psycopg://erp:pw@localhost/erp_test pytest
"""

from __future__ import annotations

import os
import secrets
import tempfile
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="erp-tests-"))
os.environ["ERP_ENV"] = "test"
os.environ["ERP_SECRET_KEY"] = secrets.token_urlsafe(48)
if Path("/opt/pw-browsers/chromium").exists():  # PDF rendering in this environment
    os.environ.setdefault("ERP_PDF_CHROMIUM_PATH", "/opt/pw-browsers/chromium")
    os.environ.setdefault("ERP_PDF_NO_SANDBOX", "true")
os.environ["ERP_ENCRYPTION_KEYS"] = '["%s"]' % __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()
os.environ["ERP_PUBLIC_BASE_URL"] = "https://erp.test"
os.environ["ERP_BACKUP_KEY"] = __import__("base64").urlsafe_b64encode(os.urandom(32)).decode()
os.environ["ERP_BACKUP_DIR"] = str(_TMP / "backups")
os.environ["ERP_MIN_FREE_DISK_MB"] = "1"
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
    from app.core.ratelimit import limiter
    from app.modules.system.domains import host_cache

    from app.modules.system.maintenance import cache as maintenance_cache

    limiter.reset()
    host_cache.invalidate()
    maintenance_cache.invalidate()
    with engine.begin() as conn:
        # Self-referencing rows (reversal entries, sub-categories) are unlinked first; SQLite
        # checks foreign keys row by row while a single DELETE runs.
        for table in metadata.sorted_tables:
            for fk in table.foreign_keys:
                if fk.column.table is table and fk.parent.nullable:
                    conn.execute(table.update().values({fk.parent.name: None}))
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


# ---------------------------------------------------------------------------- Phase 2 helpers


class Shop:
    """One business with products and warehouses, driven through the HTTP API."""

    def __init__(self, api: Api, headers: dict[str, str], valuation: str = "weighted_average",
                 **business):
        self.api, self.h = api, headers
        self.biz = api.create_business(headers, **{"name": f"Shop {valuation}",
                                                   "inventory_valuation": valuation, **business})
        self.bid = self.biz["id"]
        self.base = f"/api/v1/businesses/{self.bid}"
        self.units = {u["code"]: u["id"] for u in self.get("/units")}
        self.main = next(w for w in self.get("/warehouses") if w["code"] == "MAIN")["id"]
        self.ho = self.get("/branches")[0]["id"]

    def get(self, path: str, headers: dict | None = None, **params):
        r = self.api.c.get(self.base + path, headers=headers or self.h, params=params)
        assert r.status_code == 200, r.text
        return r.json()

    def post(self, path: str, body: dict, expect: int = 201, headers: dict | None = None):
        r = self.api.c.post(self.base + path, json=body, headers=headers or self.h)
        assert r.status_code == expect, r.text
        return r.json()

    def product(self, sku: str, unit: str = "NOS", **kw) -> dict:
        return self.post("/products", {"name": sku.title(), "sku": sku,
                                       "unit_id": self.units[unit], **kw})

    def warehouse(self, code: str, branch_id: str | None = None) -> str:
        return self.post("/warehouses", {"code": code, "name": code,
                                         "branch_id": branch_id or self.ho})["id"]

    def opening(self, lines: list[dict], warehouse: str | None = None, **kw):
        return self.post("/inventory/opening-stock", {
            "warehouse_id": warehouse or self.main, "doc_date": TODAY, "lines": lines, **kw})

    def adjust(self, lines: list[dict], warehouse: str | None = None, reason: str = "correction",
               expect: int = 201, headers: dict | None = None, **kw):
        return self.post("/inventory/adjustments", {
            "warehouse_id": warehouse or self.main, "doc_date": TODAY, "reason": reason,
            "lines": lines, **kw}, expect=expect, headers=headers)

    def transfer(self, src: str, dst: str, lines: list[dict], expect: int = 201):
        return self.post("/inventory/transfers", {
            "from_warehouse_id": src, "to_warehouse_id": dst, "doc_date": TODAY,
            "lines": lines}, expect=expect)

    def cancel(self, doc_id: str, expect: int = 200):
        return self.post(f"/inventory/documents/{doc_id}/cancel", {"reason": "test cancel"},
                         expect=expect)

    def stock(self, product_id: str, warehouse: str | None = None) -> tuple[Decimal, Decimal]:
        """(quantity, value) summed over batches."""
        rows = self.get("/inventory/balances", product_id=product_id,
                        warehouse_id=warehouse or self.main, include_zero=True)["items"]
        return (sum((Decimal(r["quantity"]) for r in rows), Decimal(0)),
                sum((Decimal(r["value"]) for r in rows), Decimal(0)))

    def assert_consistent(self, db) -> None:
        from app.modules.inventory.engine import verify_balances

        db.expire_all()
        assert verify_balances(db, uuid.UUID(self.bid)) == []


TODAY = date.today().isoformat()


@pytest.fixture
def shop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


@pytest.fixture
def fifo_shop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers, valuation="fifo")
