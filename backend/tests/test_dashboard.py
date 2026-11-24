"""Dashboard figures: computed in SQL, permission- and branch-aware."""

from __future__ import annotations

from decimal import Decimal

from tests.conftest import Shop
from tests.test_billing import customer, invoice, stocked


def test_business_dashboard_figures(api, owner_headers):
    s = Shop(api, owner_headers)
    p = stocked(s, "brahmi", qty="10", cost="40", selling_price="100", reorder_level="12")
    c = customer(s, "Dhanvantari Clinic")
    paid = invoice(s, [{"product_id": p, "quantity": "2"}], party_id=c["id"],
                   payments=[{"mode": "upi", "amount": "200"}])
    credit = invoice(s, [{"product_id": p, "quantity": "3"}], party_id=c["id"])
    d = s.get("/dashboard")
    total = Decimal(paid["grand_total"]) + Decimal(credit["grand_total"])
    assert Decimal(d["sales"]["today"]) == total == Decimal(d["sales"]["month"])
    assert d["sales"]["invoices_today"] == 2 and d["sales"]["open_invoices"] == 1
    assert Decimal(d["sales"]["receivable"]) == Decimal(credit["grand_total"])
    assert d["top_customers"][0]["name"] == "Dhanvantari Clinic"
    assert len(d["daily"]) == 30 and Decimal(d["daily"][-1]["sales"]) == total and d["daily"][-1]["invoices"] == 2
    assert Decimal(d["payments_today"]["received"]) == Decimal("200")
    assert {r["number"] for r in d["recent"]} == {paid["number"], credit["number"]}
    assert Decimal(d["profit"]["revenue"]) > 0 and Decimal(d["profit"]["net_profit"]) > 0
    # 5 left of 10 with a reorder level of 12: a stock alert.
    assert d["stock"]["low_stock_count"] == 1 and d["stock"]["low_stock"][0]["sku"] == "brahmi"

    ov = api.c.get("/api/v1/dashboard/overview", headers=owner_headers).json()
    row = next(r for r in ov if r["business_id"] == s.bid)
    assert Decimal(row["sales_month"]) == total and Decimal(row["receivable"]) == Decimal(credit["grand_total"])


def test_dashboard_sections_follow_permissions(api, owner_headers):
    s = Shop(api, owner_headers)
    stocked(s, "neem", selling_price="50")
    h, _ = api.member_session(owner_headers, s.bid, "stock@example.com", "inventory_manager")
    d = s.get("/dashboard", headers=h)
    assert "stock" in d and "sales" not in d and "profit" not in d and "top_customers" not in d
    assert all(r["doc_type"].startswith("purchase") for r in d["recent"])
    assert "purchases" in d  # inventory managers see purchases
    ov = api.c.get("/api/v1/dashboard/overview", headers=h).json()
    assert [r["business_id"] for r in ov] == [s.bid] and "sales_month" not in ov[0]


def test_owner_combined_view(api, owner_headers):
    from decimal import Decimal

    from tests.conftest import USER_PASSWORD, Shop
    from tests.test_billing import customer, invoice, stocked

    a, b = Shop(api, owner_headers, name="Combined A"), Shop(api, owner_headers, name="Combined B")
    pa = stocked(a, "ca", selling_price="1000")
    invoice(a, [{"product_id": pa, "quantity": "2"}], payments=[{"mode": "cash", "amount": "2000"}])
    pb = stocked(b, "cb", selling_price="500")
    invoice(b, [{"product_id": pb}], party_id=customer(b)["id"])
    out = api.c.get("/api/v1/dashboard/combined", headers=owner_headers, params={"period": "month"}).json()
    rows = {r["name"]: r for r in out["businesses"]}
    ra, rb = rows["Combined A"], rows["Combined B"]
    assert Decimal(ra["sales"]) == 2000 and Decimal(ra["cash"]) >= 2000 and Decimal(ra["stock_value"]) > 0
    assert Decimal(rb["sales"]) == 500 and Decimal(rb["receivable"]) == 500 and len(rb["trend"]) == 6
    months = [t["month"] for t in rb["trend"]]
    assert len({m[:7] for m in months}) == 6 and months == sorted(months)  # six distinct calendar months
    assert Decimal(out["totals"]["sales"]) == sum(Decimal(r["sales"]) for r in out["businesses"])
    assert Decimal(out["totals"]["stock_value"]) == sum(Decimal(r["stock_value"]) for r in out["businesses"])
    assert api.c.get("/api/v1/dashboard/combined", headers=owner_headers, params={"period": "week"}).status_code == 422

    m = a.post("/members", {"email": "combo@example.com", "full_name": "C", "role_id": a.post(
        "/roles", {"name": "Viewer", "permissions": ["business.view", "invoices.view"]})["id"]})
    api.c.post("/api/v1/auth/accept-invite", json={"token": m["invite_token"], "password": USER_PASSWORD})
    assert api.c.get("/api/v1/dashboard/combined", headers=api.auth("combo@example.com", USER_PASSWORD)).status_code == 403
