"""Smart reordering: sales rate, on-order quantities, reorder levels and draft purchase orders."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import TODAY, Shop
from tests.test_billing import customer, doc, finalize, invoice

T = date.fromisoformat(TODAY)
D = Decimal


@pytest.fixture
def rshop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def test_suggestions_from_sales_rate_and_open_orders(rshop):
    p = rshop.product("re1", purchase_price="40", selling_price="80")["id"]
    slow = rshop.product("re2", purchase_price="10", selling_price="20", reorder_level="5", reorder_quantity="12")["id"]
    sup = rshop.post("/suppliers", {"name": "Reliable Herbs"})["id"]
    finalize(rshop, doc(rshop, "purchase_invoice", [{"product_id": p, "quantity": "40", "unit_price": "42"},
                                                    {"product_id": slow, "quantity": "6", "unit_price": "10"}],
                        party_id=sup, supplier_invoice_no="RH/1")["id"])
    c = customer(rshop)
    for back in (3, 10, 20):  # 30 units sold in the last 30 days → 1 a day (same month keeps the FY open)
        invoice(rshop, [{"product_id": p, "quantity": "10"}], party_id=c["id"], doc_date=(T - timedelta(days=back)).isoformat())
    invoice(rshop, [{"product_id": slow, "quantity": "2"}], party_id=c["id"])

    rows = {r["product_id"]: r for r in rshop.get("/inventory/reorder")}
    r = rows[p]
    assert D(r["on_hand"]) == 10 and D(r["daily_sales"]) == 1 and r["days_of_stock"] == 10
    assert D(r["suggested_quantity"]) == 27  # 1 × (7 + 30) − 10
    assert r["supplier_name"] == "Reliable Herbs" and D(r["last_price"]) == 42
    assert D(rows[slow]["suggested_quantity"]) == 12  # at its reorder level: the product's reorder quantity

    finalize(rshop, doc(rshop, "purchase_order", [{"product_id": p, "quantity": "5"}], party_id=sup)["id"])
    assert D({r["product_id"]: r for r in rshop.get("/inventory/reorder")}[p]["suggested_quantity"]) == 22
    assert p not in {r["product_id"] for r in rshop.get("/inventory/reorder", lead_days=0, cover_days=5)}


def test_draft_purchase_orders_grouped_by_supplier(rshop):
    a = rshop.product("re3", purchase_price="10")["id"]
    b = rshop.product("re4", purchase_price="20")["id"]
    new = rshop.product("re5", purchase_price="30")["id"]
    s1 = rshop.post("/suppliers", {"name": "Supplier One"})["id"]
    s2 = rshop.post("/suppliers", {"name": "Supplier Two"})["id"]
    finalize(rshop, doc(rshop, "purchase_invoice", [{"product_id": a, "quantity": "1", "unit_price": "11"}],
                        party_id=s1, supplier_invoice_no="S1/1")["id"])
    finalize(rshop, doc(rshop, "purchase_invoice", [{"product_id": b, "quantity": "1", "unit_price": "19"}],
                        party_id=s2, supplier_invoice_no="S2/1")["id"])
    r = rshop.api.c.post(f"{rshop.base}/inventory/reorder", headers=rshop.h, json={"items": [{"product_id": new, "quantity": "3"}]})
    assert r.status_code == 422 and "never been bought" in r.json()["error"]["message"]
    out = rshop.post("/inventory/reorder", {"items": [{"product_id": a, "quantity": "5"}, {"product_id": b, "quantity": "2"},
                                                      {"product_id": new, "quantity": "3", "supplier_id": s1}]})
    by = {o["party_name"]: o for o in out}
    assert by["Supplier One"]["lines"] == 2 and by["Supplier Two"]["lines"] == 1
    po = rshop.get(f"/documents/{by['Supplier One']['id']}")
    assert po["doc_type"] == "purchase_order" and po["status"] == "draft"
    assert [(D(ln["quantity"]), D(ln["unit_price"])) for ln in po["lines"]] == [(5, 11), (3, 30)]
