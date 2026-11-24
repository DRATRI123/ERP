"""Billing engine end to end: invoices, GST, payments, conversions, returns, purchases,
numbering, printing and permissions."""

from __future__ import annotations

import threading
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.events.models import OutboxEvent
from tests.conftest import TODAY, VALID_GSTIN, Shop

D = Decimal
KA_GSTIN = "29AAGCB7383J1Z4"  # Karnataka: inter-state from Maharashtra


@pytest.fixture
def bshop(api, owner_headers) -> Shop:
    s = Shop(api, owner_headers, gstin=VALID_GSTIN, state_code="27", address_line1="12 FC Road",
             city="Pune", pincode="411004")
    s.api.c.patch(f"{s.base}/branches/{s.ho}", headers=s.h,
                  json={"address_line1": "12 FC Road", "city": "Pune", "pincode": "411004",
                        "state_code": "27"})
    return s


def doc(s: Shop, doc_type: str, lines: list[dict], expect: int = 201, headers=None, **kw):
    return s.post("/documents", {"doc_type": doc_type, "doc_date": TODAY, "lines": lines, **kw},
                  expect=expect, headers=headers)


def finalize(s: Shop, doc_id: str, payments: list | None = None, expect: int = 200, headers=None):
    return s.post(f"/documents/{doc_id}/finalize", {"payments": payments or []}, expect=expect,
                  headers=headers)


def invoice(s: Shop, lines, payments=None, **kw):
    return finalize(s, doc(s, "sales_invoice", lines, **kw)["id"], payments)


def customer(s: Shop, name="Clinic", **kw):
    return s.post("/customers", {"name": name, **kw})


def stocked(s: Shop, sku: str, qty="100", cost="50", **kw) -> str:
    p = s.product(sku, **kw)["id"]
    s.opening([{"product_id": p, "quantity": qty, "unit_cost": cost,
                **({"serials": [f"{sku}-{i}" for i in range(int(qty))]} if kw.get("serial_tracking") else {}),
                **({"batch_no": "B1", "expiry_date": "2030-01-01"} if kw.get("batch_tracking") else {})}])
    return p


def events(db, aggregate_id) -> list[str]:
    return sorted(db.scalars(select(OutboxEvent.event_type).where(
        OutboxEvent.aggregate_id == str(aggregate_id))).all())


# ---------------------------------------------------------------------------- retail sale


def test_counter_cash_sale_with_inclusive_prices(bshop, db):
    p = stocked(bshop, "tulsi-drops", selling_price="118", prices_include_tax=True, gst_rate="18",
                hsn_sac="3004")
    inv = invoice(bshop, [{"product_id": p, "quantity": "2"}],
                  payments=[{"mode": "upi", "amount": "236", "reference": "UTR123"}])
    fy = bshop.get("/financial-years")[0]["code"]
    assert inv["number"] == f"INV/{fy}/0001" and inv["status"] == "final"
    assert (inv["taxable_total"], inv["cgst_total"], inv["sgst_total"], inv["grand_total"]) == (
        "200.0000", "18.0000", "18.0000", "236.0000")
    assert (inv["payment_status"], inv["balance_due"]) == ("paid", "0.0000")
    assert inv["party_snapshot"] == {"name": "Cash Sale"}
    assert inv["seller_snapshot"]["gstin"] == VALID_GSTIN
    assert bshop.stock(p)[0] == D("98")
    assert events(db, uuid.UUID(inv["id"])) == ["document.finalized", "invoice.created", "invoice.paid"]


def test_walk_in_sale_must_be_paid_in_full(bshop):
    p = stocked(bshop, "x", selling_price="100")
    d = doc(bshop, "sales_invoice", [{"product_id": p}])
    r = finalize(bshop, d["id"], [{"mode": "cash", "amount": "50"}], expect=422)
    assert "paid in full" in r["error"]["message"]
    assert bshop.stock(p)[0] == D("100")  # nothing posted


def test_round_off_and_discounts(bshop):
    p = stocked(bshop, "ghee", unit="KG", selling_price="649.50", gst_rate="12")
    inv = invoice(bshop, [{"product_id": p, "quantity": "1.250", "discount_percent": "5"}],
                  payments=[{"mode": "cash", "amount": "864"}])
    # 1.25 × 649.50 = 811.88; −5% = 771.29; CGST = SGST = 46.28 (each rounded to the paisa)
    # → 863.85 → round off +0.15
    assert (inv["subtotal"], inv["discount_total"], inv["taxable_total"]) == ("811.8800", "40.5900", "771.2900")
    assert (inv["cgst_total"], inv["sgst_total"]) == ("46.2800", "46.2800")
    assert (inv["round_off"], inv["grand_total"]) == ("0.1500", "864.0000")


# ---------------------------------------------------------------------------- GST rules


