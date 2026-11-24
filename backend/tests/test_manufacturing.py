"""Manufacturing: recipes, planning, production costing, books, QC hold and cancellation."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import TODAY, USER_PASSWORD, Shop
from tests.test_accounting import books_ok
from tests.test_billing import customer, doc, finalize

T = date.fromisoformat(TODAY)
D = Decimal


@pytest.fixture
def mshop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def setup(s: Shop, requires_qc=False):
    herb = s.product("ashwa-root", unit="KG", purchase_price="200")["id"]
    jar = s.product("jar-250", purchase_price="10")["id"]
    churna = s.product("ashwa-churna", selling_price="295", batch_tracking=True)["id"]
    s.opening([{"product_id": herb, "quantity": "50", "unit_cost": "200"}, {"product_id": jar, "quantity": "500", "unit_cost": "10"}])
    r = s.post("/recipes", {"name": "Ashwagandha Churna 250 g", "product_id": churna, "output_quantity": "100", "overhead": "1500",
                            "requires_qc": requires_qc, "shelf_life_days": 730, "lines": [
                                {"product_id": herb, "quantity": "25", "wastage_percent": "4"}, {"product_id": jar, "quantity": "100"}]})
    return herb, jar, churna, r


def test_plan_produce_cost_and_books(mshop):
    herb, jar, churna, r = setup(mshop)
    assert r["product_name"] and [ln["product_name"] for ln in r["lines"]] and D(r["output_quantity"]) == 100
    p = mshop.get(f"/recipes/{r['id']}/plan", quantity="200", warehouse_id=mshop.main)
    mats = {m["product_id"]: m for m in p["materials"]}
    assert D(mats[herb]["required"]) == 52 and D(mats[herb]["shortage"]) == 2  # 25 × 2 × 1.04; only 50 kg
    assert p["can_produce"] is False and D(p["overhead"]) == 3000
    run = mshop.api.c.post(f"{mshop.base}/production", headers=mshop.h, json={
        "recipe_id": r["id"], "quantity": "200", "warehouse_id": mshop.main, "batch_no": "AC-01"})
    assert run.status_code == 409 and run.json()["error"]["code"] == "insufficient_stock"

    run = mshop.post("/production", {"recipe_id": r["id"], "quantity": "100", "warehouse_id": mshop.main,
                                     "batch_no": "AC-01", "mrp": "295"})
    # 26 kg × 200 + 100 jars × 10 = 6,200 materials + 1,500 overhead = 7,700 → 77 a pack
    assert D(run["material_cost"]) == 6200 and D(run["total_cost"]) == 7700 and D(run["unit_cost"]) == 77
    assert run["qc_status"] == "not_required" and run["expiry_date"] == (T + timedelta(days=730)).isoformat()
    stock = {b["product_id"]: D(b["quantity"]) for b in mshop.get("/inventory/balances")["items"]}
    assert stock[herb] == 24 and stock[jar] == 400 and stock[churna] == 100
    detail = mshop.get(f"/production/{run['id']}")
    assert {m["product_id"]: D(m["quantity"]) for m in detail["materials"]} == {herb: 26, jar: 100}
    books_ok(mshop)  # inventory account = stock valuation; overhead absorbed

    # The finished batch sells with its MRP; missing batch number is refused for batch products.
    inv = finalize(mshop, doc(mshop, "sales_invoice", [{"product_id": churna, "quantity": "2"}], party_id=customer(mshop)["id"])["id"])
    assert inv["lines"][0]["batch_no"] == "AC-01" and D(inv["lines"][0]["mrp"]) == 295
    bad = mshop.api.c.post(f"{mshop.base}/production", headers=mshop.h, json={"recipe_id": r["id"], "quantity": "1", "warehouse_id": mshop.main})
    assert bad.status_code == 422 and "batch number" in bad.json()["error"]["message"]


def test_qc_hold_release_and_reject(mshop):
    _, _, churna, r = setup(mshop, requires_qc=True)
    a = mshop.post("/production", {"recipe_id": r["id"], "quantity": "50", "warehouse_id": mshop.main, "batch_no": "QC-A"})
    assert a["qc_status"] == "pending"
    c = customer(mshop)
    d = doc(mshop, "sales_invoice", [{"product_id": churna, "quantity": "1"}], party_id=c["id"])
    out = mshop.api.c.post(f"{mshop.base}/documents/{d['id']}/finalize", headers=mshop.h, json={"payments": []})
    assert out.status_code == 409  # nothing sellable while on hold
    held = mshop.api.c.post(f"{mshop.base}/documents", headers=mshop.h, json={"doc_type": "sales_invoice", "doc_date": TODAY,
                            "party_id": c["id"], "lines": [{"product_id": churna, "batch_no": "QC-A"}]}).json()
    r2 = mshop.api.c.post(f"{mshop.base}/documents/{held['id']}/finalize", headers=mshop.h, json={"payments": []})
    assert r2.status_code == 422 and r2.json()["error"]["code"] == "batch_on_hold"

    assert mshop.post(f"/production/{a['id']}/qc", {"release": True, "notes": "Moisture 4 %, passes"}, expect=200)["qc_status"] == "released"
    finalize(mshop, d["id"])

    b = mshop.post("/production", {"recipe_id": r["id"], "quantity": "10", "warehouse_id": mshop.main, "batch_no": "QC-B"})
    rej = mshop.post(f"/production/{b['id']}/qc", {"release": False, "notes": "Microbial count high"}, expect=200)
    assert rej["qc_status"] == "rejected"
    batches = {x["batch_no"]: D(x["quantity"]) for x in mshop.get("/inventory/balances", include_zero="true")["items"] if x["product_id"] == churna}
    assert batches["QC-B"] == 0
    assert mshop.api.c.post(f"{mshop.base}/production/{b['id']}/qc", headers=mshop.h, json={"release": True}).status_code == 409
    books_ok(mshop)


def test_cancel_and_permissions(api, mshop):
    herb, _, churna, r = setup(mshop)
    run = mshop.post("/production", {"recipe_id": r["id"], "quantity": "20", "warehouse_id": mshop.main, "batch_no": "CX-1"})
    out = mshop.post(f"/production/{run['id']}/cancel", {"reason": "Wrong recipe used"}, expect=200)
    assert out["status"] == "cancelled"
    stock = {b["product_id"]: D(b["quantity"]) for b in mshop.get("/inventory/balances", include_zero="true")["items"]}
    assert stock[herb] == 50 and stock[churna] == 0
    books_ok(mshop)
    assert mshop.api.c.delete(f"{mshop.base}/recipes/{r['id']}", headers=mshop.h).status_code == 422  # has history

    role = mshop.post("/roles", {"name": "Store", "permissions": ["business.view", "stock.view", "stock.edit", "products.view"]})
    m = mshop.post("/members", {"email": "store-mfg@example.com", "full_name": "S", "role_id": role["id"]})
    api.c.post("/api/v1/auth/accept-invite", json={"token": m["invite_token"], "password": USER_PASSWORD})
    h = api.auth("store-mfg@example.com", USER_PASSWORD)
    assert api.c.get(f"{mshop.base}/recipes", headers=h).status_code == 200
    assert api.c.post(f"{mshop.base}/production", headers=h, json={"recipe_id": r["id"], "quantity": "1",
                                                                  "warehouse_id": mshop.main, "batch_no": "Z"}).status_code == 403
    run2 = mshop.post("/production", {"recipe_id": r["id"], "quantity": "10", "warehouse_id": mshop.main, "batch_no": "CX-2"})
    assert "material_cost" in api.c.get(f"{mshop.base}/production/{run2['id']}", headers=h).json()
    assert api.c.get(f"{mshop.base}/production/{run2['id']}", headers=h).json()["material_cost"] is None  # no stock_value.view
