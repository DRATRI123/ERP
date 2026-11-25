"""Budgets vs actual, Income-tax depreciation blocks, daily cash closing, balance confirmations."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from tests.conftest import TODAY, Shop
from tests.test_accounting import acct, bal, books_ok, fy
from tests.test_billing import customer, doc, finalize

T = date.fromisoformat(TODAY)


@pytest.fixture
def fs(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def test_budget_vs_actual(fs):
    a = acct(fs)
    f = fy(fs)
    r = fs.api.c.put(f"{fs.base}/budgets", headers=fs.h, json={"account_id": a["6000"]["id"], "fy_start": f["start_date"], "months": ["10000"] * 12})
    assert r.status_code == 200, r.text
    bad = fs.api.c.put(f"{fs.base}/budgets", headers=fs.h, json={"account_id": a["bank"]["id"], "fy_start": f["start_date"], "months": ["1"] * 12})
    assert bad.status_code == 422
    fs.post("/accounting/journals", {"entry_date": TODAY, "narration": "Rent", "lines": [
        {"account_id": a["6000"]["id"], "debit": "12500"}, {"account_id": a["bank"]["id"], "credit": "12500"}]})
    m = T.replace(day=1)
    end = (m + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    rep = fs.get("/budgets/report", date_from=m.isoformat(), date_to=end.isoformat())
    row = rep["rows"][0]
    assert (row["name"], D(row["budget"]), D(row["actual"]), D(row["variance"]), row["favourable"]) == ("Rent", D(10000), D(12500), D(2500), False)
    assert row["used_percent"] == 125.0
    grid = fs.get("/budgets", fy_start=f["start_date"])
    assert len(grid["months"]) == 12 and len(grid["budgets"][a["6000"]["id"]]) == 12


def test_it_depreciation_blocks(fs):
    a = acct(fs)
    f = fy(fs)
    start, end = date.fromisoformat(f["start_date"]), date.fromisoformat(f["end_date"])
    fs.api.c.put(f"{fs.base}/assets/it-opening-wdv", headers=fs.h, json={"fy_start": f["start_date"], "blocks": {"computer": "100000"}})
    common = {"asset_account_id": a["1510"]["id"], "method": "straight_line", "useful_life_months": 36}
    fs.post("/accounting/fixed-assets", {**common, "code": "C1", "name": "Server", "acquired_on": start.isoformat(), "cost": "50000", "it_block": "computer"})
    fs.post("/accounting/fixed-assets", {**common, "code": "C2", "name": "Laptop", "acquired_on": (end - timedelta(days=30)).isoformat(), "cost": "40000", "it_block": "computer"})
    bad = fs.api.c.post(f"{fs.base}/accounting/fixed-assets", headers=fs.h, json={**common, "code": "X", "name": "X", "acquired_on": TODAY, "cost": "1", "it_block": "spaceship"})
    assert bad.status_code == 422
    r = fs.get("/assets/it-depreciation", fy_start=f["start_date"])
    comp = next(b for b in r["blocks"] if b["block"] == "computer")
    # 40% on (1,00,000 + 50,000) + 20% on 40,000 used under 180 days.
    assert D(comp["depreciation"]) == D(68000) and D(comp["closing_wdv"]) == D(122000)
    assert D(comp["additions_under_180"]) == D(40000)


def test_cash_closing(fs):
    finalize(fs, doc(fs, "sales_invoice", [{"description": "Kit", "quantity": "1", "unit_price": "1000", "gst_rate": "0"}])["id"],
             [{"mode": "cash", "amount": "1000"}])
    pos = fs.get("/cash-closing/position", on=TODAY)
    assert D(pos["expected"]) == D(1000) and pos["closed"] is None
    out = fs.post("/cash-closing", {"denominations": {"500": 1, "200": 2, "50": 1, "coins": "40"}, "notes": "Evening"})
    assert (D(out["counted"]), D(out["difference"])) == (D(990), D(-10))
    assert bal(fs, "cash") == D(990) and bal(fs, "cash_shortage") == D(10)
    assert fs.api.c.post(f"{fs.base}/cash-closing", headers=fs.h, json={"denominations": {}}).status_code == 409
    assert fs.api.c.post(f"{fs.base}/cash-closing", headers=fs.h, json={"closing_date": (T - timedelta(days=1)).isoformat(),
                                                                       "denominations": {"3": 1}}).status_code == 422
    assert D(fs.get("/cash-closing/position", on=TODAY)["closed"]["difference"]) == D(-10)
    assert len(fs.get("/cash-closing", date_from=TODAY, date_to=TODAY)) == 1
    books_ok(fs)


def test_balance_confirmation_link(fs, api):
    c = customer(fs, "Shree Medical")
    finalize(fs, doc(fs, "sales_invoice", [{"description": "Kit", "quantity": "1", "unit_price": "2500", "gst_rate": "0"}], party_id=c["id"])["id"])
    conf = fs.post("/balance-confirmations", {"party_id": c["id"]})
    assert D(conf["balance"]) == D(2500) and conf["status"] == "sent" and len(conf["token"]) >= 20
    pub = api.c.get(f"/api/v1/public/confirm/{conf['token']}")  # no login
    assert pub.status_code == 200 and pub.json()["statement"]["party"] == "Shree Medical"
    assert len(pub.json()["statement"]["entries"]) == 1
    assert api.c.post(f"/api/v1/public/confirm/{conf['token']}", json={"agree": False}).status_code == 422
    ok = api.c.post(f"/api/v1/public/confirm/{conf['token']}", json={"agree": False, "their_balance": "2000", "note": "We paid 500 by UPI"})
    assert ok.status_code == 200 and ok.json()["status"] == "disputed"
    assert api.c.post(f"/api/v1/public/confirm/{conf['token']}", json={"agree": True}).status_code == 409
    row = fs.get("/balance-confirmations", party_id=c["id"])[0]
    assert (row["status"], D(row["their_balance"]), row["response_note"]) == ("disputed", D(2000), "We paid 500 by UPI")
    assert api.c.get("/api/v1/public/confirm/" + "x" * 30).status_code == 404
