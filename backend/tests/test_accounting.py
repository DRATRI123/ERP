"""Double-entry accounting: automatic postings, manual journals, opening balances, expenses,
fixed assets, bank reconciliation, year closing and financial statements.

Every scenario ends with ``books_ok``: the trial balance and balance sheet balance, every journal
balances, and the Inventory account equals the stock valuation.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import TODAY, VALID_GSTIN, Shop
from tests.test_billing import KA_GSTIN, customer, doc, finalize, invoice, stocked

D = Decimal


@pytest.fixture
def ashop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers, gstin=VALID_GSTIN, state_code="27")


def acct(s: Shop) -> dict[str, dict]:
    """Accounts by system key (and by code for the rest)."""
    rows = s.get("/accounting/accounts", with_balances=True)
    return {(a["system_key"] or a["code"]): a for a in rows}


def bal(s: Shop, key: str) -> Decimal:
    return D(acct(s)[key]["balance"])  # debit-positive


def fy(s: Shop) -> dict:
    return next(f for f in s.get("/financial-years") if f["start_date"] <= TODAY <= f["end_date"])


def books_ok(s: Shop) -> None:
    f = fy(s)
    tb = s.get("/accounting/reports/trial-balance", date_from=f["start_date"], date_to=f["end_date"])
    assert tb["balanced"], tb["totals"]
    bs = s.get("/accounting/reports/balance-sheet", as_of=f["end_date"])
    assert bs["balanced"], (bs["total_assets"], bs["total_liabilities"], bs["total_equity"])
    integrity = s.get("/accounting/reports/integrity")
    assert integrity["ok"], integrity["problems"]


def journal_for(s: Shop, ref: str) -> list[dict]:
    return s.get("/accounting/journals", q=ref)["items"]


def lines_by_account(s: Shop, entry: dict) -> dict[str, tuple[str, str]]:
    names = {a["id"]: (a["system_key"] or a["code"]) for a in s.get("/accounting/accounts", include_inactive=True)}
    return {names[ln["account_id"]]: (ln["debit"], ln["credit"]) for ln in entry["lines"]}


# ---------------------------------------------------------------------------- chart


def test_new_business_has_chart_of_accounts(ashop):
    a = acct(ashop)
    for key in ("cash", "bank", "ar", "ap", "inventory", "sales", "cogs", "output_cgst", "input_igst",
                "rcm_igst", "retained_earnings", "opening_balance", "round_off", "depreciation"):
        assert key in a, key
    assert a["ar"]["type"] == "asset" and a["sales"]["type"] == "income"
    custom = ashop.post("/accounting/accounts", {"code": "6300", "name": "Clinic Consumables",
                                                 "subtype": "operating_expense"})
    assert custom["type"] == "expense"
    ashop.post("/accounting/accounts", {"code": "6300", "name": "Dup", "subtype": "operating_expense"},
               expect=409)
    r = ashop.api.c.patch(f"{ashop.base}/accounting/accounts/{a['ar']['id']}", headers=ashop.h,
                          json={"is_active": False})
    assert r.status_code == 422  # system accounts stay


# ---------------------------------------------------------------------------- sales & purchases


def test_cash_sale_posts_revenue_tax_cogs_and_bank(ashop):
    p = stocked(ashop, "tulsi", cost="50", selling_price="118", prices_include_tax=True, gst_rate="18")
    inv = invoice(ashop, [{"product_id": p, "quantity": "2"}], payments=[{"mode": "upi", "amount": "236"}])
    entry = journal_for(ashop, inv["number"])[0]
    assert lines_by_account(ashop, entry) == {
        "ar": ("236.0000", "0.0000"), "sales": ("0.0000", "200.0000"),
        "output_cgst": ("0.0000", "18.0000"), "output_sgst": ("0.0000", "18.0000"),
        "cogs": ("100.0000", "0.0000"), "inventory": ("0.0000", "100.0000")}
    assert bal(ashop, "bank") == D("236") and bal(ashop, "ar") == D("0")
    f = fy(ashop)
    pl = ashop.get("/accounting/reports/profit-and-loss", date_from=f["start_date"], date_to=f["end_date"])
    assert (pl["total_revenue"], pl["total_cost_of_goods_sold"], pl["gross_profit"]) == ("200.00", "100.00", "100.00")
    assert pl["gross_margin_percent"] == "50.00"
    books_ok(ashop)


def test_round_off_is_posted(ashop):
    p = stocked(ashop, "ghee", unit="KG", selling_price="649.50", gst_rate="12")
    c = customer(ashop)
    inv = invoice(ashop, [{"product_id": p, "quantity": "1.250", "discount_percent": "5"}], party_id=c["id"])
    lines = lines_by_account(ashop, journal_for(ashop, inv["number"])[0])
    assert lines["round_off"] == ("0.0000", "0.1500") and lines["ar"] == ("864.0000", "0.0000")
    books_ok(ashop)


def test_interstate_credit_sale_and_return(ashop):
    p = stocked(ashop, "kit", qty="10", cost="400", selling_price="1000", gst_rate="18")
    c = customer(ashop, "KA Hospital", gstin=KA_GSTIN)
    inv = invoice(ashop, [{"product_id": p, "quantity": "3"}], party_id=c["id"])
    assert lines_by_account(ashop, journal_for(ashop, inv["number"])[0])["output_igst"] == ("0.0000", "540.0000")
    cn = ashop.post(f"/documents/{inv['id']}/convert", {"target": "credit_note"})
    ashop.api.c.patch(f"{ashop.base}/documents/{cn['id']}", headers=ashop.h, json={"lines": [
        {"product_id": p, "quantity": "1", "source_line_id": inv["lines"][0]["id"]}]})
    cn = finalize(ashop, cn["id"])
    cl = lines_by_account(ashop, journal_for(ashop, cn["number"])[0])
    assert cl == {"ar": ("0.0000", "1180.0000"), "sales_returns": ("1000.0000", "0.0000"),
                  "output_igst": ("180.0000", "0.0000"), "inventory": ("400.0000", "0.0000"),
                  "cogs": ("0.0000", "400.0000")}
    f = fy(ashop)
    ledger = ashop.get("/accounting/reports/party-ledger", party_id=c["id"], date_from=f["start_date"],
                       date_to=f["end_date"])
    assert ledger["closing"] == "2360.00"
    assert ashop.get(f"/parties/{c['id']}/outstanding")["outstanding"] == "2360.0000"
    books_ok(ashop)


def test_cancelled_invoice_is_reversed(ashop):
    p = stocked(ashop, "cx", selling_price="100")
    c = customer(ashop)
    inv = invoice(ashop, [{"product_id": p}], party_id=c["id"])
    ashop.post(f"/documents/{inv['id']}/cancel", {"reason": "wrong item"}, expect=200)
    entries = journal_for(ashop, inv["number"])
    assert len(entries) == 2
    rev = next(e for e in entries if e["reverses_id"])
    assert rev["source_event"] == "cancel"
    assert bal(ashop, "ar") == 0 and bal(ashop, "sales") == 0
    books_ok(ashop)


def test_purchases_itc_landed_cost_rcm_and_supplier_payment(ashop):
    sup = ashop.post("/suppliers", {"name": "Herb Farm", "gstin": KA_GSTIN})
    a = ashop.product("herb-a", purchase_price="100", gst_rate="12")["id"]
    b = ashop.product("herb-b", purchase_price="50", gst_rate="18")["id"]
    pur = finalize(ashop, doc(ashop, "purchase_invoice", [
        {"product_id": a, "quantity": "10"}, {"product_id": b, "quantity": "10", "itc_eligible": False},
    ], party_id=sup["id"], supplier_invoice_no="KA/1", landed_cost="150")["id"])
    pl = lines_by_account(ashop, journal_for(ashop, pur["number"])[0])
    assert pl["inventory"] == ("1740.0000", "0.0000")  # 1100 + 640 (incl. non-claimable tax)
    assert pl["input_igst"] == ("120.0000", "0.0000")
    assert pl["ap"] == ("0.0000", "1710.0000") and pl["landed_cost_clearing"] == ("0.0000", "150.0000")

    pr = ashop.post(f"/documents/{pur['id']}/convert", {"target": "purchase_return"})
    ashop.api.c.patch(f"{ashop.base}/documents/{pr['id']}", headers=ashop.h, json={"lines": [
        {"product_id": a, "quantity": "2", "source_line_id": pur["lines"][0]["id"]}]})
    pr = finalize(ashop, pr["id"])
    rl = lines_by_account(ashop, journal_for(ashop, pr["number"])[0])
    # 2 units at a carrying cost of 110 each go out; the supplier credits 100 each + tax.
    assert rl["inventory"] == ("0.0000", "220.0000") and rl["ap"] == ("224.0000", "0.0000")
    assert rl["stock_adjustment"] == ("20.0000", "0.0000") and rl["input_igst"] == ("0.0000", "24.0000")

    ashop.post("/payments", {"direction": "out", "party_id": sup["id"], "mode": "bank_transfer",
                             "amount": "1486", "allocations": [{"document_id": pur["id"], "amount": "1486"}]})
    f = fy(ashop)
    led = ashop.get("/accounting/reports/party-ledger", party_id=sup["id"], side="payable",
                    date_from=f["start_date"], date_to=f["end_date"])
    assert led["closing"] == "0.00"  # nothing owed: books agree with documents

    adv = ashop.post("/suppliers", {"name": "Advocate", "gst_type": "unregistered", "state_code": "27"})
    svc = ashop.product("legal", unit="HRS", kind="service", purchase_price="10000", gst_rate="18")["id"]
    rcm = finalize(ashop, doc(ashop, "purchase_invoice", [{"product_id": svc}], party_id=adv["id"],
                              supplier_invoice_no="L1", reverse_charge=True)["id"])
    rl = lines_by_account(ashop, journal_for(ashop, rcm["number"])[0])
    assert rl["purchases"] == ("10000.0000", "0.0000") and rl["ap"] == ("0.0000", "10000.0000")
    assert rl["input_cgst"] == ("900.0000", "0.0000") and rl["rcm_cgst"] == ("0.0000", "900.0000")
    books_ok(ashop)


def test_delivery_challan_cogs_once(ashop):
    p = stocked(ashop, "dc", qty="10", cost="7", selling_price="20")
    c = customer(ashop)
    dc = finalize(ashop, doc(ashop, "delivery_challan", [{"product_id": p, "quantity": "4"}], party_id=c["id"])["id"])
    assert lines_by_account(ashop, journal_for(ashop, dc["number"])[0]) == {
        "cogs": ("28.0000", "0.0000"), "inventory": ("0.0000", "28.0000")}
    inv = finalize(ashop, ashop.post(f"/documents/{dc['id']}/convert", {"target": "sales_invoice"})["id"])
    assert "cogs" not in lines_by_account(ashop, journal_for(ashop, inv["number"])[0])
    books_ok(ashop)


@pytest.mark.parametrize("valuation", ["weighted_average", "fifo"])
def test_stock_documents_post_and_reverse(api, owner_headers, valuation):
    s = Shop(api, owner_headers, valuation)
    gd = s.warehouse("GD")
    p = s.product("st")["id"]
    op = s.opening([{"product_id": p, "quantity": "10", "unit_cost": "12"}])
    assert lines_by_account(s, journal_for(s, op["number"])[0]) == {
        "inventory": ("120.0000", "0.0000"), "opening_balance": ("0.0000", "120.0000")}
    loss = s.adjust([{"product_id": p, "quantity": "-2"}], reason="damage")
    assert lines_by_account(s, journal_for(s, loss["number"])[0])["stock_adjustment"] == ("24.0000", "0.0000")
    trf = s.transfer(s.main, gd, [{"product_id": p, "quantity": "3"}])
    assert journal_for(s, trf["number"]) == []  # same Inventory account: no entry
    s.post("/inventory/counts", {"warehouse_id": gd, "doc_date": TODAY,
                                 "lines": [{"product_id": p, "counted_quantity": "4"}]})
    s.cancel(loss["id"])
    assert len(journal_for(s, loss["number"])) == 2
    books_ok(s)


# ---------------------------------------------------------------------------- manual & opening


def test_manual_journal_rules(ashop):
    a = acct(ashop)
    ok = ashop.post("/accounting/journals", {"entry_date": TODAY, "narration": "Capital introduced", "lines": [
        {"account_id": a["bank"]["id"], "debit": "500000"},
        {"account_id": a["capital"]["id"], "credit": "500000"}]})
    assert ok["number"].startswith("JV/") and ok["source_type"] == "manual"
    r = ashop.post("/accounting/journals", {"entry_date": TODAY, "narration": "Bad", "lines": [
        {"account_id": a["bank"]["id"], "debit": "10"}, {"account_id": a["cash"]["id"], "credit": "9"}]},
        expect=422)
    assert r["error"]["code"] == "unbalanced_journal"
    ashop.post("/accounting/journals", {"entry_date": TODAY, "narration": "Sneaky", "lines": [
        {"account_id": a["ar"]["id"], "debit": "10"}, {"account_id": a["cash"]["id"], "credit": "10"}]},
        expect=422)  # control accounts belong to documents
    ashop.post("/accounting/journals", {"entry_date": TODAY, "narration": "Both", "lines": [
        {"account_id": a["bank"]["id"], "debit": "10", "credit": "10"},
        {"account_id": a["cash"]["id"], "credit": "10"}]}, expect=422)
    rev = ashop.post(f"/accounting/journals/{ok['id']}/reverse", {"reason": "entered twice"})
    assert rev["reverses_id"] == ok["id"]
    ashop.post(f"/accounting/journals/{ok['id']}/reverse", {"reason": "again"}, expect=409)
    assert bal(ashop, "bank") == 0

    p = stocked(ashop, "auto", selling_price="10")
    inv = invoice(ashop, [{"product_id": p}], payments=[{"mode": "cash", "amount": "10"}])
    auto = journal_for(ashop, inv["number"])[0]
    ashop.post(f"/accounting/journals/{auto['id']}/reverse", {"reason": "nope"}, expect=422)
    books_ok(ashop)


def test_opening_balances(ashop):
    a = acct(ashop)
    c = customer(ashop, "Old Debtor")
    start = fy(ashop)["start_date"]
    ashop.post("/accounting/opening-balances", {"as_of": TODAY, "lines": [
        {"account_id": a["bank"]["id"], "debit": "1"}]}, expect=422)  # not the FY start
    ashop.post("/accounting/opening-balances", {"as_of": start, "lines": [
        {"account_id": a["ar"]["id"], "debit": "5000"}]}, expect=422)  # party required
    ashop.post("/accounting/opening-balances", {"as_of": start, "lines": [
        {"account_id": a["inventory"]["id"], "debit": "5000"}]}, expect=422)  # use opening stock
    e = ashop.post("/accounting/opening-balances", {"as_of": start, "lines": [
        {"account_id": a["bank"]["id"], "debit": "250000"},
        {"account_id": a["ar"]["id"], "debit": "5000", "party_id": c["id"]},
        {"account_id": a["2300"]["id"], "credit": "100000"}]})
    assert lines_by_account(ashop, e)["opening_balance"] == ("0.0000", "155000.0000")
    led = ashop.get("/accounting/reports/party-ledger", party_id=c["id"], date_from=start, date_to=TODAY)
    assert led["closing"] == "5000.00"
    books_ok(ashop)


# ---------------------------------------------------------------------------- expenses


def test_expenses_with_gst(ashop):
    a = acct(ashop)
    landlord = ashop.post("/suppliers", {"name": "Landlord", "gst_type": "unregistered", "state_code": "27"})
    rent = ashop.post("/accounting/expenses", {"account_id": a["6000"]["id"], "amount": "10000",
                                               "gst_rate": "18", "description": "Shop rent Nov",
                                               "paid_from_account_id": a["bank"]["id"]})
    assert (rent["number"].startswith("EXP/"), rent["cgst"], rent["total"]) == (True, "900.0000", "11800.0000")
    assert bal(ashop, "bank") == D("-11800") and bal(ashop, "input_cgst") == D("900")
    power = ashop.post("/accounting/expenses", {"account_id": a["6020"]["id"], "amount": "1000", "gst_rate": "18",
                                                "itc_eligible": False, "description": "Electricity",
                                                "party_id": landlord["id"]})
    assert bal(ashop, "6020") == D("1180")  # unclaimable GST is part of the cost
    assert ashop.get("/accounting/reports/party-ledger", party_id=landlord["id"], side="payable",
                     date_from=TODAY, date_to=TODAY)["closing"] == "-1180.00"  # credit: we owe
    ashop.post("/accounting/expenses", {"account_id": a["bank"]["id"], "amount": "1",
                                        "description": "x", "paid_from_account_id": a["cash"]["id"]}, expect=422)
    ashop.post("/accounting/expenses", {"account_id": a["6000"]["id"], "amount": "1", "description": "x"},
               expect=422)
    ashop.post(f"/accounting/expenses/{power['id']}/cancel", {"reason": "duplicate"}, expect=200)
    assert bal(ashop, "6020") == 0
    f = fy(ashop)
    pl = ashop.get("/accounting/reports/profit-and-loss", date_from=f["start_date"], date_to=f["end_date"])
    assert [(r["name"], r["amount"]) for r in pl["operating_expenses"]] == [("Rent", "10000.00")]
    books_ok(ashop)


# ---------------------------------------------------------------------------- fixed assets


def test_fixed_assets_depreciation_and_disposal(ashop):
    a = acct(ashop)
    start = date.fromisoformat(fy(ashop)["start_date"])
    asset = ashop.post("/accounting/fixed-assets", {
        "code": "LAP-1", "name": "Billing laptop", "asset_account_id": a["1510"]["id"],
        "acquired_on": start.isoformat(), "cost": "60000", "salvage_value": "6000",
        "method": "straight_line", "useful_life_months": 36,
        "capitalise_from_account_id": a["bank"]["id"]})
    assert bal(ashop, "1510") == D("60000") and bal(ashop, "bank") == D("-60000")
    m1 = start.replace(day=30)  # first month (April has 30 days)
    run = ashop.post("/accounting/fixed-assets/depreciation", {"period_end": m1.isoformat()}, expect=200)
    first = D(lines_by_account(ashop, run)["depreciation"][0])
    assert D("1450") < first < D("1500")  # ≈ 54000 / 36 for a 30-day month
    again = ashop.post("/accounting/fixed-assets/depreciation", {"period_end": m1.isoformat()}, expect=200)
    assert again is None  # already depreciated to that date: never doubled
    row = ashop.get("/accounting/fixed-assets")[0]
    assert D(row["book_value"]) == D("60000") - first
    ashop.post(f"/accounting/fixed-assets/{asset['id']}/dispose",
               {"on": (m1 - timedelta(days=1)).isoformat(), "proceeds": "1"}, expect=422)
    disp = ashop.post(f"/accounting/fixed-assets/{asset['id']}/dispose",
                      {"on": m1.isoformat(), "proceeds": "50000", "proceeds_account_id": a["bank"]["id"]},
                      expect=200)
    dl = lines_by_account(ashop, disp)
    loss = D("60000") - first - D("50000")
    assert dl["asset_disposal"] == (f"{loss:.4f}", "0.0000")
    assert bal(ashop, "1510") == 0 and bal(ashop, "accumulated_depreciation") == 0
    assert ashop.post("/accounting/fixed-assets/depreciation", {"period_end": TODAY}, expect=200) is None
    books_ok(ashop)


def test_wdv_never_below_salvage(ashop):
    from app.modules.accounting.assets import depreciation_for
    from app.modules.accounting.models import DepreciationMethod, FixedAsset

    a = FixedAsset(cost=D("10000"), salvage_value=D("9900"), accumulated_depreciation=D("0"),
                   method=DepreciationMethod.WRITTEN_DOWN, annual_rate=D("40"))
    assert depreciation_for(a, date(2026, 4, 1), date(2027, 3, 31)) == D("100.00")
    b = FixedAsset(cost=D("10000"), salvage_value=D("0"), accumulated_depreciation=D("4000"),
                   method=DepreciationMethod.WRITTEN_DOWN, annual_rate=D("40"))
    assert depreciation_for(b, date(2026, 4, 1), date(2027, 3, 31)) == D("2400.00")  # 40% of 6000


# ---------------------------------------------------------------------------- bank reconciliation


def test_bank_reconciliation(ashop):
    a = acct(ashop)
    bank_id = a["bank"]["id"]
    p = stocked(ashop, "bnk", selling_price="1000")
    c = customer(ashop)
    i1 = invoice(ashop, [{"product_id": p}], party_id=c["id"])
    i2 = invoice(ashop, [{"product_id": p}], party_id=c["id"])
    for i, ref in ((i1, "UTR111"), (i2, "UTR222")):
        ashop.post("/payments", {"direction": "in", "party_id": c["id"], "mode": "upi", "amount": "1000",
                                 "reference": ref, "allocations": [{"document_id": i["id"], "amount": "1000"}]})
    csv_text = ("Date,Narration,Chq/Ref No,Withdrawal,Deposit\n"
                f"{date.today():%d/%m/%Y},UPI/UTR111/clinic,UTR111,,1000.00\n"
                f"{date.today():%d/%m/%Y},UPI/UTR222/clinic,UTR222,,\"1,000.00\"\n"
                f"{date.today():%d/%m/%Y},SMS charges,,17.70,\n")
    out = ashop.post(f"/accounting/bank/{bank_id}/statement", {"csv": csv_text}, expect=200)
    assert out == {"added": 3, "skipped_duplicates": 0, "auto_matched": 2}
    again = ashop.post(f"/accounting/bank/{bank_id}/statement", {"csv": csv_text}, expect=200)
    assert again["added"] == 0 and again["skipped_duplicates"] == 3

    rec = ashop.get(f"/accounting/bank/{bank_id}/reconciliation")
    assert (rec["book_balance"], rec["statement_balance"], rec["difference"]) == ("2000.00", "1982.30", "0.00")
    assert [u["description"] for u in rec["unmatched_statement"]] == ["SMS charges"]
    ashop.post("/accounting/expenses", {"account_id": a["6200"]["id"], "amount": "17.70",
                                        "description": "SMS charges", "paid_from_account_id": bank_id})
    ashop.post(f"/accounting/bank/{bank_id}/auto-match", {}, expect=200)
    rec = ashop.get(f"/accounting/bank/{bank_id}/reconciliation")
    assert rec["unmatched_statement"] == [] and rec["uncleared_book"] == [] and rec["difference"] == "0.00"
    line = ashop.get(f"/accounting/bank/{bank_id}/statement")[0]
    ashop.post(f"/accounting/bank/statement-lines/{line['id']}/unmatch", {}, expect=200)
    ashop.post(f"/accounting/bank/statement-lines/{line['id']}/match",
               {"journal_line_id": line["matched_line_id"]}, expect=200)
    ashop.post(f"/accounting/bank/{a['6200']['id']}/statement", {"lines": []}, expect=422)  # not a bank


# ---------------------------------------------------------------------------- year end


def test_close_and_reopen_financial_year(ashop, api):
    a = acct(ashop)
    p = stocked(ashop, "yr", cost="40", selling_price="100")
    invoice(ashop, [{"product_id": p}], payments=[{"mode": "cash", "amount": "100"}])
    ashop.post("/accounting/expenses", {"account_id": a["6040"]["id"], "amount": "15",
                                        "description": "Tea", "paid_from_account_id": a["cash"]["id"]})
    f = fy(ashop)
    earlier = date.fromisoformat(f["start_date"]) - timedelta(days=10)
    prev = ashop.post("/financial-years", {"containing_date": earlier.isoformat()})
    r = ashop.post("/accounting/close-year", {"financial_year_id": f["id"]}, expect=422)
    assert prev["code"] in r["error"]["message"]  # close the earlier year first
    ashop.post("/accounting/close-year", {"financial_year_id": prev["id"]}, expect=200)

    d = doc(ashop, "sales_invoice", [{"product_id": p}])  # a draft blocks closing
    ashop.post("/accounting/close-year", {"financial_year_id": f["id"]}, expect=422)
    ashop.api.c.delete(f"{ashop.base}/documents/{d['id']}", headers=ashop.h)
    closing = ashop.post("/accounting/close-year", {"financial_year_id": f["id"]}, expect=200)
    assert lines_by_account(ashop, closing)["retained_earnings"] == ("0.0000", "45.0000")  # 100 − 40 − 15

    pl = ashop.get("/accounting/reports/profit-and-loss", date_from=f["start_date"], date_to=f["end_date"])
    assert pl["net_profit"] == "45.00"  # the closing entry does not erase the year's results
    bs = ashop.get("/accounting/reports/balance-sheet", as_of=f["end_date"])
    assert bs["profit_not_yet_closed"] == "0.00"
    assert {r["name"]: r["amount"] for r in bs["equity"]}["Retained Earnings"] == "45.00"
    assert any(x["start_date"] > f["end_date"] for x in ashop.get("/financial-years"))  # next FY exists

    late = doc(ashop, "sales_invoice", [{"product_id": p}])
    r = finalize(ashop, late["id"], [{"mode": "cash", "amount": "100"}], expect=422)
    assert "closed" in r["error"]["message"]
    admin_h, _ = api.member_session(ashop.h, ashop.bid, "admin@example.com", "business_admin")
    ashop.post("/accounting/reopen-year", {"financial_year_id": f["id"], "reason": "late bill"},
               expect=403, headers=admin_h)
    ashop.post("/accounting/reopen-year", {"financial_year_id": f["id"], "reason": "late bill"}, expect=200)
    assert bal(ashop, "retained_earnings") == 0
    finalize(ashop, late["id"], [{"mode": "cash", "amount": "100"}])  # the late bill, now allowed
    again = ashop.post("/accounting/close-year", {"financial_year_id": f["id"]}, expect=200)
    # 45 from before + the late sale (100 − 40 cost of goods)
    assert lines_by_account(ashop, again)["retained_earnings"] == ("0.0000", "105.0000")
    books_ok(ashop)


# ---------------------------------------------------------------------------- reports


def test_aging_cash_flow_and_statements(ashop):
    a = acct(ashop)
    f = fy(ashop)
    p = stocked(ashop, "ag", selling_price="1000")
    c = customer(ashop, "Slow Payer")
    old_date = max(date.fromisoformat(f["start_date"]), date.today() - timedelta(days=75))
    inv_old = invoice(ashop, [{"product_id": p}], party_id=c["id"], doc_date=old_date.isoformat(),
                      due_date=old_date.isoformat())
    invoice(ashop, [{"product_id": p}], party_id=c["id"])
    ashop.post("/payments", {"direction": "in", "party_id": c["id"], "mode": "cash", "amount": "300",
                             "allocations": []})
    ag = ashop.get("/accounting/reports/aging", side="receivable")
    row = ag["parties"][0]
    days = (date.today() - old_date).days
    bucket = "61_90" if days > 60 else "31_60" if days > 30 else "1_30" if days > 0 else "not_due"
    assert row[bucket] == "1000.00" and row["not_due"] == "1000.00" and row["credits"] == "300.00"
    assert row["net"] == "1700.00"
    books = ashop.get("/accounting/reports/party-ledger", party_id=c["id"], date_from=f["start_date"],
                      date_to=TODAY)["closing"]
    assert books == "1700.00"  # documents and books agree
    assert inv_old["due_date"] == old_date.isoformat()

    ashop.post("/accounting/journals", {"entry_date": TODAY, "narration": "Capital", "lines": [
        {"account_id": a["bank"]["id"], "debit": "100000"}, {"account_id": a["capital"]["id"], "credit": "100000"}]})
    ashop.post("/accounting/fixed-assets", {"code": "F1", "name": "Shelf", "asset_account_id": a["fixed_assets"]["id"],
                                            "acquired_on": TODAY, "cost": "20000", "method": "written_down",
                                            "annual_rate": "10", "capitalise_from_account_id": a["bank"]["id"]})
    cf = ashop.get("/accounting/reports/cash-flow", date_from=f["start_date"], date_to=f["end_date"])
    assert (cf["net_operating"], cf["net_investing"], cf["net_financing"]) == ("300.00", "-20000.00", "100000.00")
    assert cf["closing_cash"] == "80300.00"
    day = ashop.get("/accounting/reports/day-book", date_from=TODAY, date_to=TODAY)
    assert all(sum(D(ln["debit"]) for ln in e["lines"]) == sum(D(ln["credit"]) for ln in e["lines"]) for e in day)
    cash = ashop.get("/accounting/reports/cash-book", date_from=TODAY, date_to=TODAY)
    assert cash["closing"] == "300.00"
    books_ok(ashop)


def test_accounting_permissions(ashop, api):
    op_h, _ = api.member_session(ashop.h, ashop.bid, "op@example.com", "billing_operator")
    acc_h, _ = api.member_session(ashop.h, ashop.bid, "acc@example.com", "accountant")
    sales_h, _ = api.member_session(ashop.h, ashop.bid, "se@example.com", "sales_executive")
    f = fy(ashop)
    q = {"date_from": f["start_date"], "date_to": f["end_date"]}
    assert api.c.get(f"{ashop.base}/accounting/accounts", headers=op_h).status_code == 403
    assert api.c.get(f"{ashop.base}/accounting/reports/profit-and-loss", headers=sales_h, params=q).status_code == 403
    assert api.c.get(f"{ashop.base}/accounting/reports/profit-and-loss", headers=acc_h, params=q).status_code == 200
    a = acct(ashop)
    ashop.post("/accounting/journals", {"entry_date": TODAY, "narration": "Contra deposit", "lines": [
        {"account_id": a["bank"]["id"], "debit": "10"}, {"account_id": a["cash"]["id"], "credit": "10"}]},
        headers=acc_h)
    ashop.post("/accounting/journals", {"entry_date": TODAY, "narration": "Contra deposit", "lines": [
        {"account_id": a["bank"]["id"], "debit": "10"}, {"account_id": a["cash"]["id"], "credit": "10"}]},
        headers=op_h, expect=403)


def test_backfill_posts_missing_journals_idempotently(ashop, db):
    import uuid

    from sqlalchemy import delete, select

    from app.modules.accounting.backfill import backfill
    from app.modules.accounting.models import JournalEntry, JournalLine
    from app.modules.businesses.models import Business

    p = stocked(ashop, "bf", selling_price="50")
    c = customer(ashop)
    inv = invoice(ashop, [{"product_id": p}], party_id=c["id"], payments=[{"mode": "cash", "amount": "20"}])
    cancelled = invoice(ashop, [{"product_id": p}], party_id=c["id"])
    ashop.post(f"/documents/{cancelled['id']}/cancel", {"reason": "void"}, expect=200)
    bid = uuid.UUID(ashop.bid)
    # Simulate data recorded before accounting existed: drop every journal.
    db.execute(delete(JournalLine).where(JournalLine.business_id == bid))
    db.execute(delete(JournalEntry).where(JournalEntry.business_id == bid,
                                          JournalEntry.reverses_id.is_not(None)))
    db.execute(delete(JournalEntry).where(JournalEntry.business_id == bid))
    db.commit()
    business = db.get(Business, bid)
    r = backfill(db, business)
    db.commit()
    # opening stock, two invoices, one payment; plus the cancelled invoice's reversal
    assert (r.posted, r.reversed, r.skipped) == (4, 1, [])
    r2 = backfill(db, business)
    assert (r2.posted, r2.reversed) == (0, 0)
    assert db.scalar(select(JournalEntry.id).where(JournalEntry.source_ref == inv["number"])) is not None
    books_ok(ashop)
