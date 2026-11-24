"""Staff, attendance, salary runs, commission and their books; expense claims and petty cash."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import TODAY, USER_PASSWORD, Shop
from tests.test_accounting import acct, books_ok
from tests.test_billing import customer, doc, finalize, stocked

T = date.fromisoformat(TODAY)
D = Decimal
FIRST = T.replace(day=1)


@pytest.fixture
def pshop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def member(api, s: Shop, email: str, perms: list[str]) -> dict:
    role = s.post("/roles", {"name": email.split("@")[0], "permissions": perms})
    m = s.post("/members", {"email": email, "full_name": email.split("@")[0].title(), "role_id": role["id"]})
    api.c.post("/api/v1/auth/accept-invite", json={"token": m["invite_token"], "password": USER_PASSWORD})
    return {"h": api.auth(email, USER_PASSWORD), "user_id": m["user"]["id"] if "user" in m else m.get("user_id")}


def test_salary_run_with_attendance_commission_and_books(api, pshop):
    import calendar

    days = calendar.monthrange(FIRST.year, FIRST.month)[1]
    member(api, pshop, "rep@example.com", ["business.view", "invoices.view", "invoices.create", "products.view",
                                                  "stock.view", "customers.view"])
    rep_user = next(x["user"]["id"] for x in pshop.get("/members")["items"] if x["user"]["email"] == "rep@example.com")
    asha = pshop.post("/payroll/employees", {"code": "e1", "name": "Asha", "joining_date": "2020-01-01",
                                             "monthly_salary": str(days * 1000), "commission_percent": "2", "user_id": rep_user})
    assert asha["code"] == "E1"
    ravi = pshop.post("/payroll/employees", {"code": "E2", "name": "Ravi", "joining_date": FIRST.isoformat(),
                                             "pay_type": "daily", "daily_wage": "500"})
    bad = pshop.api.c.post(f"{pshop.base}/payroll/employees", headers=pshop.h, json={"code": "E3", "name": "x", "joining_date": TODAY})
    assert bad.status_code == 422  # no salary

    # Asha: one absence, one half day. Ravi: two absences.
    pshop.api.c.put(f"{pshop.base}/payroll/attendance", headers=pshop.h, json={"day": FIRST.isoformat(), "entries": [
        {"employee_id": asha["id"], "status": "absent"}, {"employee_id": ravi["id"], "status": "absent"}]})
    pshop.api.c.put(f"{pshop.base}/payroll/attendance", headers=pshop.h, json={"day": (FIRST + timedelta(days=1)).isoformat(), "entries": [
        {"employee_id": asha["id"], "status": "half_day"}, {"employee_id": ravi["id"], "status": "absent"}]})
    att = pshop.get("/payroll/attendance", month=FIRST.isoformat())
    assert att["marks"][asha["id"]][FIRST.isoformat()] == "absent"

    # Asha sold 10,000 before GST this month → 2 % commission.
    p = stocked(pshop, "pay1", selling_price="10000", gst_rate="12")
    finalize(pshop, doc(pshop, "sales_invoice", [{"product_id": p}], party_id=customer(pshop)["id"],
                        salesperson_id=rep_user, doc_date=FIRST.isoformat())["id"])

    run = pshop.post("/payroll/runs", {"month": FIRST.isoformat()})
    lines = {ln["employee_name"]: ln for ln in run["lines"]}
    a, r = lines["Asha"], lines["Ravi"]
    assert D(a["paid_days"]) == days - D("1.5") and D(a["base"]) == (days - D("1.5")) * 1000
    assert D(a["sales"]) == 10000 and D(a["commission"]) == 200
    assert D(r["paid_days"]) == days - 2 and D(r["base"]) == (days - 2) * 500
    run = pshop.api.c.patch(f"{pshop.base}/payroll/runs/{run['id']}/lines/{ravi['id']}", headers=pshop.h,
                            json={"bonus": "1000", "deductions": "2000", "note": "Advance recovered"}).json()
    assert D(run["deductions"]) == 2000
    assert pshop.api.c.post(f"{pshop.base}/payroll/runs", headers=pshop.h, json={"month": FIRST.isoformat()}).status_code == 409

    posted = pshop.post(f"/payroll/runs/{run['id']}/post", {}, expect=200)
    assert posted["status"] == "posted"
    assert pshop.api.c.patch(f"{pshop.base}/payroll/runs/{run['id']}/lines/{ravi['id']}", headers=pshop.h,
                             json={"bonus": "1"}).status_code == 409
    paid = pshop.post(f"/payroll/runs/{run['id']}/pay", {"paid_from_account_id": acct(pshop)["bank"]["id"]}, expect=200)
    assert paid["status"] == "paid"
    books_ok(pshop)


def test_payroll_permissions(api, pshop):
    e = pshop.post("/payroll/employees", {"code": "E9", "name": "Meena", "joining_date": "2021-04-01", "monthly_salary": "18000",
                                         "bank_account": "123456789012", "ifsc": "SBIN0001234"})
    marker = member(api, pshop, "marker@example.com", ["business.view", "attendance.mark"])
    rows = pshop.get("/payroll/employees", headers=marker["h"])
    assert rows[0]["name"] == "Meena" and D(rows[0]["monthly_salary"]) == 0 and rows[0]["bank_account"] is None
    assert api.c.put(f"{pshop.base}/payroll/attendance", headers=marker["h"],
                     json={"day": TODAY, "entries": [{"employee_id": e["id"], "status": "leave"}]}).status_code == 200
    assert api.c.get(f"{pshop.base}/payroll/runs", headers=marker["h"]).status_code == 403
    assert api.c.put(f"{pshop.base}/payroll/attendance", headers=marker["h"],
                     json={"day": "2019-01-01", "entries": [{"employee_id": e["id"], "status": "present"}]}).status_code == 422


def test_expense_claims_and_petty_cash(api, pshop):
    staff = member(api, pshop, "claimer@example.com", ["business.view", "expenses.claim"])
    other = member(api, pshop, "other-claimer@example.com", ["business.view", "expenses.claim"])
    travel = acct(pshop)["6050"]["id"]
    c = pshop.post("/claims", {"expense_date": TODAY, "account_id": travel, "amount": "450", "description": "Auto to supplier"},
                   headers=staff["h"])
    assert c["status"] == "submitted" and c["claimant_name"] == "Claimer"
    assert pshop.get("/claims", headers=other["h"])["total"] == 0  # own claims only
    assert api.c.post(f"{pshop.base}/claims/{c['id']}/approve", headers=staff["h"], json={}).status_code == 403
    bad = api.c.post(f"{pshop.base}/claims", headers=staff["h"], json={"expense_date": TODAY, "account_id": acct(pshop)["cash"]["id"],
                                                                       "amount": "10", "description": "cash?"})
    assert bad.status_code == 422  # not an expense head

    pc = pshop.get("/petty-cash")
    assert D(pc["balance"]) == 0 and D(pc["claims_waiting"]) == 450
    pc = pshop.post("/petty-cash/top-up", {"from_account_id": acct(pshop)["bank"]["id"], "amount": "2000"}, expect=200)
    assert D(pc["balance"]) == 2000

    approved = pshop.post(f"/claims/{c['id']}/approve", {"note": "OK", "paid_from_account_id": pc["account_id"]}, expect=200)
    assert approved["status"] == "paid" and approved["expense_id"]
    assert D(pshop.get("/petty-cash")["balance"]) == 1550
    r = pshop.post("/claims", {"expense_date": TODAY, "account_id": travel, "amount": "99", "description": "Snacks"}, headers=staff["h"])
    assert pshop.post(f"/claims/{r['id']}/reject", {"note": "Not a business expense"}, expect=200)["status"] == "rejected"
    assert api.c.post(f"{pshop.base}/claims/{r['id']}/pay", headers=pshop.h,
                      json={"paid_from_account_id": pc["account_id"]}).status_code == 409
    books_ok(pshop)
