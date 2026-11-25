"""Pharmacy: salt substitutes, rack, PTR/PTS, loose selling, doctors, prescriptions for scheduled
drugs, the schedule register, doctor-wise sales and the near-expiry discount."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import TODAY, Shop
from tests.test_billing import customer, doc, finalize

T = date.fromisoformat(TODAY)


@pytest.fixture
def ph(api, owner_headers) -> Shop:
    s = Shop(api, owner_headers)
    r = api.c.put(f"{s.base}/modules", headers=owner_headers, json={"modules": {"pharmacy": True}})
    assert r.status_code == 200 and r.json()["enabled"]["pharmacy"] is True
    return s


def stocked(s: Shop, sku: str, qty: str = "100", **kw) -> str:
    p = s.product(sku, **kw)["id"]
    s.opening([{"product_id": p, "quantity": qty, "unit_cost": "1",
                **({"batch_no": "B1", "expiry_date": "2030-01-01"} if kw.get("batch_tracking") else {})}])
    return p


def fails(s: Shop, doc_id: str) -> dict:
    r = s.api.c.post(f"{s.base}/documents/{doc_id}/finalize", headers=s.h, json={"payments": []})
    assert r.status_code == 422, r.text
    return r.json()["error"]


def test_product_fields_search_by_salt_and_substitutes(ph):
    a = ph.product("crocin", composition="Paracetamol   500 mg ", rack="A-3", selling_price="2.50",
                   ptr="1.80", pts="1.60", pack_size="15", pack_label="strip")
    assert (a["composition"], a["rack"], a["pack_size"], a["pack_label"]) == ("Paracetamol 500 mg", "A-3", "15.000", "strip")
    assert a["drug_schedule"] == "none" and a["ptr"] == "1.8000"
    ph.product("dolo", composition="paracetamol 500 MG", selling_price="2.00")
    ph.product("calpol", composition="Paracetamol 650 mg", selling_price="2.20")
    ph.product("other", composition="Cetirizine 10 mg")

    found = ph.get("/products", q="paracetamol")["items"]
    assert {p["sku"] for p in found} == {"crocin", "dolo", "calpol"}
    subs = ph.get(f"/products/{a['id']}/substitutes")
    assert [p["sku"] for p in subs] == ["dolo"]  # same salt and strength only, case-insensitive
    assert ph.get(f"/products/{ph.product('plain')['id']}/substitutes") == []

    bad = ph.api.c.post(f"{ph.base}/products", headers=ph.h, json={
        "name": "X", "sku": "x", "unit_id": ph.units["NOS"], "pack_label": "strip"})
    assert bad.status_code == 422


def test_ptr_and_pts_price_tiers(ph):
    p = stocked(ph, "amox", selling_price="10", wholesale_price="9", ptr="8", pts="7")
    q = stocked(ph, "noptr", selling_price="10", wholesale_price="9")
    c = customer(ph, "Retail Chemist")
    ptr = doc(ph, "sales_invoice", [{"product_id": p}, {"product_id": q}], party_id=c["id"], price_tier="ptr")
    assert [ln["unit_price"] for ln in ptr["lines"]] == ["8.0000", "9.0000"]  # falls back to wholesale
    pts = doc(ph, "sales_invoice", [{"product_id": p}], party_id=c["id"], price_tier="pts")
    assert pts["lines"][0]["unit_price"] == "7.0000" and pts["price_tier"] == "pts"


def test_loose_selling_uses_base_units(ph):
    # Stock and price per tablet; a strip of 10. Selling 1 strip + 3 loose = 13 tablets.
    p = stocked(ph, "tab", qty="100", selling_price="2", pack_size="10", pack_label="strip")
    inv = finalize(ph, doc(ph, "sales_invoice", [{"product_id": p, "quantity": "13"}])["id"],
                   [{"mode": "cash", "amount": "26"}])
    assert inv["lines"][0]["quantity"] == "13.000" and Decimal(inv["grand_total"]) == 26
    assert ph.stock(p)[0] == Decimal(87)


def test_doctors_and_prescription_rules(ph):
    dr = ph.post("/doctors", {"name": "Dr. Mehta", "registration_no": "GMC-1234", "clinic": "Mehta Clinic",
                              "address": "Ring Road, Surat"})
    dup = ph.api.c.post(f"{ph.base}/doctors", headers=ph.h, json={"name": "Dr. M", "registration_no": "GMC-1234"})
    assert dup.status_code == 409
    assert [d["name"] for d in ph.get("/doctors", q="mehta")] == ["Dr. Mehta"]

    h = stocked(ph, "azithro", selling_price="100", drug_schedule="h", composition="Azithromycin 500 mg")
    h1 = stocked(ph, "alpraz", selling_price="50", drug_schedule="h1", batch_tracking=True)
    otc = stocked(ph, "balm", selling_price="40")

    finalize(ph, doc(ph, "sales_invoice", [{"product_id": otc}])["id"], [{"mode": "cash", "amount": "40"}])

    d1 = doc(ph, "sales_invoice", [{"product_id": h}])
    err = fails(ph, d1["id"])
    assert err["code"] == "prescription_required" and "doctor" in err["message"] and "patient" in err["message"]

    ok = doc(ph, "sales_invoice", [{"product_id": h}], doctor_id=dr["id"], patient_name="Ramesh Patel")
    inv = finalize(ph, ok["id"], [{"mode": "cash", "amount": "100"}])
    assert inv["doctor_name"] == "Dr. Mehta" and inv["patient_name"] == "Ramesh Patel"

    # H1 also needs the prescription number, and goes in the register.
    d2 = doc(ph, "sales_invoice", [{"product_id": h1, "quantity": "2"}], doctor_id=dr["id"], patient_name="Sita")
    assert "Rx" in fails(ph, d2["id"])["message"]
    upd = ph.api.c.patch(f"{ph.base}/documents/{d2['id']}", headers=ph.h,
                         json={"rx_number": "RX-77", "patient_address": "Adajan"})
    assert upd.status_code == 200, upd.text
    sold = finalize(ph, d2["id"], [{"mode": "cash", "amount": "100"}])

    reg = ph.get("/reports/schedule-register", date_from=TODAY, date_to=TODAY)["rows"]
    assert len(reg) == 1
    row = reg[0]
    assert (row["number"], row["patient_name"], row["rx_number"], row["doctor_name"], row["doctor_registration"],
            row["product_name"], row["schedule"], row["batch_no"], row["quantity"]) == (
        sold["number"], "Sita", "RX-77", "Dr. Mehta", "GMC-1234", "Alpraz", "h1", "B1", "2.000")
    both = ph.get("/reports/schedule-register", date_from=TODAY, date_to=TODAY, schedule=["h", "h1"])["rows"]
    assert {r["schedule"] for r in both} == {"h", "h1"}

    sales = ph.get("/reports/doctor-sales", date_from=TODAY, date_to=TODAY)["rows"]
    assert len(sales) == 1 and sales[0]["bills"] == 2 and Decimal(sales[0]["total"]) == 200

    # Unknown doctor is refused.
    other = ph.api.c.post(f"{ph.base}/documents", headers=ph.h, json={
        "doc_type": "sales_invoice", "doc_date": TODAY, "lines": [{"product_id": otc}],
        "doctor_id": "00000000-0000-0000-0000-000000000001"})
    assert other.status_code == 422

    # The bill prints the doctor and patient.
    html = ph.api.c.get(f"{ph.base}/documents/{inv['id']}/print", headers=ph.h, params={"format": "html"})
    assert html.status_code == 200 and "Dr. Mehta" in html.text and "Ramesh Patel" in html.text
    thermal = ph.api.c.get(f"{ph.base}/documents/{inv['id']}/print", headers=ph.h, params={"paper": "thermal_80"})
    assert thermal.status_code == 200 and "Dr. Mehta" in thermal.text


def test_near_expiry_discount(ph):
    assert ph.get("/pharmacy/settings")["near_expiry_discount"]["days"] == 0
    p = ph.product("syrup", selling_price="100", batch_tracking=True)["id"]
    ph.opening([{"product_id": p, "quantity": "2", "unit_cost": "50", "batch_no": "SOON",
                 "expiry_date": (T + timedelta(days=20)).isoformat()},
                {"product_id": p, "quantity": "10", "unit_cost": "50", "batch_no": "LATE",
                 "expiry_date": (T + timedelta(days=400)).isoformat()}])

    off = doc(ph, "sales_invoice", [{"product_id": p}])
    assert off["lines"][0]["discount_percent"] == "0.0000"

    r = ph.api.c.put(f"{ph.base}/pharmacy/settings/near-expiry", headers=ph.h, json={"days": 30, "percent": "15"})
    assert r.status_code == 200 and r.json()["near_expiry_discount"]["days"] == 30

    on = doc(ph, "sales_invoice", [{"product_id": p}])
    ln = on["lines"][0]
    assert ln["discount_percent"] == "15.0000" and ln["custom_fields"].get("near_expiry") is True
    inv = finalize(ph, on["id"], [{"mode": "cash", "amount": "85"}])
    assert inv["lines"][0]["batch_no"] == "SOON" and Decimal(inv["grand_total"]) == 85

    # A chosen far-off batch, a line with its own discount, and a line bigger than the first batch: no auto discount.
    lines = doc(ph, "sales_invoice", [{"product_id": p, "batch_no": "LATE"},
                                      {"product_id": p, "discount_percent": "5"},
                                      {"product_id": p, "quantity": "5"}])["lines"]
    assert [x["discount_percent"] for x in lines] == ["0.0000", "5.0000", "0.0000"]

    bad = ph.api.c.put(f"{ph.base}/pharmacy/settings/near-expiry", headers=ph.h, json={"days": 30, "percent": "150"})
    assert bad.status_code == 422


def test_modules_switch_and_settings_merge(api, owner_headers):
    s = Shop(api, owner_headers)
    mods = s.get("/modules")
    assert mods["enabled"]["pharmacy"] is False and mods["enabled"]["manufacturing"] is True
    assert {m["key"] for m in mods["modules"]} >= {"pharmacy", "manufacturing", "payroll", "catalogue"}

    # Pharmacy off: a scheduled drug sells without a prescription, and no near-expiry discount.
    h = stocked(s, "azithro", selling_price="100", drug_schedule="h")
    finalize(s, doc(s, "sales_invoice", [{"product_id": h}])["id"], [{"mode": "cash", "amount": "100"}])
    put = lambda body: api.c.put(f"{s.base}/modules", headers=owner_headers, json=body)  # noqa: E731
    assert put({"modules": {"nonsense": True}}).status_code == 422
    out = put({"modules": {"pharmacy": True, "manufacturing": False}}).json()["enabled"]
    assert out["pharmacy"] and not out["manufacturing"] and out["payroll"]
    assert fails(s, doc(s, "sales_invoice", [{"product_id": h}])["id"])["code"] == "prescription_required"

    # Editing the business profile with other settings keeps the modules (settings merge).
    r = api.c.patch(s.base, headers=owner_headers, json={"settings": {"round_off": False}})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["modules"] == {"pharmacy": True, "manufacturing": False}
    assert r.json()["settings"]["round_off"] is False
    assert s.get("/modules")["enabled"]["manufacturing"] is False

    # Members without business.settings can read but not change modules.
    r = api.c.put(f"{s.base}/modules", headers={}, json={"modules": {"pharmacy": False}})
    assert r.status_code in (401, 403)
