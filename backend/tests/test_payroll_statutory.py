"""PF, ESI, professional tax and TDS on salary; posting, ECR file and payslips."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from app.modules.payroll.statutory import annual_tax, professional_tax
from tests.conftest import TODAY, Shop
from tests.test_accounting import bal, books_ok

T = date.fromisoformat(TODAY)
FIRST = T.replace(day=1)


@pytest.fixture
def ps(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def test_rules():
    assert annual_tax(D(1275000)) == 0  # up to ₹12 lakh after standard deduction: rebate
    assert annual_tax(D(1500000)) == D(97500)
    assert professional_tax("27", D(9000), date(2026, 5, 1)) == 175
    assert professional_tax("27", D(30000), date(2027, 2, 1)) == 300
    assert professional_tax("24", D(11999), FIRST) == 0 and professional_tax("24", D(12000), FIRST) == 200
    assert professional_tax("07", D(90000), FIRST) == 0


def test_salary_run_with_statutory_deductions(ps):
    base = {"joining_date": "2020-01-01", "pay_type": "monthly", "pt_state": "24"}
    asha = ps.post("/payroll/employees", {**base, "code": "E1", "name": "Asha", "monthly_salary": "30000", "pf_enabled": True,
                                          "uan": "100200300400", "basic_percent": "50"})
    ravi = ps.post("/payroll/employees", {**base, "code": "E2", "name": "Ravi", "monthly_salary": "18000", "pf_enabled": True,
                                          "esi_enabled": True, "esi_number": "3100123456"})
    mgr = ps.post("/payroll/employees", {**base, "code": "E3", "name": "Manager", "monthly_salary": "125000", "tds_enabled": True,
                                         "pan": "ABCDE1234F"})
    run = ps.post("/payroll/runs", {"month": FIRST.isoformat()})
    lines = {ln["employee_name"]: ln for ln in run["lines"]}
    a, r, m = lines["Asha"], lines["Ravi"], lines["Manager"]
    # Asha: PF wage 50% of 30,000 = 15,000 -> 1,800 each side; above the ESI limit; PT 200.
    assert (D(a["pf_employee"]), D(a["pf_employer"]), D(a["esi_employee"]), D(a["pt"]), D(a["net"])) == (D(1800), D(1800), D(0), D(200), D(28000))
    # Ravi: PF on 18,000 capped at 15,000; ESI 0.75% / 3.25% (rounded up); PT 200.
    assert (D(r["pf_employee"]), D(r["esi_employee"]), D(r["esi_employer"]), D(r["net"])) == (D(1800), D(135), D(585), D(15865))
    # Manager: 15 lakh a year -> 97,500 tax -> 8,125 a month.
    assert D(m["tds"]) == D(8125) and D(m["pf_employee"]) == 0

    # A manual TDS figure (e.g. after investment proofs) overrides the calculation.
    upd = ps.api.c.patch(f"{ps.base}/payroll/runs/{run['id']}/lines/{mgr['id']}", headers=ps.h, json={"tds": "5000"})
    assert upd.status_code == 200 and D(next(x for x in upd.json()["lines"] if x["employee_name"] == "Manager")["tds"]) == D(5000)

    st = ps.get(f"/payroll/runs/{run['id']}/statutory")
    assert D(st["pf"]["employee"]) == D(3600) and D(st["pf"]["eps"]) == D(2500) and D(st["pf"]["epf_employer"]) == D(1100)
    assert D(st["esi"]["employer"]) == D(585) and D(st["pt"]["total"]) == D(600) and D(st["tds"]["total"]) == D(5000)
    ecr = ps.api.c.get(f"{ps.base}/payroll/runs/{run['id']}/ecr.txt", headers=ps.h).text.splitlines()
    assert ecr[0] == "100200300400#~#ASHA#~#30000#~#15000#~#15000#~#15000#~#1800#~#1250#~#550#~#0#~#0"
    slip = ps.api.c.get(f"{ps.base}/payroll/runs/{run['id']}/payslips/{ravi['id']}", headers=ps.h).text
    assert "Payslip" in slip and "15,865.00" in slip and "Provident fund" in slip

    ps.post(f"/payroll/runs/{run['id']}/post", {}, expect=200)
    assert bal(ps, "pf_payable") == D(-7200) and bal(ps, "esi_payable") == D(-720)
    assert bal(ps, "pt_payable") == D(-600) and bal(ps, "tds_payable") == D(-5000)
    assert bal(ps, "employer_contributions") == D(3600 + 585)
    books_ok(ps)
    assert asha["pf_enabled"] is True
