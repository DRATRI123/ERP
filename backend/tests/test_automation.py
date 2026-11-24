"""Automation rules: matching, conditions, delays with re-checks, actions and their failures."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.modules.automation import service as automation
from app.modules.automation.models import AutomationRun
from app.modules.developer.webhooks import emit_overdue
from app.modules.integrations.outbox import process_due
from tests.conftest import USER_PASSWORD, VALID_GSTIN, Shop
from tests.test_billing import customer, invoice, stocked
from tests.test_integrations import connect, smtp  # noqa: F401 - fixture

SMTP_CFG = {"host": "smtp.gmail.com", "port": "587", "security": "starttls", "from_email": "billing@shop.in",
            "from_name": "Shop", "username": "billing@shop.in"}


@pytest.fixture
def ashop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers, gstin=VALID_GSTIN, state_code="27")


def rule(s: Shop, expect: int = 201, **kw):
    body = {"name": "Rule", "trigger": "invoice.created", "action": "notify_email",
            "action_config": {"to": ["owner@shop.in"], "subject": "New sale {number}", "body": "Total {grand_total}"},
            **kw}
    return s.post("/automation/rules", body, expect=expect)


def runs(s: Shop, **params):
    return s.get("/automation/runs", **params)["items"]


def tick(db, now=None):
    automation.dispatch(db)
    return automation.execute_due(db, now=now)


def test_catalog_and_validation(ashop):
    cat = ashop.get("/automation/catalog")
    by = {t["type"]: t for t in cat["triggers"]}
    assert "send_document" in by["invoice.overdue"]["actions"] and "send_document" not in by["stock.low"]["actions"]
    assert {f["key"] for f in by["invoice.created"]["fields"]} >= {"grand_total", "party_name"}
    bad = [
        ({"trigger": "invoice.exploded"}, "Unknown trigger"),
        ({"conditions": [{"field": "password", "op": "eq", "value": "x"}]}, "cannot be checked"),
        ({"conditions": [{"field": "grand_total", "op": "gte", "value": "lots"}]}, "needs a number"),
        ({"trigger": "stock.low", "action": "send_document", "action_config": {"channel": "email"}}, "invoice triggers"),
        ({"action_config": {"to": ["not-an-email"], "subject": "s", "body": "b"}}, "not an e-mail"),
        ({"action_config": {"to": ["a@b.in"], "subject": "{__class__}", "body": "b"}}, "not available"),
        ({"delay_minutes": 60 * 24 * 365}, "90 days"),
        ({"conditions": [{"field": "number", "op": "eq", "value": " "}]}, "Give a value"),
    ]
    for kw, msg in bad:
        r = rule(ashop, expect=422, **kw)
        assert msg in r["error"]["message"], (kw, r)


def test_high_value_sale_alert_is_sent_with_escaped_values(ashop, smtp, db):  # noqa: F811
    connect(ashop, "smtp", SMTP_CFG, {"password": "app-pass"})
    p = stocked(ashop, "auto1", qty="200", selling_price="1000")
    before = invoice(ashop, [{"product_id": p, "quantity": "60"}], party_id=customer(ashop, "Earlier")["id"])  # before the rule: never replayed
    r = rule(ashop, name="Big sale", conditions=[{"field": "grand_total", "op": "gte", "value": "50000"}],
             action_config={"to": ["owner@shop.in", "accounts@shop.in"], "subject": "Big sale {number} for {party_name}",
                            "body": "{party_name} bought ₹{grand_total}.\nCheck {business}."})
    small = invoice(ashop, [{"product_id": p, "quantity": "1"}], party_id=customer(ashop, "Small")["id"])
    big = invoice(ashop, [{"product_id": p, "quantity": "60"}], party_id=customer(ashop, "<b>Tom & Co</b>")["id"])
    assert tick(db) == {"done": 1, "skipped": 0, "failed": 0}
    done = runs(ashop, rule_id=r["id"])
    assert len(done) == 1 and done[0]["status"] == "done" and done[0]["message_id"]
    assert ashop.get(f"/automation/rules/{r['id']}")["run_count"] == 1
    assert before["id"] and small["id"]

    process_due(db)
    assert sorted(m["To"] for m in smtp.sent) == ["accounts@shop.in", "owner@shop.in"]
    mail = smtp.sent[0]
    assert mail["Subject"] == f"Big sale {big['number']} for <b>Tom & Co</b>"  # plain-text header
    html = mail.get_body(("html",)).get_content()
    assert "&lt;b&gt;Tom &amp; Co&lt;/b&gt; bought ₹" in html and "<b>Tom" not in html and "<br>" in html
    assert tick(db) == {"done": 0, "skipped": 0, "failed": 0}  # each event runs a rule once


def test_overdue_reminder_after_delay_rechecks_payment(ashop, smtp, db):  # noqa: F811
    connect(ashop, "smtp", SMTP_CFG, {"password": "app-pass"})
    p = stocked(ashop, "auto2", selling_price="500")
    r = rule(ashop, name="Overdue reminder", trigger="invoice.overdue", delay_minutes=60 * 24 * 3,
             action="send_document", action_config={"channel": "email", "message": "A gentle reminder."})
    past, due = (date.today() - timedelta(days=20)).isoformat(), (date.today() - timedelta(days=5)).isoformat()
    paid_later = invoice(ashop, [{"product_id": p}], doc_date=past, due_date=due,
                         party_id=customer(ashop, "Pays late", email="late@example.com")["id"])
    unpaid = invoice(ashop, [{"product_id": p}], doc_date=past, due_date=due,
                     party_id=customer(ashop, "Never pays", email="never@example.com")["id"])
    emit_overdue(db)
    assert tick(db) == {"done": 0, "skipped": 0, "failed": 0}  # waiting 3 days
    sched = runs(ashop, rule_id=r["id"], status="scheduled")
    assert len(sched) == 2
    ashop.post("/payments", {"direction": "in", "party_id": paid_later["party_id"], "mode": "upi",
                             "amount": paid_later["grand_total"], "allocations": [
                                 {"document_id": paid_later["id"], "amount": paid_later["grand_total"]}]})
    out = automation.execute_due(db, now=datetime.now(UTC) + timedelta(days=3, minutes=1))
    assert out == {"done": 1, "skipped": 1, "failed": 0}
    by_status = {x["status"]: x for x in runs(ashop, rule_id=r["id"])}
    assert by_status["skipped"]["detail"] == "The invoice has been paid"
    process_due(db)
    assert [m["To"] for m in smtp.sent] == ["never@example.com"]
    assert unpaid["number"] in smtp.sent[0]["Subject"]
    assert "A gentle reminder." in smtp.sent[0].get_body(("html",)).get_content()


def test_failures_and_switching_off(ashop, db):
    p = stocked(ashop, "auto3", selling_price="100")
    r = rule(ashop, name="No mail set up")
    invoice(ashop, [{"product_id": p}], party_id=customer(ashop)["id"])
    assert tick(db)["failed"] == 1
    failed = runs(ashop, rule_id=r["id"])[0]
    assert failed["status"] == "failed" and "connect one under Integrations" in failed["detail"]

    late = rule(ashop, name="Later", delay_minutes=10)
    invoice(ashop, [{"product_id": p}], party_id=customer(ashop)["id"])
    automation.dispatch(db)
    assert ashop.api.c.patch(f"{ashop.base}/automation/rules/{late['id']}", headers=ashop.h,
                             json={"is_active": False}).json()["is_active"] is False
    automation.execute_due(db, now=datetime.now(UTC) + timedelta(minutes=11))
    assert runs(ashop, rule_id=late["id"])[0]["detail"] == "The rule was switched off"


def test_welcome_email_to_new_customer(ashop, smtp, db):  # noqa: F811
    connect(ashop, "smtp", SMTP_CFG, {"password": "app-pass"})
    rule(ashop, name="Welcome", trigger="customer.created",
         action_config={"to": ["customer"], "subject": "Welcome to {business}", "body": "Namaste {name}!"})
    customer(ashop, "Asha", email="asha@example.com")
    customer(ashop, "No email")
    assert tick(db) == {"done": 1, "skipped": 0, "failed": 1}
    process_due(db)
    assert [m["To"] for m in smtp.sent] == ["asha@example.com"]
    assert "Namaste Asha!" in smtp.sent[0].get_body(("html",)).get_content()


def test_permissions_and_isolation(api, owner_headers, ashop, db):
    r = rule(ashop)
    role = ashop.post("/roles", {"name": "Sales only", "permissions": ["business.view", "invoices.view"]})
    m = ashop.post("/members", {"email": "noauto@example.com", "full_name": "No Auto", "role_id": role["id"]})
    api.c.post("/api/v1/auth/accept-invite", json={"token": m["invite_token"], "password": USER_PASSWORD})
    h = api.auth("noauto@example.com", USER_PASSWORD)
    assert api.c.get(f"{ashop.base}/automation/rules", headers=h).status_code == 403
    other = Shop(api, owner_headers, name="Other biz")
    assert api.c.get(f"{other.base}/automation/rules/{r['id']}", headers=other.h).status_code == 404
    # The other business's events never run this business's rules.
    p = stocked(other, "auto4", selling_price="100")
    invoice(other, [{"product_id": p}], party_id=customer(other)["id"])
    tick(db)
    assert db.scalars(select(AutomationRun).where(AutomationRun.rule_id == uuid.UUID(r["id"]))).all() == []
    db.rollback()
    assert ashop.api.c.delete(f"{ashop.base}/automation/rules/{r['id']}", headers=ashop.h).status_code == 204
