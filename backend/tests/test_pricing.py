"""Price lists, schemes (free goods and quantity discounts) and credit control."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import TODAY, USER_PASSWORD, Shop
from tests.test_billing import customer, doc, finalize, invoice, stocked

T = date.fromisoformat(TODAY)
D = Decimal


@pytest.fixture
def pshop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def preview(s: Shop, lines, **kw):
    return s.post("/documents/preview", {"doc_type": "sales_invoice", "doc_date": TODAY, "lines": lines, **kw}, expect=200)


def test_price_list_percent_fixed_and_quantity_breaks(pshop):
    p = stocked(pshop, "pl1", selling_price="200", mrp="250")
    q = stocked(pshop, "pl2", selling_price="100", mrp="120")
    hosp = pshop.post("/price-lists", {"name": "Hospitals", "base": "mrp", "adjust_percent": "-20", "items": [
        {"product_id": q, "price": "80"}, {"product_id": q, "min_quantity": "50", "price": "70"}]})
    assert len(hosp["items"]) == 2
    c = customer(pshop, "City Hospital", price_list_id=hosp["id"])
    lines = preview(pshop, [{"product_id": p, "quantity": "2"}, {"product_id": q, "quantity": "10"},
                            {"product_id": q, "quantity": "60"}, {"product_id": p, "unit_price": "190"}],
                    party_id=c["id"])["lines"]
    assert D(lines[0]["unit_price"]) == D("200.00") and lines[0]["price_includes_tax"] is True  # MRP 250 − 20 %
    assert [D(x["unit_price"]) for x in lines[1:]] == [D(80), D(70), D(190)]  # fixed, quantity break, typed price wins
    assert D(preview(pshop, [{"product_id": p}], party_id=customer(pshop, "Retail")["id"])["lines"][0]["unit_price"]) == 200

    bad = pshop.api.c.post(f"{pshop.base}/price-lists", headers=pshop.h, json={"name": "Hospitals"})
    assert bad.status_code == 422  # duplicate name
    assert pshop.api.c.delete(f"{pshop.base}/price-lists/{hosp['id']}", headers=pshop.h).status_code == 422  # in use
    assert pshop.get("/price-lists")[0]["customers"] == 1


def test_free_goods_and_discount_schemes(pshop):
    p = stocked(pshop, "sch1", selling_price="100", gst_rate="12")
    q = stocked(pshop, "sch2", selling_price="50")
    pshop.post("/schemes", {"name": "10 + 1", "kind": "free_goods", "product_id": p, "min_quantity": "10", "free_quantity": "1"})
    pshop.post("/schemes", {"name": "5 % on 20+", "kind": "discount", "product_id": q, "min_quantity": "20",
                            "discount_percent": "5", "valid_to": (T + timedelta(days=5)).isoformat()})
    pshop.post("/schemes", {"name": "Expired", "kind": "discount", "product_id": q, "min_quantity": "1",
                            "discount_percent": "50", "valid_to": (T - timedelta(days=1)).isoformat()})
    c = customer(pshop, "Stockist")
    inv = invoice(pshop, [{"product_id": p, "quantity": "25"}, {"product_id": q, "quantity": "20"},
                          {"product_id": q, "quantity": "5", "discount_percent": "2"}], party_id=c["id"])
    free = [ln for ln in inv["lines"] if ln["custom_fields"].get("scheme_free")]
    assert len(free) == 1 and D(free[0]["quantity"]) == 2 and D(free[0]["line_total"]) == 0
    assert "FREE (10 + 1)" in free[0]["description"]
    assert D(inv["lines"][1]["discount_percent"]) == 5 and inv["lines"][1]["custom_fields"]["scheme"] == "5 % on 20+"
    assert D(inv["lines"][2]["discount_percent"]) == 2  # typed discount kept; slab not reached anyway
    stock = {b["product_id"]: D(b["quantity"]) for b in pshop.get("/inventory/balances")["items"]}
    assert stock[p] == 100 - 27  # free goods leave stock too

    bad = pshop.api.c.post(f"{pshop.base}/schemes", headers=pshop.h, json={
        "name": "x", "kind": "free_goods", "product_id": p, "min_quantity": "10"})
    assert bad.status_code == 422 and "free quantity" in bad.json()["error"]["message"]


def test_editing_a_draft_rebuilds_free_lines(pshop):
    p = stocked(pshop, "sch3", selling_price="10")
    pshop.post("/schemes", {"name": "6+1", "kind": "free_goods", "product_id": p, "min_quantity": "6", "free_quantity": "1"})
    d = doc(pshop, "sales_invoice", [{"product_id": p, "quantity": "12"}], party_id=customer(pshop)["id"])
    assert sum(D(ln["quantity"]) for ln in d["lines"] if ln["custom_fields"].get("scheme_free")) == 2
    r = pshop.api.c.patch(f"{pshop.base}/documents/{d['id']}", headers=pshop.h,
                          json={"lines": [{"product_id": p, "quantity": "6"}]})
    assert [D(ln["quantity"]) for ln in r.json()["lines"]] == [6, 1]


def test_credit_hold_and_status(api, owner_headers, pshop):
    p = stocked(pshop, "cr1", selling_price="1000")
    c = customer(pshop, "Late Payer", credit_limit="5000", credit_days=15)
    invoice(pshop, [{"product_id": p, "quantity": "2"}], party_id=c["id"],
            doc_date=(T - timedelta(days=40)).isoformat() if T.month == (T - timedelta(days=40)).month else TODAY)
    st = pshop.get(f"/parties/{c['id']}/credit")
    assert D(st["outstanding"]) == 2000 and D(st["available"]) == 3000 and st["credit_hold"] is False

    pshop.api.c.patch(f"{pshop.base}/customers/{c['id']}", headers=pshop.h, json={"credit_hold": True})
    assert pshop.get(f"/parties/{c['id']}/credit")["credit_hold"] is True
    # A billing operator cannot sell on credit to a customer on hold …
    role = pshop.post("/roles", {"name": "Counter", "permissions": [
        "business.view", "invoices.view", "invoices.create", "products.view", "stock.view", "customers.view", "payments.create"]})
    m = pshop.post("/members", {"email": "counter@example.com", "full_name": "Counter", "role_id": role["id"]})
    api.c.post("/api/v1/auth/accept-invite", json={"token": m["invite_token"], "password": USER_PASSWORD})
    h = api.auth("counter@example.com", USER_PASSWORD)
    d = doc(pshop, "sales_invoice", [{"product_id": p}], party_id=c["id"], headers=h)
    r = api.c.post(f"{pshop.base}/documents/{d['id']}/finalize", headers=h, json={"payments": []})
    assert r.status_code == 409 and r.json()["error"]["code"] == "credit_hold"
    # … but can when paid in full.
    finalize(pshop, d["id"], [{"mode": "cash", "amount": d["grand_total"]}], headers=h)
