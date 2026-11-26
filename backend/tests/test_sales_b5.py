"""Leads and follow-ups, warranty / AMC and service calls, dispatch with proof of delivery, and
approval of discounts above the limit."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import TODAY, Shop
from tests.test_billing import customer, doc, finalize, stocked

D = Decimal
T = date.fromisoformat(TODAY)
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture
def s(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


# ---------------------------------------------------------------------------- leads


def test_lead_pipeline_follow_ups_and_conversion(s):
    lead = s.post("/leads", {"name": "Ramesh Shah", "company": "Shah Medicals", "phone": "9876500001", "source": "IndiaMART",
                             "expected_value": "50000", "next_follow_up": TODAY})
    assert lead["stage"] == "new" and lead["assigned_to"]
    assert [x["id"] for x in s.get("/leads", due="today")] == [lead["id"]]

    nxt = (T + timedelta(days=3)).isoformat()
    after = s.post(f"/leads/{lead['id']}/activities", {"kind": "call", "note": "Wants rates for 50 boxes", "next_follow_up": nxt})
    assert after["stage"] == "contacted" and after["next_follow_up"] == nxt
    assert s.get("/leads", due="today") == []

    s.api.c.patch(f"{s.base}/leads/{lead['id']}", headers=s.h, json={"stage": "proposal"}).raise_for_status()
    pipe = s.get("/leads/pipeline")
    assert pipe["stages"]["proposal"]["count"] == 1 and D(pipe["stages"]["proposal"]["value"]) == 50000

    other = s.post("/leads", {"name": "Walk-in"})
    r = s.api.c.patch(f"{s.base}/leads/{other['id']}", headers=s.h, json={"stage": "lost"})
    assert r.status_code == 422  # needs a reason
    s.api.c.patch(f"{s.base}/leads/{other['id']}", headers=s.h, json={"stage": "lost", "lost_reason": "Price"}).raise_for_status()

    existing = customer(s, "Shah Medicals Old", phone="9876500001")
    out = s.post(f"/leads/{lead['id']}/convert", {}, expect=200)
    assert out["party_id"] == existing["id"] and out["lead"]["stage"] == "won"  # matched by phone
    one = s.get(f"/leads/{lead['id']}")
    assert [a["kind"] for a in one["activities"]][-1] == "call" and len(one["activities"]) >= 3
    pipe = s.get("/leads/pipeline")
    assert pipe["stages"]["won"]["count"] == 1 and pipe["stages"]["lost"]["count"] == 1 and pipe["win_rate"] == 50

    fresh = s.post("/leads", {"name": "New Person", "city": "Anand"})
    made = s.post(f"/leads/{fresh['id']}/convert", {}, expect=200)
    assert made["party_name"] == "New Person"


# ---------------------------------------------------------------------------- warranty / AMC / service


def test_warranty_on_sale_service_calls_and_amc(api, owner_headers, s):
    assert api.c.put(f"{s.base}/modules", headers=owner_headers, json={"modules": {"service": True}}).status_code == 200
    p = stocked(s, "mixer", qty="3", selling_price="3000", serial_tracking=True, warranty_months=12)
    c = customer(s, "Asha Patel", phone="9800000001")
    inv = finalize(s, doc(s, "sales_invoice", [{"product_id": p, "quantity": "2", "serials": ["mixer-0", "mixer-1"]}],
                          party_id=c["id"])["id"])
    ws = s.get("/service/contracts", q="mixer-")
    assert sorted(w["serial_no"] for w in ws) == ["mixer-0", "mixer-1"]
    assert all(w["kind"] == "warranty" and w["state"] == "active" and w["party_name"] == "Asha Patel" for w in ws)

    call = s.post("/service/tickets", {"serial_no": "mixer-1", "complaint": "Motor not starting"})
    assert call["covered"] is True and call["contract"]["kind"] == "warranty" and call["party_name"] == "Asha Patel"
    r = s.api.c.patch(f"{s.base}/service/tickets/{call['id']}", headers=s.h, json={"status": "resolved"})
    assert r.status_code == 422  # say what was done
    done = s.api.c.patch(f"{s.base}/service/tickets/{call['id']}", headers=s.h,
                         json={"status": "resolved", "resolution": "Replaced carbon brush"}).json()
    assert done["status"] == "resolved" and done["resolved_on"] == TODAY
    walkin = s.post("/service/tickets", {"customer_name": "Unknown", "serial_no": "XYZ", "complaint": "Noise"})
    assert walkin["covered"] is False

    found = s.get("/service/lookup", q="mixer-1")
    assert len(found["contracts"]) == 1 and len(found["tickets"]) == 1

    s.post(f"/documents/{inv['id']}/cancel", {"reason": "wrong bill"}, expect=200)
    assert {w["state"] for w in s.get("/service/contracts", q="mixer-")} == {"cancelled"}

    amc = s.post("/service/contracts", {"kind": "amc", "party_id": c["id"], "description": "RO purifier",
                                       "start_date": (T - timedelta(days=350)).isoformat(), "months": 12, "amount": "2500"})
    assert amc["number"].startswith("AMC/") and amc["days_left"] <= 30
    assert [x["id"] for x in s.get("/service/contracts", ending_within=30, kind="amc")] == [amc["id"]]
    new = s.post(f"/service/contracts/{amc['id']}/renew", {"months": 12, "amount": "2750"})
    assert new["start_date"] == (date.fromisoformat(amc["end_date"]) + timedelta(days=1)).isoformat()
    assert s.get("/service/contracts", ending_within=30, kind="amc") == []  # renewed
    s.post(f"/service/contracts/{amc['id']}/renew", {"months": 12, "amount": "1"}, expect=409)


def test_no_warranty_when_service_module_off(s):
    p = stocked(s, "fan", qty="2", selling_price="1000", warranty_months=6)
    finalize(s, doc(s, "sales_invoice", [{"product_id": p, "quantity": "1"}], party_id=customer(s)["id"])["id"])
    assert s.get("/service/contracts") == []


# ---------------------------------------------------------------------------- dispatch


def test_dispatch_with_proof_of_delivery(api, owner_headers, s):
    p = stocked(s, "box", qty="20", selling_price="100")
    c = customer(s, "Retailer")
    inv = finalize(s, doc(s, "sales_invoice", [{"product_id": p, "quantity": "5"}], party_id=c["id"])["id"])
    assert [d["id"] for d in s.get("/dispatches/to-send")] == [inv["id"]]
    d = s.post("/dispatches", {"document_id": inv["id"], "packages": 2})
    assert d["status"] == "pending" and d["party_name"] == "Retailer"
    s.post("/dispatches", {"document_id": inv["id"]}, expect=409)
    assert s.get("/dispatches/to-send") == []

    patch = lambda body, code=200: s.api.c.patch(f"{s.base}/dispatches/{d['id']}", headers=s.h, json=body)  # noqa: E731
    assert patch({"status": "delivered"}).status_code == 409  # must go out first
    out = patch({"status": "out", "vehicle_no": "gj05ab1234", "driver_name": "Raju"}).json()
    assert out["vehicle_no"] == "GJ05AB1234" and out["dispatched_at"]
    assert patch({"status": "failed"}).status_code == 422  # why?
    assert patch({"status": "failed", "failure_reason": "Shop closed"}).json()["status"] == "failed"
    patch({"status": "out"})
    r = s.api.c.post(f"{s.base}/dispatches/{d['id']}/pod", headers=s.h, files={"file": ("pod.png", PNG, "image/png")},
                     data={"receiver_name": "Mahesh", "latitude": "22.30", "longitude": "70.80"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "delivered" and got["receiver_name"] == "Mahesh" and got["pod_file_id"]
    assert [x["id"] for x in s.get("/dispatches", status="delivered")] == [d["id"]]
    assert s.get("/dispatches", status="open") == []

    sales_h, _ = api.member_session(owner_headers, s.bid, "rep@example.com", "sales_executive")
    r = s.api.c.post(f"{s.base}/dispatches", headers=sales_h, json={"document_id": inv["id"]})
    assert r.status_code == 403


# ---------------------------------------------------------------------------- discount approval


def test_discount_above_limit_needs_a_manager(api, owner_headers, s):
    r = api.c.patch(f"/api/v1/businesses/{s.bid}", headers=owner_headers, json={"settings": {"discount_limit_percent": "10"}})
    assert r.status_code == 200, r.text
    p = stocked(s, "dl", qty="50", selling_price="100")
    q = stocked(s, "sc", qty="50", selling_price="100")
    s.post("/schemes", {"name": "20 % on 10+", "kind": "discount", "product_id": q, "min_quantity": "10", "discount_percent": "20"})
    op_h, _ = api.member_session(owner_headers, s.bid, "cashier@example.com", "billing_operator")
    c = customer(s)

    ok = doc(s, "sales_invoice", [{"product_id": p, "quantity": "1", "discount_percent": "10"},
                                  {"product_id": q, "quantity": "10"}], party_id=c["id"], headers=op_h)
    finalize(s, ok["id"], headers=op_h)  # 10 % is the limit; scheme discounts do not count

    big = doc(s, "sales_invoice", [{"product_id": p, "quantity": "2", "discount_percent": "15"}], party_id=c["id"], headers=op_h)
    r = finalize(s, big["id"], headers=op_h, expect=409)
    assert r["error"]["code"] == "discount_approval_required" and D(r["error"]["details"]["discount_percent"]) == 15
    s.post(f"/documents/{big['id']}/discount-approval", {"reason": "Regular customer"}, headers=op_h)
    mine = s.get("/discount-approvals", headers=op_h)
    assert len(mine) == 1 and mine[0]["code"] is None  # the biller never sees the code
    code = s.get("/discount-approvals")[0]["code"]
    assert code and len(code) == 6
    wrong = "000000" if code != "000000" else "111111"
    r = s.post(f"/documents/{big['id']}/finalize", {"payments": [], "discount_code": wrong}, headers=op_h, expect=422)
    assert r["error"]["details"]["tries_left"] == 4
    done = s.post(f"/documents/{big['id']}/finalize", {"payments": [], "discount_code": code}, headers=op_h, expect=200)
    assert done["status"] == "final"

    third = doc(s, "sales_invoice", [{"product_id": p, "quantity": "1", "discount_percent": "25"}], party_id=c["id"], headers=op_h)
    s.post(f"/documents/{third['id']}/discount-approval", {}, headers=op_h)
    pending = s.get("/discount-approvals")
    assert len(pending) == 1
    s.post(f"/discount-approvals/{pending[0]['id']}/decide", {"approve": True}, headers=op_h, expect=403)
    s.post(f"/discount-approvals/{pending[0]['id']}/decide", {"approve": True}, expect=200)
    finalize(s, third["id"], headers=op_h)

    own = doc(s, "sales_invoice", [{"product_id": p, "quantity": "1", "discount_percent": "40"}], party_id=c["id"])
    finalize(s, own["id"])  # the owner may give any discount


def test_field_rep_role_and_portal_summary(api, owner_headers, s):
    assert api.c.put(f"{s.base}/modules", headers=owner_headers, json={"modules": {"field": True}}).status_code == 200
    rep_h, _ = api.member_session(owner_headers, s.bid, "mr@example.com", "field_rep")
    me = api.c.get("/api/v1/auth/me", headers=rep_h).json()
    assert next(b for b in me["businesses"] if b["business_id"] == s.bid)["role"] == "field_rep"
    p = stocked(s, "fr1", qty="50", selling_price="100")
    # The rep adds a new shop, books an order with 2 free, and collects cash.
    c = s.post("/customers", {"name": "Gupta Medical", "phone": "9811111111"}, headers=rep_h)
    rep_id = me["user"]["id"]
    order = doc(s, "sales_order", [{"product_id": p, "quantity": "10", "free_quantity": "2"}], party_id=c["id"],
                salesperson_id=rep_id, headers=rep_h)
    finalize(s, order["id"], headers=rep_h)
    s.post("/payments", {"direction": "in", "party_id": c["id"], "mode": "cash", "amount": "500"}, headers=rep_h)
    s.post("/field/visits", {"party_id": c["id"], "outcome": "order_payment", "note": "New shop"}, headers=rep_h)
    out = s.get("/field/me", headers=rep_h)
    assert out["today"]["orders"] == 1 and D(out["today"]["collected"]) == 500 and out["today"]["productive"] == 1
    assert out["orders"][0]["party_name"] == "Gupta Medical" and out["collections"][0]["amount"] == "500.0000"
    # The rep cannot reach the office screens: no purchases, accounts, settings or users.
    for path in ("/documents?doc_type=purchase_invoice", "/accounting/accounts", "/members"):
        assert s.api.c.get(f"{s.base}{path}", headers=rep_h).status_code == 403, path
