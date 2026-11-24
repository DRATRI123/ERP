"""Phase 9: SQLite → PostgreSQL migration utility (and the reverse direction), cutover with
maintenance mode.

The copy is tested in whichever direction the suite's database allows: with the default SQLite
suite the target is a scratch PostgreSQL database (``ERP_TEST_PG_ADMIN_URL``, skipped when no
server is reachable); with ``ERP_TEST_DATABASE_URL`` pointing at PostgreSQL, the target is a
scratch SQLite file. Both run in CI.
"""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text, update
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.db import transfer as tx
from app.db.models import metadata
from app.db.session import build_engine, set_engine
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, TODAY, Shop
from tests.test_accounting import fy
from tests.test_billing import customer, invoice, stocked

PG_ADMIN_URL = os.environ.get("ERP_TEST_PG_ADMIN_URL",
                              "postgresql+psycopg://erp:erp_test@localhost:5432/erp_test")


def _engine(url: str) -> Engine:
    return build_engine(get_settings().model_copy(update={"database_url": url}))


@pytest.fixture
def target_url(engine: Engine, tmp_path: Path):
    """An empty database of the *other* dialect, migrated to head by the test."""
    if engine.dialect.name == "postgresql":
        yield f"sqlite:///{tmp_path / 'target.db'}"
        return
    name = f"erp_transfer_{uuid.uuid4().hex[:8]}"
    admin_url = make_url(PG_ADMIN_URL)
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text(f"CREATE DATABASE {name}"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable for the migration test: {exc.__class__.__name__}")
    try:
        yield admin_url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as c:
            c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        admin.dispose()


def _business_data(api, owner_headers) -> tuple[Shop, dict]:
    """Every kind of row the engine must carry: masters, stock with reversals, invoices with
    payments, a cancelled invoice (journal + stock reversals), self-referencing categories and
    money with all four decimal places."""
    s = Shop(api, owner_headers)
    parent = s.post("/categories", {"name": "Ayurveda"})
    s.post("/categories", {"name": "Churna", "parent_id": parent["id"]})
    p = stocked(s, "triphala", qty="250", cost="12.3456", selling_price="99.99")
    q = stocked(s, "chyawanprash", qty="40", cost="180.5", selling_price="349")
    c = customer(s, "Wellness Clinic", email="clinic@example.com")
    paid = invoice(s, [{"product_id": p, "quantity": "7"}, {"product_id": q, "quantity": "2"}],
                   party_id=c["id"], payments=[{"mode": "upi", "amount": "500", "reference": "UTR1"}])
    wrong = invoice(s, [{"product_id": q, "quantity": "1"}], party_id=c["id"])
    s.post(f"/documents/{wrong['id']}/cancel", {"reason": "wrong customer"}, expect=200)
    adj = s.adjust([{"product_id": p, "quantity": "-3", "unit_cost": "12.3456"}], reason="damage")
    s.cancel(adj["id"])
    return s, paid


def _trial_balance(client: TestClient, s: Shop, headers: dict) -> dict:
    f = fy(s)
    r = client.get(f"{s.base}/accounting/reports/trial-balance", headers=headers,
                   params={"date_from": f["start_date"], "date_to": f["end_date"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_full_copy_verifies_and_the_app_runs_on_the_copy(api, owner_headers, engine, target_url, client):
    s, paid = _business_data(api, owner_headers)
    tb_before = _trial_balance(client, s, owner_headers)
    stock_before = s.get("/inventory/balances", include_zero=True)["items"]

    tx.upgrade_target(target_url)
    target = _engine(target_url)
    seen: list[str] = []
    report = tx.transfer(engine, target, batch_size=7, progress=lambda t, n: seen.append(t))
    assert report.ok and report.committed, report.problems
    assert report.total_rows > 100 and len(report.tables) == len(metadata.tables) - (
        1 if "alembic_version" in metadata.tables else 0)
    assert seen.index("businesses") < seen.index("documents") < seen.index("document_lines")
    by_name = {t.name: t for t in report.tables}
    assert by_name["stock_ledger"].rows >= 6 and by_name["journal_entries"].rows >= 4
    assert tx.compare(engine, target) == []

    # Exact values survive the change of storage (scaled integers ↔ NUMERIC, hex ↔ uuid).
    from app.modules.billing.models import Document

    with target.connect() as c:
        d = c.execute(select(Document).where(Document.id == uuid.UUID(paid["id"]))).one()._mapping
        assert d["grand_total"] == Decimal(paid["grand_total"])
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == report.revision
        from app.modules.inventory.models import StockLedgerEntry

        reversed_rows = c.execute(select(func.count()).where(
            StockLedgerEntry.reverses_entry_id.is_not(None))).scalar()
        assert reversed_rows >= 1  # self-references were back-filled

    # Cut over: point the application at the copy and use it.
    set_engine(target)
    try:
        with TestClient(__import__("app.main", fromlist=["create_app"]).create_app()) as c2:
            r = c2.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
            assert r.status_code == 200, r.text
            h = {"Authorization": f"Bearer {r.json()['access_token']}"}
            assert _trial_balance(c2, s, h) == tb_before
            got = c2.get(f"{s.base}/inventory/balances", headers=h, params={"include_zero": True}).json()["items"]
            assert sorted(got, key=lambda x: x["product_id"]) == sorted(stock_before, key=lambda x: x["product_id"])
            # …and keep working: a new invoice on the new database.
            pid = c2.get(f"{s.base}/products", headers=h).json()["items"][0]["id"]
            cid = c2.get(f"{s.base}/customers", headers=h).json()["items"][0]["id"]
            r = c2.post(f"{s.base}/documents", headers=h, json={
                "doc_type": "sales_invoice", "doc_date": TODAY, "party_id": cid, "lines": [{"product_id": pid}]})
            assert r.status_code == 201, r.text
            fin = c2.post(f"{s.base}/documents/{r.json()['id']}/finalize", headers=h, json={"payments": []})
            assert fin.status_code == 200, fin.text
            assert c2.get(f"{s.base}/accounting/reports/integrity", headers=h).json()["ok"] is True
    finally:
        set_engine(engine)
        target.dispose()


def test_dry_run_rolls_back_and_target_must_be_empty(api, owner_headers, engine, target_url):
    _business_data(api, owner_headers)
    tx.upgrade_target(target_url)
    target = _engine(target_url)
    try:
        rehearsal = tx.transfer(engine, target, dry_run=True)
        assert rehearsal.ok and not rehearsal.committed and rehearsal.total_rows > 100
        with target.connect() as c:
            assert c.execute(text("SELECT COUNT(*) FROM businesses")).scalar() == 0

        assert tx.transfer(engine, target).committed
        with pytest.raises(tx.TransferError, match="already contains data"):
            tx.transfer(engine, target)
        with pytest.raises(tx.TransferError, match="same database"):
            tx.transfer(engine, engine)
    finally:
        target.dispose()


def test_refuses_unmigrated_target_or_outdated_source(engine, target_url, monkeypatch, owner):
    target = _engine(target_url)
    try:
        with pytest.raises(tx.TransferError, match="must be created by the migrations"):
            tx.transfer(engine, target)
        tx.upgrade_target(target_url)
        monkeypatch.setattr(tx, "_head", lambda: "9999")
        with pytest.raises(tx.TransferError, match=r"Run `python -m app\.cli migrate`"):
            tx.transfer(engine, target)
    finally:
        target.dispose()


def test_any_difference_aborts_and_writes_nothing(api, owner_headers, engine, target_url, monkeypatch):
    _business_data(api, owner_headers)
    tx.upgrade_target(target_url)
    target = _engine(target_url)
    real = tx.table_checksum

    def corrupt(conn, table, batch=5000):  # simulate a value changed in flight
        out = real(conn, table, batch)
        return out[:-1] + ("0" if out[-1] != "0" else "1") if table.name == "journal_lines" else out
    monkeypatch.setattr(tx, "table_checksum", corrupt)
    try:
        report = tx.transfer(engine, target)
        assert not report.ok and not report.committed
        assert report.problems == ["journal_lines: target data differs from source"]
        with target.connect() as c:
            assert c.execute(text("SELECT COUNT(*) FROM journal_lines")).scalar() == 0
        monkeypatch.setattr(tx, "table_checksum", real)

        # After a real copy, compare() pinpoints later drift.
        assert tx.transfer(engine, target).committed
        from app.modules.parties.models import Party

        with target.begin() as c:
            c.execute(update(Party).values(name="Changed"))
        assert [d.split(":")[0] for d in tx.compare(engine, target)] == ["parties"]
    finally:
        target.dispose()


def test_checksum_is_order_independent_and_type_canonical():
    from datetime import UTC, datetime

    a, b = tx._Checksum(), tx._Checksum()
    rows = [(uuid.UUID(int=1), Decimal("10.5000"), True, {"b": 1, "a": [1]}),
            (uuid.UUID(int=2), Decimal("0.0000"), None, datetime(2026, 4, 1, tzinfo=UTC))]
    for r in rows:
        a.add(r)
    for r in reversed(rows):
        b.add(r)
    assert a.hexdigest() == b.hexdigest()
    c = tx._Checksum()
    c.add((uuid.UUID(int=1), Decimal("10.5"), True, {"a": [1], "b": 1}))
    c.add(rows[1])
    assert c.hexdigest() == a.hexdigest()  # 10.5 == 10.5000; key order in JSON ignored
    d = tx._Checksum()
    d.add((uuid.UUID(int=1), Decimal("10.5001"), True, {"a": [1], "b": 1}))
    d.add(rows[1])
    assert d.hexdigest() != a.hexdigest()


def test_cli_rehearsal_and_maintenance_gate(api, owner_headers, engine, target_url, tmp_path):
    if engine.dialect.name != "sqlite":
        pytest.skip("the CLI migrates *to* PostgreSQL")
    from app.cli import main

    _business_data(api, owner_headers)
    assert main(["migrate-to-postgres", "--target", target_url]) == 1  # maintenance is off
    report_file = tmp_path / "report.json"
    assert main(["migrate-to-postgres", "--target", target_url, "--dry-run", "--report", str(report_file)]) == 0
    rep = json.loads(report_file.read_text())
    assert rep["ok"] and rep["dry_run"] and not rep["committed"] and rep["total_rows"] > 100
    assert all(t["match"] for t in rep["tables"])

    assert main(["maintenance", "on"]) == 0
    assert main(["migrate-to-postgres", "--target", target_url]) == 0
    assert main(["compare-databases", "--source", get_settings().database_url, "--target", target_url]) == 0
    assert main(["maintenance", "off"]) == 0


def test_maintenance_mode_pauses_changes_not_reads(api, owner_headers, client, db):
    biz = api.create_business(owner_headers)
    r = client.put("/api/v1/system/maintenance", headers=owner_headers,
                   json={"enabled": True, "message": "Moving to PostgreSQL, back at 10:30"})
    assert r.status_code == 200 and r.json()["enabled"] is True
    r = client.post("/api/v1/businesses", headers=owner_headers, json={"name": "New", "state_code": "27"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "maintenance"
    assert "10:30" in r.json()["error"]["message"] and r.headers["Retry-After"]
    assert client.patch(f"/api/v1/businesses/{biz['id']}", headers=owner_headers,
                        json={"name": "Renamed"}).status_code == 503
    assert client.get(f"/api/v1/businesses/{biz['id']}", headers=owner_headers).status_code == 200
    assert api.login(OWNER_EMAIL, OWNER_PASSWORD)["access_token"]  # signing in still works

    from app.cli import main

    assert main(["worker", "--once"]) == 0  # paused: does nothing, no heartbeat
    from app.modules.system import monitor

    db.expire_all()
    assert monitor.last_heartbeat(db) is None
    db.rollback()

    assert client.put("/api/v1/system/maintenance", headers=owner_headers,
                      json={"enabled": False}).status_code == 200
    assert client.get("/api/v1/system/maintenance", headers=owner_headers).json()["enabled"] is False
    r = client.post("/api/v1/businesses", headers=owner_headers, json={"name": "New", "state_code": "27"})
    assert r.status_code == 201
