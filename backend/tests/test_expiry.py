"""Batch and expiry control: first-expiry-first-out, no expired sales, batch MRP, alerts, returns."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.events.models import OutboxEvent
from app.modules.inventory.expiry import emit_expiry_alerts
from tests.conftest import TODAY, Shop
from tests.test_billing import customer, doc, finalize

T = date.fromisoformat(TODAY)


def d(days: int) -> str:
    return (T + timedelta(days=days)).isoformat()


@pytest.fixture
def eshop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def setup_stock(s: Shop) -> tuple[str, str, dict]:
    p = s.product("ashwa-b", purchase_price="60", selling_price="100", mrp="125", gst_rate="12",
                  batch_tracking=True)["id"]
    sup = s.post("/suppliers", {"name": "Herbal Distributor"})["id"]
    bill = finalize(s, doc(s, "purchase_invoice", [
        {"product_id": p, "quantity": "4", "unit_price": "60", "batch_no": "OLD", "expiry_date": d(-10)},
        {"product_id": p, "quantity": "5", "unit_price": "60", "batch_no": "SOON", "expiry_date": d(20), "mrp": "120"},
        {"product_id": p, "quantity": "10", "unit_price": "60", "batch_no": "LATER", "expiry_date": d(200), "mrp": "130"},
    ], party_id=sup, supplier_invoice_no="HD/77")["id"])
    return p, sup, bill


def test_first_expiry_first_out_splits_lines_and_carries_batch_mrp(eshop):
    p, _, _ = setup_stock(eshop)
    avail = eshop.get(f"/products/{p}/batches/available")
    assert [(b["batch_no"], b["quantity"], b["mrp"]) for b in avail] == [
        ("SOON", "5.000", "120.0000"), ("LATER", "10.000", "130.0000")]  # expired batch never offered

    c = customer(eshop, "Wellness Clinic")
    inv = finalize(eshop, doc(eshop, "sales_invoice", [{"product_id": p, "quantity": "8", "discount_amount": "8"}],
                              party_id=c["id"])["id"])
    lines = [(ln["batch_no"], ln["quantity"], ln["mrp"], ln["discount_amount"]) for ln in inv["lines"]]
    assert lines == [("SOON", "5.000", "120.0000", "5.0000"), ("LATER", "3.000", "130.0000", "3.0000")]
    assert [ln["line_no"] for ln in inv["lines"]] == [1, 2]
    assert Decimal(inv["grand_total"]) > 0
    assert [(b["batch_no"], b["quantity"]) for b in eshop.get(f"/products/{p}/batches/available")] == [("LATER", "7.000")]

    # More than the unexpired stock: refused, even though expired stock would cover it.
    r = doc(eshop, "sales_invoice", [{"product_id": p, "quantity": "9"}], party_id=c["id"])
    out = eshop.api.c.post(f"{eshop.base}/documents/{r['id']}/finalize", headers=eshop.h, json={"payments": []})
    assert out.status_code == 409 and "unexpired" in out.json()["error"]["message"]


def test_expired_batch_cannot_be_sold(eshop):
    p, _, _ = setup_stock(eshop)
    c = customer(eshop)
    r = doc(eshop, "sales_invoice", [{"product_id": p, "quantity": "1", "batch_no": "OLD"}], party_id=c["id"])
    out = eshop.api.c.post(f"{eshop.base}/documents/{r['id']}/finalize", headers=eshop.h, json={"payments": []})
    assert out.status_code == 422 and out.json()["error"]["code"] == "batch_expired"


def test_alerts_summary_and_expiry_return(eshop, db):
    _, _, bill = setup_stock(eshop)
    summary = eshop.get("/inventory/expiry-summary")
    assert summary["expired"]["batches"] == 1 and summary["d30"]["batches"] == 1 and summary["d90"]["batches"] == 0

    assert emit_expiry_alerts(db, today=T) >= 2
    evs = {e.payload["batch_no"]: e for e in db.scalars(select(OutboxEvent).where(
        OutboxEvent.event_type.in_(["batch.expiring", "batch.expired"]),
        OutboxEvent.business_id == uuid.UUID(eshop.bid)))}
    assert evs["OLD"].event_type == "batch.expired" and evs["SOON"].event_type == "batch.expiring"
    assert evs["SOON"].payload["days_left"] == 20 and "LATER" not in evs
    assert emit_expiry_alerts(db, today=T) == 0  # each stage announced once
    db.rollback()

    returns = eshop.get("/inventory/expiry-returns")
    assert len(returns) == 1 and returns[0]["number"] == bill["number"]
    assert [(ln["batch_no"], ln["quantity"], ln["expired"]) for ln in returns[0]["lines"]] == [("OLD", "4.000", True)]
    soon = eshop.get("/inventory/expiry-returns", within_days=30)[0]["lines"]
    assert {ln["batch_no"] for ln in soon} == {"OLD", "SOON"}

    draft = eshop.post("/inventory/expiry-returns", {"purchase_invoice_id": bill["id"]})
    debit_note = finalize(eshop, draft["id"])
    assert debit_note["doc_type"] == "purchase_return" and debit_note["lines"][0]["batch_no"] == "OLD"
    assert eshop.get("/inventory/expiry-returns") == []
    assert eshop.get("/inventory/expiry-summary")["expired"]["batches"] == 0


def test_expiry_trigger_in_automation_catalog(eshop):
    types = {t["type"]: t for t in eshop.get("/automation/catalog")["triggers"]}
    assert "days_left" in {f["key"] for f in types["batch.expiring"]["fields"]}
    rule = eshop.post("/automation/rules", {"name": "Expiry", "trigger": "batch.expiring",
                                            "conditions": [{"field": "days_left", "op": "lte", "value": "30"}],
                                            "action": "notify_email", "action_config": {
                                                "to": ["owner@shop.in"], "subject": "{name} batch {batch_no} expires {expiry_date}",
                                                "body": "{quantity} in stock, {days_left} days left."}})
    assert rule["trigger"] == "batch.expiring"
