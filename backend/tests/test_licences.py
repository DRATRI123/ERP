"""Licence register: CRUD, status, printing on invoices, renewal alerts, permissions."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.modules.compliance.service import emit_renewal_alerts
from app.modules.events.models import OutboxEvent
from tests.conftest import TODAY, USER_PASSWORD, Shop
from tests.test_billing import customer, invoice, stocked

T = date.fromisoformat(TODAY)


@pytest.fixture
def lshop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def test_register_status_and_invoice_printing(lshop):
    dl = lshop.post("/licences", {"kind": "drug_retail", "number": "MH-PZ-123456", "issued_by": "FDA Maharashtra",
                                  "expiry_date": (T + timedelta(days=45)).isoformat(), "print_on_invoice": True})
    assert dl["name"] == "Drug licence (retail)" and dl["status"] == "due" and dl["days_left"] == 45
    lshop.post("/licences", {"kind": "fssai", "number": "11524999000123", "print_on_invoice": True})
    old = lshop.post("/licences", {"kind": "trade", "number": "TL/9", "print_on_invoice": True,
                                   "expiry_date": (T - timedelta(days=1)).isoformat()})
    assert old["status"] == "expired"
    s = lshop.get("/licences/summary")
    assert s["total"] == 3 and s["expired"] == 1 and s["due"] == 1 and s["next"]["number"] == "MH-PZ-123456"
    bad = lshop.api.c.post(f"{lshop.base}/licences", headers=lshop.h, json={
        "kind": "fssai", "number": "X", "issue_date": TODAY, "expiry_date": (T - timedelta(days=5)).isoformat()})
    assert bad.status_code == 422

    p = stocked(lshop, "lic1", selling_price="100")
    inv = invoice(lshop, [{"product_id": p}], party_id=customer(lshop)["id"])
    html = lshop.api.c.get(f"{lshop.base}/documents/{inv['id']}/print", headers=lshop.h).text
    assert "D.L. No.</b> MH-PZ-123456" in html and "11524999000123" in html
    assert "TL/9" not in html  # expired licences are not printed


def test_renewal_alerts_once_per_stage_and_reset_on_renewal(lshop, db):
    lic = lshop.post("/licences", {"kind": "ayush_manufacturing", "number": "25D/77",
                                   "expiry_date": (T + timedelta(days=25)).isoformat()})
    assert emit_renewal_alerts(db, today=T) >= 1
    assert emit_renewal_alerts(db, today=T) == 0

    def mine():
        return list(db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == lic["id"])))
    assert [e.payload["days_left"] for e in mine()] == [25]
    db.rollback()
    renewed = lshop.api.c.patch(f"{lshop.base}/licences/{lic['id']}", headers=lshop.h,
                                json={"expiry_date": (T + timedelta(days=20)).isoformat()})
    assert renewed.status_code == 200
    emit_renewal_alerts(db, today=T)
    assert sorted(e.payload["days_left"] for e in mine()) == [20, 25]
    db.rollback()


def test_licence_permissions(api, owner_headers, lshop):
    lic = lshop.post("/licences", {"kind": "gst", "number": "27AAPFU0939F1ZV"})
    role = lshop.post("/roles", {"name": "Viewer", "permissions": ["business.view"]})
    m = lshop.post("/members", {"email": "lic-view@example.com", "full_name": "V", "role_id": role["id"]})
    api.c.post("/api/v1/auth/accept-invite", json={"token": m["invite_token"], "password": USER_PASSWORD})
    h = api.auth("lic-view@example.com", USER_PASSWORD)
    assert len(lshop.get("/licences", headers=h)) == 1
    assert api.c.post(f"{lshop.base}/licences", headers=h, json={"kind": "fssai", "number": "1"}).status_code == 403
    assert api.c.delete(f"{lshop.base}/licences/{lic['id']}", headers=h).status_code == 403
    other = Shop(api, owner_headers, name="Other")
    assert api.c.patch(f"{other.base}/licences/{lic['id']}", headers=owner_headers, json={"number": "x"}).status_code == 404
    assert uuid.UUID(lic["id"])
