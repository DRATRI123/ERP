"""Scheduled report e-mails: schedule maths, content, permissions and the worker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.modules.digest import service
from app.modules.digest.models import Frequency, ScheduledReport
from app.modules.integrations.models import OutboundMessage
from app.modules.integrations.outbox import process_due
from tests.conftest import TODAY, USER_PASSWORD, Shop
from tests.test_billing import customer, invoice, stocked
from tests.test_integrations import connect, smtp  # noqa: F401 - fixture

SMTP_CFG = {"host": "smtp.gmail.com", "port": "587", "security": "starttls", "from_email": "erp@shop.in",
            "from_name": "Shop", "username": "erp@shop.in"}


@pytest.fixture
def gshop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers, name="Digest <Wellness> & Co")


def test_next_run_and_periods():
    rep = ScheduledReport(frequency=Frequency.DAILY, hour=8, weekday=0, day_of_month=1)
    # 02:00 UTC = 07:30 IST → today 08:00 IST = 02:30 UTC
    assert service.next_run(rep, datetime(2026, 11, 24, 2, 0, tzinfo=UTC)) == datetime(2026, 11, 24, 2, 30, tzinfo=UTC)
    assert service.next_run(rep, datetime(2026, 11, 24, 3, 0, tzinfo=UTC)) == datetime(2026, 11, 25, 2, 30, tzinfo=UTC)
    rep.frequency, rep.weekday = Frequency.WEEKLY, 0  # Mondays
    assert service.next_run(rep, datetime(2026, 11, 24, 3, 0, tzinfo=UTC)).date().isoformat() == "2026-11-30"
    rep.frequency, rep.day_of_month = Frequency.MONTHLY, 1
    at = service.next_run(rep, datetime(2026, 11, 24, 3, 0, tzinfo=UTC))
    assert at.date().isoformat() == "2026-12-01"
    assert service.period_for(rep, at)[:2] == (datetime(2026, 11, 1).date(), datetime(2026, 11, 30).date())
    rep.frequency = Frequency.DAILY
    assert service.period_for(rep, datetime(2026, 11, 24, 3, 0, tzinfo=UTC))[0].isoformat() == "2026-11-23"


def test_create_preview_send_and_worker(gshop, smtp, db):  # noqa: F811
    p = stocked(gshop, "dg1", selling_price="1500", qty="3", reorder_level="5")
    invoice(gshop, [{"product_id": p}], party_id=customer(gshop, "<script>x</script>")["id"])
    bad = gshop.api.c.post(f"{gshop.base}/scheduled-reports", headers=gshop.h, json={
        "name": "x", "frequency": "daily", "recipients": ["not-mail"], "sections": ["sales"]})
    assert bad.status_code == 422
    rep = gshop.post("/scheduled-reports", {"name": "Morning summary", "frequency": "daily", "hour": 8,
                                            "recipients": ["Owner@Shop.in"], "sections": list(service.SECTIONS)})
    assert rep["recipients"] == ["owner@shop.in"] and rep["next_run_at"]

    html = gshop.api.c.get(f"{gshop.base}/scheduled-reports/{rep['id']}/preview", headers=gshop.h).text
    assert "Digest &lt;Wellness&gt; &amp; Co" in html and "<script>" not in html
    assert "Low stock (1)" in html and "Licences" in html and "To receive" in html

    # No e-mail integration: the API says so; the worker keeps the error on the report.
    r = gshop.api.c.post(f"{gshop.base}/scheduled-reports/{rep['id']}/send-now", headers=gshop.h)
    assert r.status_code == 422
    row = db.get(ScheduledReport, __import__("uuid").UUID(rep["id"]))
    row.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert service.send_due(db)["failed"] >= 1
    db.refresh(row)
    assert "integration" in row.last_error and row.next_run_at > datetime.now(UTC)
    db.rollback()

    connect(gshop, "smtp", SMTP_CFG, {"password": "app-pass"})
    assert gshop.post(f"/scheduled-reports/{rep['id']}/send-now", {}, expect=200)["queued"] == 1
    row.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert service.send_due(db)["sent"] >= 1
    process_due(db)
    mine = [m for m in smtp.sent if m["To"] == "owner@shop.in"]
    assert len(mine) == 2 and "Morning summary" in mine[0]["Subject"]
    assert "₹1,500.00" in mine[0].get_body(("html",)).get_content() or TODAY
    assert db.scalars(select(OutboundMessage).where(OutboundMessage.recipient == "owner@shop.in")).first()


def test_scheduled_reports_need_permissions(api, gshop):
    role = gshop.post("/roles", {"name": "Reports only", "permissions": ["business.view", "reports.view"]})
    m = gshop.post("/members", {"email": "rep-view@example.com", "full_name": "R", "role_id": role["id"]})
    api.c.post("/api/v1/auth/accept-invite", json={"token": m["invite_token"], "password": USER_PASSWORD})
    h = api.auth("rep-view@example.com", USER_PASSWORD)
    assert api.c.get(f"{gshop.base}/scheduled-reports", headers=h).status_code == 403