def test_interstate_b2b_igst_einvoice_and_irn(bshop, db):
    p = stocked(bshop, "kit", selling_price="1000", gst_rate="18", hsn_sac="30049011")
    c = customer(bshop, "Bengaluru Hospital", gstin=KA_GSTIN, addresses=[
        {"kind": "billing", "address_line1": "1 MG Road", "city": "Bengaluru", "pincode": "560001",
         "state_code": "29"}])
    inv = invoice(bshop, [{"product_id": p, "quantity": "3"}], party_id=c["id"])
    assert inv["is_interstate"] and inv["place_of_supply"] == "29"
    assert (inv["igst_total"], inv["cgst_total"], inv["grand_total"]) == ("540.0000", "0.0000", "3540.0000")
    assert inv["payment_status"] == "unpaid" and inv["due_date"] == TODAY

    ej = bshop.get(f"/documents/{inv['id']}/einvoice")
    assert ej["TranDtls"]["SupTyp"] == "B2B" and ej["DocDtls"]["Typ"] == "INV"
    assert ej["BuyerDtls"]["Gstin"] == KA_GSTIN and ej["BuyerDtls"]["Pos"] == "29"
    assert ej["ValDtls"]["IgstVal"] == 540.0 and ej["ItemList"][0]["HsnCd"] == "30049011"

    irn = {"irn": "a" * 64, "ack_no": "112410000000001", "ack_date": "2026-11-24T10:00:00+05:30",
           "signed_qr": "eyJhbGciOi.test.signed"}
    out = bshop.post(f"/documents/{inv['id']}/irn", irn, expect=200)
    assert out["irn"] == "a" * 64
    bshop.post(f"/documents/{inv['id']}/irn", irn, expect=409)
    r = bshop.post(f"/documents/{inv['id']}/cancel", {"reason": "mistake"}, expect=409)
    assert "IRN" in r["error"]["message"]
    html = bshop.api.c.get(f"{bshop.base}/documents/{inv['id']}/print", headers=bshop.h).text
    assert "a" * 64 in html and "e-invoice QR" in html


def test_einvoice_requires_b2b_details(bshop):
    p = stocked(bshop, "kit2", selling_price="10")
    inv = invoice(bshop, [{"product_id": p}], payments=[{"mode": "cash", "amount": "10"}])
    r = bshop.api.c.get(f"{bshop.base}/documents/{inv['id']}/einvoice", headers=bshop.h)
    assert r.status_code == 422 and "buyer GSTIN" in r.json()["error"]["message"]


def test_utgst_in_union_territory(bshop):
    br = bshop.post("/branches", {"code": "CHD", "name": "Chandigarh", "state_code": "04"})["id"]
    wh = bshop.warehouse("CHD-W", br)
    p = bshop.product("ut-item", selling_price="100", gst_rate="18")["id"]
    bshop.opening([{"product_id": p, "quantity": "5", "unit_cost": "60"}], warehouse=wh)
    c = customer(bshop, "Chandigarh Buyer", gst_type="consumer", state_code="04")
    inv = invoice(bshop, [{"product_id": p}], party_id=c["id"], branch_id=br, warehouse_id=wh)
    assert inv["is_utgst"] and not inv["is_interstate"] and inv["sgst_total"] == "9.0000"
    html = bshop.api.c.get(f"{bshop.base}/documents/{inv['id']}/print", headers=bshop.h).text
    assert "UTGST" in html


def test_composition_business_issues_bill_of_supply(api, owner_headers):
    s = Shop(api, owner_headers, gst_registration_type="composition")
    p = s.product("comp", selling_price="100", gst_rate="18")["id"]
    s.opening([{"product_id": p, "quantity": "1", "unit_cost": "1"}])
    inv = invoice(s, [{"product_id": p}], payments=[{"mode": "cash", "amount": "100"}])
    assert inv["tax_mode"] == "bill_of_supply" and inv["tax_total"] == "0.0000"
    html = api.c.get(f"{s.base}/documents/{inv['id']}/print", headers=owner_headers).text
    assert "Bill of Supply" in html


