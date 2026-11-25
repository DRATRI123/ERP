"""Money: TDS on payments and receipts, post-dated cheques, cheque printing, interest on overdue bills."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.modules.events.models import OutboxEvent
from app.modules.money.service import emit_cheque_due, indian_figures, tds_due_date
from tests.conftest import TODAY, Shop
from tests.test_accounting import bal, books_ok
from tests.test_billing import customer, doc, finalize

T = date.fromisoformat(TODAY)


def d(days: int) -> str:
    return (T + timedelta(days=days)).isoformat()


@pytest.fixture
def ms(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def supplier(s: Shop, name="Transporter", **kw) -> dict:
    return s.post("/suppliers", {"name": name, **kw})


def bill(s: Shop, sup: dict, amount: str = "10000", no: str = "T-1", **kw) -> dict:
    return finalize(s, doc(s, "purchase_invoice", [{"description": "Freight", "quantity": "1", "unit_price": amount,
                                                    "gst_rate": "0"}], party_id=sup["id"], supplier_invoice_no=no, **kw)["id"])


def sale(s: Shop, c: dict, amount: str = "10000", **kw) -> dict:
    return finalize(s, doc(s, "sales_invoice", [{"description": "Consulting", "quantity": "1", "unit_price": amount,
                                                 "gst_rate": "0"}], party_id=c["id"], **kw)["id"])


def fails(s: Shop, path: str, body: dict, code: int = 422) -> dict:
    r = s.api.c.post(s.base + path, json=body, headers=s.h)
    assert r.status_code == code, r.text
    return r.json()["error"]


# ---------------------------------------------------------------------------- TDS


def test_tds_on_payment_and_receipt_with_register_and_deposit(ms):
    sup = supplier(ms, pan="ABCDE1234F", tds_section="194C")
    assert sup["tds_section"] == "194C"
    pinv = bill(ms, sup)
    pay = ms.post("/payments", {"direction": "out", "party_id": sup["id"], "mode": "bank_transfer", "amount": "9800",
                               "tds_amount": "200", "tds_section": "194C",
                               "allocations": [{"document_id": pinv["id"], "amount": "10000"}]})
    assert (pay["amount"], pay["tds_amount"], pay["tds_section"]) == ("9800.0000", "200.0000", "194C")
    assert ms.get(f"/documents/{pinv['id']}")["payment_status"] == "paid"
    assert bal(ms, "tds_payable") == D(-200) and bal(ms, "ap") == 0

    # Receipt: the customer deducted 10% under 194J.
    c = customer(ms, "Big Hospital")
    inv = sale(ms, c)
    ms.post("/payments", {"direction": "in", "party_id": c["id"], "mode": "bank_transfer", "amount": "9000",
                          "tds_amount": "1000", "tds_section": "194J",
                          "allocations": [{"document_id": inv["id"], "amount": "10000"}]})
    assert ms.get(f"/documents/{inv['id']}")["payment_status"] == "paid"
    assert bal(ms, "tds_receivable") == D(1000)

    # Validation: TDS needs a section and a party; allocations may not exceed money + TDS.
    base = {"direction": "out", "party_id": sup["id"], "mode": "cash", "amount": "100"}
    assert "section" in fails(ms, "/payments", {**base, "tds_amount": "10"})["message"]
    assert fails(ms, "/payments", {**base, "party_id": None, "tds_amount": "10", "tds_section": "194C"})
    pinv2 = bill(ms, sup, "500", "T-2")
    fails(ms, "/payments", {**base, "tds_amount": "10", "tds_section": "194C",
                            "allocations": [{"document_id": pinv2["id"], "amount": "120"}]})

    reg = ms.get("/reports/tds", direction="out", date_from=d(-1), date_to=TODAY)
    assert [(r["party_name"], r["pan"], r["section"], r["gross"], r["tds"], r["rate"]) for r in reg["rows"]] == [
        ("Transporter", "ABCDE1234F", "194C", "10000.0000", "200.0000", "2.00")]
    month = reg["months"][0]
    assert (month["deducted"], month["deposited"], month["balance"]) == ("200.0000", "0", "200.0000")
    assert month["due_date"] == tds_due_date(T.replace(day=1)).isoformat()
    assert ms.get("/reports/tds", direction="in", date_from=d(-1), date_to=TODAY)["total_tds"] == "1000.0000"

    bank = next(a for a in ms.get("/accounting/accounts") if a["system_key"] == "bank")
    dep = ms.post("/tds/deposits", {"month": TODAY, "section": "194C", "amount": "200", "challan_no": "00123",
                                    "bsr_code": "0510002", "account_id": bank["id"]})
    assert dep["month"] == T.replace(day=1).isoformat()
    assert bal(ms, "tds_payable") == 0
    month = ms.get("/reports/tds", direction="out", date_from=d(-1), date_to=TODAY)["months"][0]
    assert month["balance"] == "0.0000" and month["overdue"] is False
    assert {s["section"] for s in ms.get("/tds/sections")} >= {"194C", "194J", "194Q"}
    books_ok(ms)

    # Cancelling the payment reverses the TDS too.
    ms.post(f"/payments/{pay['id']}/cancel", {"reason": "wrong bank"}, expect=200)
    assert bal(ms, "tds_payable") == D(200)  # the deposit stays: now paid in advance
    books_ok(ms)


def test_tds_due_dates():
    assert tds_due_date(date(2026, 3, 1)) == date(2026, 4, 30)
    assert tds_due_date(date(2026, 12, 1)) == date(2027, 1, 7)
    assert tds_due_date(date(2026, 6, 1)) == date(2026, 7, 7)


# ---------------------------------------------------------------------------- cheques


def test_post_dated_cheque_clear_bounce_and_alerts(ms, db):
    c = customer(ms, "Wellness Clinic")
    inv = sale(ms, c, "5000")
    chq = ms.post("/cheques", {"direction": "in", "party_id": c["id"], "cheque_no": "456789", "cheque_date": d(5),
                               "bank_name": "SBI", "amount": "5000",
                               "allocations": [{"document_id": inv["id"], "amount": "5000"}]})
    assert chq["status"] == "pending" and chq["days_to_date"] == 5 and chq["party_name"] == "Wellness Clinic"
    assert ms.get(f"/documents/{inv['id']}")["payment_status"] == "unpaid"  # not in the books yet
    assert fails(ms, "/cheques", {"direction": "in", "party_id": c["id"], "cheque_no": "456789", "cheque_date": d(5),
                                  "amount": "1"}, 409)
    summary = ms.get("/cheques/summary")
    assert summary["in"]["pending"] == 1 and summary["in"]["due_7d_amount"] == "5000.0000"
    assert [x["cheque_no"] for x in ms.get("/cheques", status="pending", due_within_days=7)] == ["456789"]
    assert ms.get("/cheques", due_within_days=2) == []

    assert "before its date" in fails(ms, f"/cheques/{chq['id']}/clear", {"on": TODAY})["message"]

    # The daily alert fires once the date comes.
    assert emit_cheque_due(db, today=T + timedelta(days=5)) >= 1
    ev = db.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "cheque.due",
                                             OutboxEvent.business_id == uuid.UUID(ms.bid)))
    assert ev is not None and ev.payload["cheque_no"] == "456789"
    assert emit_cheque_due(db, today=T + timedelta(days=5)) == 0
    db.rollback()  # release SQLite's read lock before the API writes

    cleared = ms.post(f"/cheques/{chq['id']}/clear", {"on": d(5)}, expect=200)
    assert cleared["status"] == "cleared" and cleared["payment_id"]
    pay = ms.get(f"/payments/{cleared['payment_id']}")
    assert (pay["mode"], pay["reference"], pay["amount"]) == ("cheque", "Chq 456789 SBI", "5000.0000")
    assert ms.get(f"/documents/{inv['id']}")["payment_status"] == "paid"

    bounced = ms.post(f"/cheques/{chq['id']}/bounce", {"on": d(6), "reason": "Funds insufficient"}, expect=200)
    assert bounced["status"] == "bounced"
    assert ms.get(f"/payments/{cleared['payment_id']}")["status"] == "cancelled"
    assert ms.get(f"/documents/{inv['id']}")["payment_status"] == "unpaid"
    assert db.scalar(select(OutboxEvent.id).where(OutboxEvent.event_type == "cheque.bounced",
                                                  OutboxEvent.business_id == uuid.UUID(ms.bid)))
    db.rollback()
    fails(ms, f"/cheques/{chq['id']}/clear", {"on": d(6)}, 409)

    other = ms.post("/cheques", {"direction": "in", "party_id": c["id"], "cheque_no": "456790", "cheque_date": TODAY,
                                 "amount": "100"})
    assert ms.post(f"/cheques/{other['id']}/cancel", {"reason": "Returned to customer"}, expect=200)["status"] == "cancelled"
    books_ok(ms)


def test_cheque_printing_and_layout(ms):
    sup = supplier(ms, "Herbal Distributors Pvt Ltd")
    chq = ms.post("/cheques", {"direction": "out", "party_id": sup["id"], "cheque_no": "000123", "cheque_date": "2026-12-05",
                               "amount": "12500.50"})
    assert chq["payee_name"] == "Herbal Distributors Pvt Ltd"
    html = ms.api.c.get(f"{ms.base}/cheques/{chq['id']}/print", headers=ms.h)
    assert html.status_code == 200
    body = html.text
    assert "Herbal Distributors Pvt Ltd" in body and "Twelve Thousand Five Hundred and Fifty Paise Only" in body
    assert "**12,500.50/-" in body and "A/C PAYEE" in body and "size:203.0mm 92.0mm" in body
    for digit_box in ("0", "5", "1", "2"):
        assert f">{digit_box}</div>" in body

    lay = ms.get("/cheque-layout")
    assert lay["layout"]["date_x"] == 153
    r = ms.api.c.put(f"{ms.base}/cheque-layout", headers=ms.h, json={"layout": {"offset_x": 2.5, "ac_payee": False}})
    assert r.status_code == 200 and r.json()["layout"]["offset_x"] == 2.5
    body = ms.api.c.get(f"{ms.base}/cheques/{chq['id']}/print", headers=ms.h).text
    assert "A/C PAYEE" not in body
    assert ms.api.c.put(f"{ms.base}/cheque-layout", headers=ms.h, json={"layout": {"nope": 1}}).status_code == 422
    assert "Sample Payee" in ms.api.c.get(f"{ms.base}/cheque-layout/sample", headers=ms.h).text

    c = customer(ms)
    got = ms.post("/cheques", {"direction": "in", "party_id": c["id"], "cheque_no": "9", "cheque_date": TODAY, "amount": "5"})
    assert ms.api.c.get(f"{ms.base}/cheques/{got['id']}/print", headers=ms.h).status_code == 422


def test_indian_figures():
    assert indian_figures(D("1234567.5")) == "12,34,567.50"
    assert indian_figures(D("999")) == "999.00"
    assert indian_figures(D("100000")) == "1,00,000.00"


# ---------------------------------------------------------------------------- interest


def test_interest_on_overdue_with_part_payment_and_debit_note(ms):
    r = ms.api.c.put(f"{ms.base}/interest/settings", headers=ms.h, json={"rate": "18", "grace_days": 0})
    assert r.status_code == 200
    c = customer(ms, "Slow Payer", credit_days=30)
    inv = sale(ms, c, "36500", doc_date=d(-60))  # due on day -30
    assert inv["due_date"] == d(-30)
    ms.post("/payments", {"direction": "in", "party_id": c["id"], "mode": "cash", "amount": "18250", "payment_date": d(-10),
                          "allocations": [{"document_id": inv["id"], "amount": "18250"}]})
    out = ms.get("/interest", as_of=TODAY)
    assert len(out["parties"]) == 1
    row = out["parties"][0]["invoices"][0]
    # 36,500 for 19 days (day -29 to -11) + 18,250 for 11 days (day -10 to today) at 18%.
    expected = D(36500) * 18 * 19 / 36500 + D(18250) * 18 * 11 / 36500
    assert D(row["interest"]) == expected.quantize(D("0.01")) and row["days"] == 30
    assert row["interest_from"] == d(-29)

    # A customer with 0% is skipped; a party rate overrides the default.
    assert ms.get("/interest", as_of=TODAY, party_id=customer(ms, "Friend", interest_rate="0")["id"])["parties"] == []

    notes = ms.post("/interest/debit-notes", {"party_id": c["id"], "as_of": TODAY})
    assert len(notes) == 1
    dn = notes[0]
    assert dn["doc_type"] == "debit_note" and dn["status"] == "draft" and dn["reference_document_id"] == inv["id"]
    assert D(dn["lines"][0]["unit_price"]) == D(row["interest"]) and "18% p.a." in dn["lines"][0]["description"]
    assert ms.get("/interest", as_of=TODAY)["parties"] == []  # already charged up to today
    final = finalize(ms, dn["id"])
    assert final["number"]
    # Tomorrow only one more day accrues, and not on the interest note itself.
    later = ms.get("/interest", as_of=d(1))["parties"][0]["invoices"]
    assert [x["number"] for x in later] == [inv["number"]] and later[0]["days"] == 1
    assert fails(ms, "/interest/debit-notes", {"party_id": customer(ms, "Nobody")["id"]})["message"]


def test_modules_list_money_modules(ms):
    mods = ms.get("/modules")["enabled"]
    assert mods["cheques"] is True and mods["tds"] is True and mods["interest"] is False
