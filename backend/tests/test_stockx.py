"""Supplier bill import, damage / breakage claims and loyalty points."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal as D

import pytest
from openpyxl import Workbook

from app.modules.stockx.purchase_import import detect_columns, parse_expiry
from tests.conftest import Shop
from tests.test_accounting import bal, books_ok
from tests.test_billing import customer, doc, finalize


@pytest.fixture
def sx(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def upload(s: Shop, party_id: str, name: str, data: bytes, expect: int = 200) -> dict:
    r = s.api.c.post(f"{s.base}/purchases/import/preview", headers=s.h, data={"party_id": party_id},
                     files={"file": (name, data, "text/csv" if name.endswith(".csv") else "application/octet-stream")})
    assert r.status_code == expect, r.text
    return r.json()


# ---------------------------------------------------------------------------- import


def test_parse_helpers():
    assert parse_expiry("03/27") == date(2027, 3, 31)
    assert parse_expiry("12/2026") == date(2026, 12, 31)
    assert parse_expiry("15-08-2027") == date(2027, 8, 15)
    assert parse_expiry("2027-01-31") == date(2027, 1, 31)
    assert parse_expiry("Feb-28") == date(2028, 2, 29)
    assert parse_expiry("soon") is None
    cols = detect_columns(["Sr", "Item Description", "Batch No.", "Exp. Date", "Qty", "Sch Qty", "PTR", "CD %", "GST %", "M.R.P."])
    assert cols == {"name": "Item Description", "qty": "Qty", "free": "Sch Qty", "rate": "PTR", "disc": "CD %",
                    "gst": "GST %", "batch": "Batch No.", "expiry": "Exp. Date", "mrp": "M.R.P."}


def test_supplier_bill_import_matches_and_remembers(sx):
    ashwa = sx.product("ASH-250", name="Ashwagandha Churna 250 g", purchase_price="80", gst_rate="12", batch_tracking=True)
    tri = sx.product("TRI-100", name="Triphala Tablets", purchase_price="60", gst_rate="12")
    neem = sx.product("NEEM-60", name="Neem Tablets 60", purchase_price="50", gst_rate="12")
    sup = sx.post("/suppliers", {"name": "Herbal Distributors"})
    csv = (b"Item Name,Item Code,Batch No,Exp,Qty,Free,Rate,Disc %,GST %,MRP\n"
           b"Ashwagandha Churna 250 g,,AC-77,03/27,10,1,82.50,5,12,150\n"
           b"Triphala Tab 100's,TRI-100,T-1,,24,,58,,12,\n"
           b"NEEM TAB (60),,N-9,08/2027,12,2,49,,12,\n"
           b"Total,,,,46,,,,,\n")
    pv = upload(sx, sup["id"], "bill.csv", csv)
    assert pv["total"] == 3 and pv["matched"] == 2
    rows = {r["name"]: r for r in pv["rows"]}
    a = rows["Ashwagandha Churna 250 g"]
    assert (a["product_id"], a["match"], a["quantity"], a["free"], a["rate"], a["expiry_date"], a["batch_tracking"]) == (
        ashwa["id"], "name", "10.000", "1.000", "82.5000", "2027-03-31", True)
    assert rows["Triphala Tab 100's"]["match"] == "code"
    n = rows["NEEM TAB (60)"]
    assert n["product_id"] is None and n["suggestions"][0]["id"] == neem["id"]

    body_rows = []
    for r in pv["rows"]:
        body_rows.append({"product_id": r["product_id"] or neem["id"], "quantity": r["quantity"], "free": r["free"],
                          "rate": r["rate"], "discount_percent": r["discount_percent"], "gst_rate": r["gst_rate"],
                          "batch_no": r["batch_no"], "expiry_date": r["expiry_date"], "mrp": r["mrp"],
                          "supplier_text": r["supplier_text"], "supplier_key": r["supplier_key"]})
    draft = sx.post("/purchases/import", {"party_id": sup["id"], "supplier_invoice_no": "HD/901", "rows": body_rows})
    assert draft["doc_type"] == "purchase_invoice" and draft["status"] == "draft"
    lines = [(ln["product_id"], ln["quantity"], ln["unit_price"], ln["batch_no"]) for ln in draft["lines"]]
    assert (ashwa["id"], "10.000", "82.5000", "AC-77") in lines and (ashwa["id"], "1.000", "0.0000", "AC-77") in lines
    assert len(lines) == 5  # 3 items + 2 free lines
    assert finalize(sx, draft["id"])["number"]
    assert sx.stock(ashwa["id"])[0] == D(11)

    # Next bill from the same supplier: the odd name is now remembered.
    again = upload(sx, sup["id"], "bill.csv", csv)
    assert {r["name"]: r["match"] for r in again["rows"]}["NEEM TAB (60)"] == "remembered"
    names = sx.get(f"/suppliers/{sup['id']}/item-names")
    assert {m["supplier_text"] for m in names} >= {"NEEM TAB (60)"}
    gone = next(m for m in names if m["supplier_text"] == "NEEM TAB (60)")
    assert sx.api.c.delete(f"{sx.base}/suppliers/{sup['id']}/item-names/{gone['id']}", headers=sx.h).status_code == 204

    # Excel works too; a file without a quantity column is refused with a clear message.
    wb = Workbook()
    ws = wb.active
    ws.append(["Product", "Quantity", "Price"])
    ws.append(["Triphala Tablets", 5, 60])
    buf = io.BytesIO()
    wb.save(buf)
    x = upload(sx, sup["id"], "bill.xlsx", buf.getvalue())
    assert x["rows"][0]["product_id"] == tri["id"] and x["rows"][0]["quantity"] == "5.000"
    err = upload(sx, sup["id"], "bad.csv", b"Item Name,Rate\nX,1\n", expect=422)
    assert "Qty" in err["error"]["message"]


# ---------------------------------------------------------------------------- damage claims


def test_damage_claim_write_off_and_settlement(sx):
    p = sx.product("OIL-100", name="Mahanarayan Oil 100 ml", purchase_price="50")["id"]
    sx.opening([{"product_id": p, "quantity": "10", "unit_cost": "50"}])
    sup = sx.post("/suppliers", {"name": "Oil Supplier"})
    c = sx.post("/damage-claims", {"product_id": p, "warehouse_id": sx.main, "quantity": "2", "reason": "leakage",
                                   "party_id": sup["id"], "notes": "Caps broken in transit"})
    assert (c["status"], c["cost_value"], c["claim_amount"], c["party_name"]) == ("open", "100.0000", "100.0000", "Oil Supplier")
    assert c["number"].startswith("DMG/")
    assert sx.stock(p)[0] == D(8)
    loss_before = bal(sx, "stock_adjustment")
    assert loss_before == D(100)

    settled = sx.post(f"/damage-claims/{c['id']}/settle", {"amount": "80", "reference": "CN-55"}, expect=200)
    assert (settled["status"], settled["settled_amount"]) == ("settled", "80.0000")
    assert bal(sx, "stock_adjustment") == D(20) and bal(sx, "ap") == D(80)  # supplier owes us / we owe less
    r = sx.api.c.post(f"{sx.base}/damage-claims/{c['id']}/settle", headers=sx.h, json={"amount": "1"})
    assert r.status_code == 409

    w = sx.post("/damage-claims", {"product_id": p, "warehouse_id": sx.main, "quantity": "1", "reason": "breakage"})
    assert (w["status"], w["claim_amount"]) == ("written_off", "0.0000")
    rj = sx.post("/damage-claims", {"product_id": p, "warehouse_id": sx.main, "quantity": "1", "reason": "transit",
                                    "party_id": sup["id"], "claim_amount": "60"})
    assert sx.post(f"/damage-claims/{rj['id']}/reject", {"reason": "Not reported in time"}, expect=200)["status"] == "rejected"
    summary = sx.get("/damage-claims/summary")
    assert summary["settled"]["settled"] == "80.0000" and summary["rejected"]["count"] == 1
    assert [x["number"] for x in sx.get("/damage-claims", status="open")] == []
    over = sx.api.c.post(f"{sx.base}/damage-claims", headers=sx.h, json={
        "product_id": p, "warehouse_id": sx.main, "quantity": "50", "reason": "breakage"})
    assert over.status_code in (409, 422)
    books_ok(sx)


# ---------------------------------------------------------------------------- loyalty


def test_loyalty_points_earn_redeem_and_reverse(sx):
    c = customer(sx, "Regular Customer")
    line = [{"description": "Wellness kit", "quantity": "1", "unit_price": "1000", "gst_rate": "0"}]
    # Module off: nothing earned.
    finalize(sx, doc(sx, "sales_invoice", line, party_id=c["id"])["id"], [{"mode": "cash", "amount": "1000"}])
    assert sx.get(f"/customers/{c['id']}/loyalty")["points"] == "0"

    assert sx.api.c.put(f"{sx.base}/modules", headers=sx.h, json={"modules": {"loyalty": True}}).status_code == 200
    r = sx.api.c.put(f"{sx.base}/loyalty/settings", headers=sx.h, json={"earn_per_100": "2", "point_value": "0.5", "min_redeem": "10"})
    assert r.status_code == 200
    inv = finalize(sx, doc(sx, "sales_invoice", line, party_id=c["id"])["id"], [{"mode": "cash", "amount": "1000"}])
    st = sx.get(f"/customers/{c['id']}/loyalty")
    assert (D(st["points"]), D(st["value"]), st["can_redeem"]) == (D(20), D(10), True)

    # Pay ₹10 of the next bill with points (20 points), the rest in cash; points earned only on ₹990.
    inv2 = finalize(sx, doc(sx, "sales_invoice", line, party_id=c["id"])["id"],
                    [{"mode": "loyalty", "amount": "10"}, {"mode": "cash", "amount": "990"}])
    assert inv2["payment_status"] == "paid"
    st = sx.get(f"/customers/{c['id']}/loyalty")
    assert D(st["points"]) == D("19.80")  # 20 - 20 + 19.80
    assert bal(sx, "loyalty_expense") == D(10)
    too_much = sx.api.c.post(f"{sx.base}/payments", headers=sx.h, json={
        "direction": "in", "party_id": c["id"], "mode": "loyalty", "amount": "50"})
    assert too_much.status_code == 422 and "points available" in too_much.json()["error"]["message"]

    # Cancelling an invoice takes its points back; cancelling the points receipt returns them.
    cash = next(p for p in sx.get("/payments")["items"] if any(a["document_id"] == inv["id"] for a in p["allocations"]))
    sx.post(f"/payments/{cash['id']}/cancel", {"reason": "billed twice"}, expect=200)
    sx.post(f"/documents/{inv['id']}/cancel", {"reason": "billed twice"}, expect=200)
    assert D(sx.get(f"/customers/{c['id']}/loyalty")["points"]) == D("-0.20")
    pay = next(p for p in sx.get("/payments")["items"] if p["mode"] == "loyalty")
    sx.post(f"/payments/{pay['id']}/cancel", {"reason": "wrong mode"}, expect=200)
    assert D(sx.get(f"/customers/{c['id']}/loyalty")["points"]) == D("19.80")

    adj = sx.post(f"/customers/{c['id']}/loyalty/adjust", {"points": "50", "note": "Welcome bonus"}, expect=200)
    assert D(adj["points"]) == D("69.80") and adj["entries"][0]["kind"] == "adjust"
    overview = sx.get("/loyalty")
    assert overview["active"] is True and overview["members"][0]["name"] == "Regular Customer"
    assert D(overview["outstanding_value"]) == D("34.90")
    books_ok(sx)