def test_versioned_hsn_rates(bshop):
    p = stocked(bshop, "rated", selling_price="100", gst_rate="12", hsn_sac="30049011")
    old_from = (date.today() - timedelta(days=400)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    rate = bshop.post("/tax-rates", {"hsn_prefix": "3004", "gst_rate": "12", "effective_from": old_from})
    bshop.post("/tax-rates", {"hsn_prefix": "3004", "gst_rate": "5", "effective_from": TODAY}, expect=422)
    r = bshop.api.c.patch(f"{bshop.base}/tax-rates/{rate['id']}", headers=bshop.h,
                          json={"effective_to": yesterday})
    assert r.status_code == 200
    bshop.post("/tax-rates", {"hsn_prefix": "3004", "gst_rate": "5", "effective_from": TODAY})
    bshop.post("/tax-rates", {"hsn_prefix": "30049011", "gst_rate": "0", "effective_from": old_from,
                              "effective_to": yesterday})
    today_doc = bshop.post("/documents/preview", {"doc_type": "sales_invoice", "doc_date": TODAY,
                                                  "lines": [{"product_id": p}]}, expect=200)
    assert today_doc["lines"][0]["gst_rate"] == "5.0000"
    past = bshop.post("/documents/preview", {"doc_type": "sales_invoice", "doc_date": yesterday,
                                             "lines": [{"product_id": p}]}, expect=200)
    assert past["lines"][0]["gst_rate"] == "0.0000"  # most specific prefix wins
    assert past["lines"][0]["tax_category"] == "nil_rated"
    override = bshop.post("/documents/preview", {"doc_type": "sales_invoice", "doc_date": TODAY,
                          "lines": [{"product_id": p, "gst_rate": "18"}]}, expect=200)
    assert override["igst_total"] == "0.0000" and override["cgst_total"] == "9.0000"
    assert bshop.get("/documents")["total"] == 0  # previews are never saved


# ---------------------------------------------------------------------------- lifecycle


def test_draft_edit_finalize_immutable_and_delete(bshop):
    p = stocked(bshop, "edit", selling_price="10")
    c = customer(bshop)
    d = doc(bshop, "sales_invoice", [{"product_id": p, "quantity": "1"}], party_id=c["id"])
    assert d["status"] == "draft" and d["number"] is None
    r = bshop.api.c.patch(f"{bshop.base}/documents/{d['id']}", headers=bshop.h,
                          json={"lines": [{"product_id": p, "quantity": "3"}], "notes": "edited"})
    assert r.status_code == 200 and r.json()["grand_total"] == "30.0000"
    final = finalize(bshop, d["id"])
    r = bshop.api.c.patch(f"{bshop.base}/documents/{d['id']}", headers=bshop.h, json={"notes": "x"})
    assert r.status_code == 409
    assert bshop.api.c.delete(f"{bshop.base}/documents/{d['id']}", headers=bshop.h).status_code == 409
    finalize(bshop, d["id"], expect=409)
    d2 = doc(bshop, "sales_invoice", [{"product_id": p}], party_id=c["id"])
    assert bshop.api.c.delete(f"{bshop.base}/documents/{d2['id']}", headers=bshop.h).status_code == 204
    assert final["number"].endswith("0001")


def test_cancel_restores_stock_and_numbers_are_not_reused(bshop, db):
    p = stocked(bshop, "cancel-me", selling_price="10", qty="10", cost="4")
    c = customer(bshop)
    inv = invoice(bshop, [{"product_id": p, "quantity": "4"}], party_id=c["id"])
    assert bshop.stock(p) == (D("6"), D("24"))
    out = bshop.post(f"/documents/{inv['id']}/cancel", {"reason": "wrong customer"}, expect=200)
    assert out["status"] == "cancelled" and out["balance_due"] == "0.0000"
    assert bshop.stock(p) == (D("10"), D("40"))
    inv2 = invoice(bshop, [{"product_id": p}], party_id=c["id"])
    assert inv2["number"].endswith("0002")
    assert "invoice.cancelled" in events(db, uuid.UUID(inv["id"]))
    bshop.assert_consistent(db)


def test_client_ref_makes_create_idempotent(bshop):
    p = stocked(bshop, "idem", selling_price="5")
    a = doc(bshop, "sales_invoice", [{"product_id": p}], client_ref="POS1-000123")
    b = doc(bshop, "sales_invoice", [{"product_id": p, "quantity": "9"}], client_ref="POS1-000123")
    assert a["id"] == b["id"] and b["lines"][0]["quantity"] == "1.000"


# ---------------------------------------------------------------------------- payments & credit


def test_partial_payments_and_cancellation(bshop, db):
    p = stocked(bshop, "pp", selling_price="1000")
    c = customer(bshop, credit_days=15)
    inv = invoice(bshop, [{"product_id": p}], party_id=c["id"])
    assert inv["due_date"] == (date.fromisoformat(TODAY) + timedelta(days=15)).isoformat()
    pay = {"direction": "in", "party_id": c["id"], "mode": "bank_transfer", "amount": "400",
           "allocations": [{"document_id": inv["id"], "amount": "400"}]}
    p1 = bshop.post("/payments", pay)
    assert p1["number"].startswith("RCT/")
    got = bshop.get(f"/documents/{inv['id']}")
    assert (got["payment_status"], got["balance_due"]) == ("partial", "600.0000")
    bshop.post("/payments", {**pay, "amount": "700",
                             "allocations": [{"document_id": inv["id"], "amount": "700"}]}, expect=422)
    bshop.post("/payments", {**pay, "direction": "out"}, expect=422)  # cannot pay out on a sale
    bshop.post("/payments", {**pay, "amount": "600",
                             "allocations": [{"document_id": inv["id"], "amount": "600"}]})
    assert bshop.get(f"/documents/{inv['id']}")["payment_status"] == "paid"
    bshop.post(f"/documents/{inv['id']}/cancel", {"reason": "x x x"}, expect=409)
    bshop.post(f"/payments/{p1['id']}/cancel", {"reason": "bounced"}, expect=200)
    got = bshop.get(f"/documents/{inv['id']}")
    assert (got["payment_status"], got["balance_due"]) == ("partial", "400.0000")
    out = bshop.get(f"/parties/{c['id']}/outstanding")
    assert out["outstanding"] == "400.0000" and len(out["documents"]) == 1
    assert "invoice.paid" in events(db, uuid.UUID(inv["id"]))


def test_advance_payment_with_no_allocation(bshop):
    c = customer(bshop)
    p = bshop.post("/payments", {"direction": "in", "party_id": c["id"], "mode": "cash",
                                 "amount": "500", "allocations": []})
    assert p["amount"] == "500.0000" and p["allocations"] == []


def test_credit_limit(bshop):
    api, h = bshop.api, bshop.h
    p = stocked(bshop, "cl", selling_price="1000")
    c = customer(bshop, credit_limit="1500")
    oh, _ = api.member_session(h, bshop.bid, "op@example.com", "billing_operator")
    first = doc(bshop, "sales_invoice", [{"product_id": p}], party_id=c["id"], headers=oh)
    finalize(bshop, first["id"], headers=oh)
    second = doc(bshop, "sales_invoice", [{"product_id": p}], party_id=c["id"], headers=oh)
    r = finalize(bshop, second["id"], headers=oh, expect=409)
    assert r["error"]["code"] == "credit_limit_exceeded" and r["error"]["details"]["outstanding"] == "2000.0000"
    # Paying at the counter keeps exposure within the limit.
    finalize(bshop, second["id"], [{"mode": "cash", "amount": "1000"}], headers=oh)
    third = doc(bshop, "sales_invoice", [{"product_id": p}], party_id=c["id"], headers=oh)
    finalize(bshop, third["id"])  # the owner may override


# ---------------------------------------------------------------------------- conversions


def test_quotation_to_order_to_invoice(bshop):
    p = stocked(bshop, "conv", selling_price="200", gst_rate="5")
    c = customer(bshop)
    q = finalize(bshop, doc(bshop, "quotation", [{"product_id": p, "quantity": "5"}], party_id=c["id"])["id"])
    assert q["number"].startswith("QT/") and q["payment_status"] == "not_applicable"
    assert bshop.stock(p)[0] == D("100")  # quotations never move stock
    so = bshop.post(f"/documents/{q['id']}/convert", {"target": "sales_order"})
    assert so["reference_document_id"] == q["id"] and so["lines"][0]["source_line_id"] == q["lines"][0]["id"]
    so = finalize(bshop, so["id"])
    inv = finalize(bshop, bshop.post(f"/documents/{so['id']}/convert", {"target": "sales_invoice"})["id"])
    assert inv["grand_total"] == q["grand_total"] and bshop.stock(p)[0] == D("95")
    bshop.post(f"/documents/{q['id']}/convert", {"target": "credit_note"}, expect=422)


def test_delivery_challan_then_invoice_moves_stock_once(bshop, db):
    p = stocked(bshop, "dc", selling_price="10", qty="20")
    c = customer(bshop)
    dc = finalize(bshop, doc(bshop, "delivery_challan", [{"product_id": p, "quantity": "8"}],
                             party_id=c["id"])["id"])
    assert bshop.stock(p)[0] == D("12")
    inv = bshop.post(f"/documents/{dc['id']}/convert", {"target": "sales_invoice"})
    bshop.api.c.patch(f"{bshop.base}/documents/{inv['id']}", headers=bshop.h, json={"lines": [
        {"product_id": p, "quantity": "5", "source_line_id": dc["lines"][0]["id"]}]})
    finalize(bshop, inv["id"])
    assert bshop.stock(p)[0] == D("12")  # not moved again
    rest = bshop.post(f"/documents/{dc['id']}/convert", {"target": "sales_invoice"})
    assert rest["lines"][0]["quantity"] == "3.000"  # only what is left on the challan
    bshop.api.c.patch(f"{bshop.base}/documents/{rest['id']}", headers=bshop.h, json={"lines": [
        {"product_id": p, "quantity": "4", "source_line_id": dc["lines"][0]["id"]}]})
    r = finalize(bshop, rest["id"], expect=422)
    assert "only 3.000 left" in r["error"]["message"]
    bshop.post(f"/documents/{dc['id']}/cancel", {"reason": "dependent"}, expect=409)
    bshop.assert_consistent(db)


# ---------------------------------------------------------------------------- returns


@pytest.mark.parametrize("valuation", ["weighted_average", "fifo"])
def test_sales_return_restocks_at_original_cost(api, owner_headers, db, valuation):
    s = Shop(api, owner_headers, valuation)
    p = s.product("ret", selling_price="100", gst_rate="18")["id"]
    s.opening([{"product_id": p, "quantity": "10", "unit_cost": "30"}])
    s.adjust([{"product_id": p, "quantity": "10", "unit_cost": "50"}])
    c = s.post("/customers", {"name": "Returner"})
    inv = invoice(s, [{"product_id": p, "quantity": "12"}], party_id=c["id"])
    issued = s.get("/inventory/ledger", product_id=p)["items"][0]
    assert -D(issued["value"]) == (D("480") if valuation == "weighted_average" else D("400"))
    cn = s.post(f"/documents/{inv['id']}/convert", {"target": "credit_note"})
    s.api.c.patch(f"{s.base}/documents/{cn['id']}", headers=s.h, json={"lines": [
        {"product_id": p, "quantity": "12", "source_line_id": inv["lines"][0]["id"]}]})
    cn = finalize(s, cn["id"])
    assert cn["number"].startswith("CN/") and cn["grand_total"] == inv["grand_total"]
    assert s.stock(p) == (D("20"), D("800"))  # back at exactly the cost it left with
    over = s.post(f"/documents/{inv['id']}/convert", {"target": "credit_note"}, expect=409)
    assert "Nothing left" in over["error"]["message"]
    assert s.get(f"/parties/{c['id']}/outstanding")["outstanding"] == "0.0000"
    s.assert_consistent(db)


def test_value_only_credit_note_does_not_restock(bshop):
    p = stocked(bshop, "disc", selling_price="100")
    c = customer(bshop)
    inv = invoice(bshop, [{"product_id": p, "quantity": "2"}], party_id=c["id"])
    cn = bshop.post(f"/documents/{inv['id']}/convert", {"target": "credit_note"})
    bshop.api.c.patch(f"{bshop.base}/documents/{cn['id']}", headers=bshop.h, json={
        "restock": False, "lines": [{"product_id": p, "quantity": "2", "unit_price": "10",
                                     "source_line_id": inv["lines"][0]["id"]}]})
    finalize(bshop, cn["id"])
    assert bshop.stock(p)[0] == D("98")
    assert bshop.get(f"/parties/{c['id']}/outstanding")["outstanding"] == "180.0000"


def test_serial_numbers_sold_and_returned(bshop):
    p = stocked(bshop, "bp-monitor", qty="3", selling_price="2000", serial_tracking=True)
    c = customer(bshop)
    d = doc(bshop, "sales_invoice", [{"product_id": p, "quantity": "1"}], party_id=c["id"])
    finalize(bshop, d["id"], expect=422)  # serial required
    d = doc(bshop, "sales_invoice", [{"product_id": p, "quantity": "2",
                                      "serials": ["bp-monitor-0", "bp-monitor-2"]}], party_id=c["id"])
    inv = finalize(bshop, d["id"])
    serial = bshop.get("/inventory/serials", q="bp-monitor-2")["items"][0]
    assert serial["status"] == "out"
    cn = bshop.post(f"/documents/{inv['id']}/convert", {"target": "credit_note"})
    bshop.api.c.patch(f"{bshop.base}/documents/{cn['id']}", headers=bshop.h, json={"lines": [
        {"product_id": p, "quantity": "1", "serials": ["bp-monitor-1"],
         "source_line_id": inv["lines"][0]["id"]}]})
    finalize(bshop, cn["id"], expect=422)  # was never sold on this invoice
    bshop.api.c.patch(f"{bshop.base}/documents/{cn['id']}", headers=bshop.h, json={"lines": [
        {"product_id": p, "quantity": "1", "serials": ["bp-monitor-2"],
         "source_line_id": inv["lines"][0]["id"]}]})
    finalize(bshop, cn["id"])
    assert bshop.get("/inventory/serials", q="bp-monitor-2")["items"][0]["status"] == "in_stock"


# ---------------------------------------------------------------------------- purchases


def test_purchase_invoice_itc_landed_cost_and_return(bshop, db):
    sup = bshop.post("/suppliers", {"name": "Herb Farm", "gstin": KA_GSTIN})
    a = bshop.product("herb-a", purchase_price="100", gst_rate="12")["id"]
    b = bshop.product("herb-b", purchase_price="50", gst_rate="18", batch_tracking=True)["id"]
    pur = finalize(bshop, doc(bshop, "purchase_invoice", [
        {"product_id": a, "quantity": "10"},
        {"product_id": b, "quantity": "10", "itc_eligible": False, "batch_no": "HB-01",
         "expiry_date": "2028-06-30"},
    ], party_id=sup["id"], supplier_invoice_no="KA/889", supplier_invoice_date=TODAY,
        landed_cost="150")["id"])
    assert pur["is_interstate"] and pur["igst_total"] == "210.0000"  # 120 + 90
    assert pur["number"].startswith("PUR/") and pur["payment_status"] == "unpaid"
    # Stock cost: A = 1000 + landed 100 (2/3 by value); B = 500 + 90 non-claimable tax + 50.
    assert bshop.stock(a) == (D("10"), D("1100"))
    assert bshop.stock(b) == (D("10"), D("640"))
    dup = doc(bshop, "purchase_invoice", [{"product_id": a}], party_id=sup["id"], supplier_invoice_no="KA/889")
    finalize(bshop, dup["id"], expect=409)

    pr = bshop.post(f"/documents/{pur['id']}/convert", {"target": "purchase_return"})
    bshop.api.c.patch(f"{bshop.base}/documents/{pr['id']}", headers=bshop.h, json={"lines": [
        {"product_id": a, "quantity": "2", "source_line_id": pur["lines"][0]["id"]}]})
    pr = finalize(bshop, pr["id"])
    assert pr["igst_total"] == "24.0000" and bshop.stock(a)[0] == D("8")
    assert bshop.get(f"/parties/{sup['id']}/outstanding", side="purchase")["outstanding"] == "1486.0000"  # 1710 billed − 224 returned
    pay = bshop.post("/payments", {"direction": "out", "party_id": sup["id"], "mode": "bank_transfer",
                                   "amount": "500", "allocations": [{"document_id": pur["id"], "amount": "500"}]})
    assert pay["number"].startswith("PAY/")
    assert "purchase.created" in events(db, uuid.UUID(pur["id"]))
    bshop.assert_consistent(db)


def test_reverse_charge_purchase_excluded_from_payable(bshop):
    sup = bshop.post("/suppliers", {"name": "Advocate", "gst_type": "unregistered", "state_code": "27"})
    svc = bshop.product("legal", unit="HRS", kind="service", hsn_sac="998211", purchase_price="10000",
                        gst_rate="18")["id"]
    pur = finalize(bshop, doc(bshop, "purchase_invoice", [{"product_id": svc}], party_id=sup["id"],
                              supplier_invoice_no="L-1", reverse_charge=True)["id"])
    assert pur["tax_total"] == "1800.0000" and pur["grand_total"] == "11800.0000"
    assert pur["amount_payable"] == "10000.0000" and pur["balance_due"] == "10000.0000"
    r3b = bshop.get("/gst/gstr3b", date_from=TODAY, date_to=TODAY)
    assert r3b["3.1"]["d_inward_reverse_charge"]["cgst"] == "900.00"
    assert r3b["4"]["a3_itc_inward_reverse_charge"]["sgst"] == "900.00"


# ---------------------------------------------------------------------------- numbering


def test_custom_series_and_gst_number_rule(bshop):
    p = stocked(bshop, "ser", selling_price="1")
    bshop.post("/document-series", {"doc_type": "sales_invoice", "name": "Retail", "prefix": "{BRANCH}/R{FYS}/",
                                    "padding": 5, "is_default": True})
    inv = invoice(bshop, [{"product_id": p}], payments=[{"mode": "cash", "amount": "1"}])
    fys = bshop.get("/financial-years")[0]["code"].replace("-", "")[2:]
    assert inv["number"] == f"HO/R{fys}/00001"
    bshop.post("/document-series", {"doc_type": "sales_invoice", "name": "Too long",
                                    "prefix": "ABCDEFGHIJKLMNOP/", "is_default": True})
    d = doc(bshop, "sales_invoice", [{"product_id": p}])
    r = finalize(bshop, d["id"], [{"mode": "cash", "amount": "1"}], expect=422)
    assert "GST rules" in r["error"]["message"]
    bshop.post("/document-series", {"doc_type": "sales_invoice", "name": "Bad", "prefix": "{YEAR}/"},
               expect=422)


# ---------------------------------------------------------------------------- GST returns


def test_gst_registers_and_returns(bshop):
    p = stocked(bshop, "gst-a", qty="300", selling_price="1000", gst_rate="18", hsn_sac="3004")
    ex = stocked(bshop, "gst-exempt", selling_price="50", gst_rate="0", hsn_sac="0401")
    b2b = customer(bshop, "Reg Buyer", gstin=KA_GSTIN)
    big = customer(bshop, "Big Unreg", gst_type="consumer", state_code="29")
    inv_b2b = invoice(bshop, [{"product_id": p, "quantity": "2"}], party_id=b2b["id"])
    invoice(bshop, [{"product_id": p, "quantity": "150"}], party_id=big["id"])  # B2CL (> ₹1 lakh)
    invoice(bshop, [{"product_id": p}, {"product_id": ex}],
            payments=[{"mode": "cash", "amount": "1230"}])  # B2CS local + nil
    walk = doc(bshop, "sales_invoice", [{"product_id": p}])
    walk = finalize(bshop, walk["id"], [{"mode": "cash", "amount": "1180"}])
    pays = bshop.get("/payments")["items"]
    cash = next(x for x in pays if x["allocations"] and x["allocations"][0]["document_id"] == walk["id"])
    bshop.post(f"/payments/{cash['id']}/cancel", {"reason": "void"}, expect=200)
    bshop.post(f"/documents/{walk['id']}/cancel", {"reason": "void"}, expect=200)
    cn = bshop.post(f"/documents/{inv_b2b['id']}/convert", {"target": "credit_note"})
    bshop.api.c.patch(f"{bshop.base}/documents/{cn['id']}", headers=bshop.h, json={"lines": [
        {"product_id": p, "quantity": "1", "source_line_id": inv_b2b["lines"][0]["id"]}]})
    finalize(bshop, cn["id"])

    reg = bshop.get("/gst/sales-register", date_from=TODAY, date_to=TODAY)
    assert len(reg["rows"]) == 4 and D(reg["totals"]["taxable_value"]) == D("2000") + D("150000") + D("1050") - D("1000")

    g1 = bshop.get("/gst/gstr1", date_from=TODAY, date_to=TODAY)
    assert g1["status"] == "prepared — not filed"
    assert [x["ctin"] for x in g1["b2b"]] == [KA_GSTIN]
    assert g1["b2b"][0]["invoices"][0]["items"][0]["igst"] == "360.00"
    assert len(g1["b2cl"]) == 1 and g1["b2cl"][0]["place_of_supply"] == "29"
    assert [(x["place_of_supply"], x["rate"], x["taxable_value"]) for x in g1["b2cs"]] == [("27", "18.0000", "1000.00")]
    assert g1["nil"]["intra_unregistered"]["nil_rated"] == "50.00"
    assert g1["cdnr"][0]["notes"][0]["original_number"] == inv_b2b["number"]
    issued = {x["doc_type"]: x for x in g1["doc_issue"]}
    assert issued["sales_invoice"]["total"] == 4 and issued["sales_invoice"]["cancelled"] == 1
    hsn = {x["hsn_sac"]: x for x in g1["hsn"]["b2b"]}
    assert hsn["3004"]["quantity"] == "1.000"  # 2 sold − 1 returned

    g3 = bshop.get("/gst/gstr3b", date_from=TODAY, date_to=TODAY)
    out = g3["3.1"]["a_outward_taxable"]
    assert out["taxable_value"] == "152000.00" and out["igst"] == str(D("27000.00") + D("180.00"))
    assert g3["3.1"]["c_outward_nil_exempt"]["taxable_value"] == "50.00"


# ---------------------------------------------------------------------------- printing


def test_print_html_pdf_templates_and_upi(bshop):
    p = stocked(bshop, "print-me", selling_price="1180", prices_include_tax=True, gst_rate="18",
                hsn_sac="3004")
    c = customer(bshop, "Print & <Co>")
    inv = invoice(bshop, [{"product_id": p, "quantity": "100"}], party_id=c["id"])
    url = f"{bshop.base}/documents/{inv['id']}/print"
    html = bshop.api.c.get(url, headers=bshop.h).text
    assert inv["number"] in html and VALID_GSTIN in html and "Tax Invoice" in html
    assert "Rupees One Lakh Eighteen Thousand Only" in html and "1,18,000.00" in html
    assert "Print &amp; &lt;Co&gt;" in html and "<Co>" not in html  # escaped

    bad = bshop.post("/invoice-templates", {"name": "Bad", "config": {"primary_color": "red"}}, expect=422)
    assert bad["error"]["code"] == "validation_failed"
    bshop.post("/invoice-templates", {"name": "Bad2", "config": {"logo_data_url": "javascript:alert(1)"}},
               expect=422)
    bshop.post("/invoice-templates", {"name": "Bad3", "config": {"font_family": "x;}</style>"}}, expect=422)
    bshop.post("/invoice-templates", {"name": "Counter", "doc_types": ["sales_invoice"], "is_default": True,
               "config": {"paper": "thermal_80", "show_upi_qr": True, "upi_id": "shop@okaxis",
                          "footer_text": "Visit again"}})
    thermal = bshop.api.c.get(url, headers=bshop.h).text
    assert "80mm auto" in thermal and "UPI" in thermal and "Visit again" in thermal
    a5 = bshop.api.c.get(url, headers=bshop.h, params={"paper": "a5", "copies": 3}).text
    assert "148mm 210mm" in a5 and a5.count('class="copy"') == 3 and "Triplicate for Supplier" in a5

    assert "script-src" not in html and "default-src 'none'" in html  # CSP: no scripts, no network
    from app.modules.billing.printing import chromium_path

    r = bshop.api.c.get(url, headers=bshop.h, params={"format": "pdf"})
    if chromium_path() is None:  # only acceptable when the machine has no Chromium at all
        assert r.status_code == 503 and r.json()["error"]["code"] == "pdf_unavailable"
    else:
        assert r.status_code == 200, r.text
        assert r.content[:5] == b"%PDF-" and r.headers["content-type"] == "application/pdf"


# ---------------------------------------------------------------------------- permissions


def test_role_limits_on_documents(bshop):
    api, h = bshop.api, bshop.h
    p = stocked(bshop, "perm", selling_price="10")
    sup = bshop.post("/suppliers", {"name": "S"})
    pur = finalize(bshop, doc(bshop, "purchase_invoice", [{"product_id": p, "unit_price": "4"}],
                              party_id=sup["id"], supplier_invoice_no="1")["id"])
    oh, _ = api.member_session(h, bshop.bid, "op@example.com", "billing_operator")
    inv = doc(bshop, "sales_invoice", [{"product_id": p}], headers=oh)
    inv = finalize(bshop, inv["id"], [{"mode": "cash", "amount": "10"}], headers=oh)
    assert api.c.get(f"{bshop.base}/documents/{pur['id']}", headers=oh).status_code == 403
    listed = bshop.get("/documents", headers=oh)["items"]
    assert [d["doc_type"] for d in listed] == ["sales_invoice"]
    doc(bshop, "purchase_invoice", [{"product_id": p, "unit_price": "4"}], headers=oh, expect=403)
    bshop.post(f"/documents/{inv['id']}/cancel", {"reason": "nope"}, expect=403, headers=oh)
    assert api.c.get(f"{bshop.base}/gst/gstr1", headers=oh,
                     params={"date_from": TODAY, "date_to": TODAY}).status_code == 403


# ---------------------------------------------------------------------------- concurrency


def test_concurrent_finalization_unique_numbers_no_oversell(api, owner_headers, owner, db):
    from app.db.session import SessionLocal
    from app.modules.access.service import get_business_access
    from app.modules.billing.models import Document
    from app.modules.billing.service import finalize as finalize_doc
    from app.modules.users.models import User

    s = Shop(api, owner_headers)
    p = stocked(s, "rush", qty="6", selling_price="10")
    c = customer(s)
    drafts = [doc(s, "sales_invoice", [{"product_id": p}], party_id=c["id"])["id"] for _ in range(10)]
    results: list[str] = []
    lock = threading.Lock()

    def work(ids: list[str]) -> None:
        session = SessionLocal()
        try:
            for i in ids:
                try:
                    access = get_business_access(session, session.get(User, owner.id), uuid.UUID(s.bid))
                    finalize_doc(session, access, session.get(Document, uuid.UUID(i)))
                    session.commit()
                    outcome = "ok"
                except Exception as exc:
                    session.rollback()
                    outcome = type(exc).__name__
                with lock:
                    results.append(outcome)
        finally:
            session.close()

    threads = [threading.Thread(target=work, args=(drafts[i::4],)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 6 and results.count("InsufficientStock") == 4, results
    numbers = [d["number"] for d in s.get("/documents", status_filter="final")["items"]]
    assert len(numbers) == len(set(numbers)) == 6
    assert s.stock(p)[0] == D("0")
    s.assert_consistent(db)


def test_batch_print_single_pdf(bshop):
    from app.modules.billing.printing import chromium_path

    p = stocked(bshop, "batch", selling_price="10")
    ids = [invoice(bshop, [{"product_id": p}], payments=[{"mode": "cash", "amount": "10"}])["id"] for _ in range(3)]
    r = bshop.api.c.post(f"{bshop.base}/documents/print-batch", headers=bshop.h, json={"document_ids": ids})
    if chromium_path() is None:
        assert r.status_code == 503
        return
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
    assert r.content.count(b"/Type /Page\n") + r.content.count(b"/Type/Page") >= 3 or b"/Count 3" in r.content
    other = bshop.post("/invoice-templates", {"name": "Thermal", "doc_types": ["sales_invoice"], "is_default": True,
                                              "config": {"paper": "thermal_80"}})
    assert other["is_default"]
    br = bshop.post("/branches", {"code": "B2", "name": "Two"})
    bshop.post("/invoice-templates", {"name": "B2 A5", "branch_id": br["id"], "doc_types": ["sales_invoice"],
                                      "is_default": True, "config": {"paper": "a5"}})
    wh = bshop.warehouse("W2", br["id"])
    bshop.opening([{"product_id": p, "quantity": "5", "unit_cost": "1"}], warehouse=wh)
    b2 = invoice(bshop, [{"product_id": p}], payments=[{"mode": "cash", "amount": "10"}], branch_id=br["id"],
                 warehouse_id=wh)
    r = bshop.api.c.post(f"{bshop.base}/documents/print-batch", headers=bshop.h,
                         json={"document_ids": [ids[0], b2["id"]]})
    assert r.status_code == 422 and "template" in r.json()["error"]["message"]
