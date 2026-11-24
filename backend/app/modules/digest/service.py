"""Scheduled reports: build a business summary for a period and e-mail it on a schedule.

The summary respects the permissions of the owner (who sends it) and contains only the chosen
sections. Recipients get a plain, mobile-friendly HTML e-mail through the business's connected
e-mail integration (Gmail or SMTP), queued like every other outgoing message.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.digest.models import Frequency, ScheduledReport

SECTIONS = {
    "sales": "Sales and invoices", "collections": "Money received and paid", "dues": "Receivables and payables",
    "profit": "Profit (from the books)", "top_products": "Best-selling products", "low_stock": "Low stock",
    "expiry": "Expiring stock", "licences": "Licences due for renewal",
}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ZERO = Decimal(0)


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


# ---------------------------------------------------------------------------- schedule


def next_run(rep: ScheduledReport, after: datetime) -> datetime:
    """The first send time strictly after ``after`` (in the business time zone)."""
    local = after.astimezone(_tz())
    day = local.date()
    for _ in range(400):
        ok = (rep.frequency == Frequency.DAILY or (rep.frequency == Frequency.WEEKLY and day.weekday() == rep.weekday)
              or (rep.frequency == Frequency.MONTHLY and day.day == rep.day_of_month))
        at = datetime.combine(day, time(rep.hour), tzinfo=_tz())
        if ok and at > local:
            return at.astimezone(UTC)
        day += timedelta(days=1)
    raise ValidationFailed("Could not schedule this report")  # pragma: no cover


def period_for(rep: ScheduledReport, sent_at: datetime) -> tuple[date, date, str]:
    today = sent_at.astimezone(_tz()).date()
    if rep.frequency == Frequency.DAILY:
        d = today - timedelta(days=1)
        return d, d, f"{d:%a %d %b %Y}"
    if rep.frequency == Frequency.WEEKLY:
        return today - timedelta(days=7), today - timedelta(days=1), "the last 7 days"
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end, f"{end:%B %Y}"


# ---------------------------------------------------------------------------- CRUD


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    if "recipients" in out:
        rec = [str(r).strip().lower() for r in out["recipients"] if str(r).strip()]
        if not rec or len(rec) > 10 or any(not _EMAIL.match(r) for r in rec):
            raise ValidationFailed("Give 1 to 10 valid e-mail addresses")
        out["recipients"] = list(dict.fromkeys(rec))
    if "sections" in out:
        bad = [s for s in out["sections"] if s not in SECTIONS]
        if bad or not out["sections"]:
            raise ValidationFailed("Choose at least one section" if not bad else f"Unknown section {bad[0]}")
    return out


def save(db: Session, access: BusinessAccess, data: dict[str, Any], rep: ScheduledReport | None = None) -> ScheduledReport:
    data = _clean(data)
    if rep is None:
        rep = ScheduledReport(business_id=access.business.id, created_by_id=access.user.id, attributes={})
        db.add(rep)
    for k, v in data.items():
        setattr(rep, k, v)
    if not 0 <= rep.hour <= 23 or not 0 <= rep.weekday <= 6 or not 1 <= rep.day_of_month <= 28:
        raise ValidationFailed("Hour 0–23, weekday 0–6 and day of month 1–28")
    rep.next_run_at = next_run(rep, datetime.now(UTC)) if rep.is_active else None
    db.flush()
    audit.record(db, action="scheduled_report.saved", entity_type="scheduled_report", entity_id=rep.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"name": rep.name, "frequency": rep.frequency.value, "recipients": rep.recipients})
    return rep


def get(db: Session, access: BusinessAccess, rid: uuid.UUID) -> ScheduledReport:
    rep = db.get(ScheduledReport, rid)
    if rep is None or rep.business_id != access.business.id:
        raise NotFound("Scheduled report not found")
    return rep


# ---------------------------------------------------------------------------- content


def build(db: Session, access: BusinessAccess, sections: list[str], date_from: date, date_to: date) -> dict[str, Any]:
    from app.core.clock import local_today
    from app.modules.billing.doctypes import DocType
    from app.modules.billing.models import DocStatus, Document, DocumentLine, Payment, PaymentDirection
    from app.modules.dashboard.service import (
        PURCHASE_MINUS,
        PURCHASE_PLUS,
        SALES_MINUS,
        SALES_PLUS,
        _net_total,
        _outstanding,
        _scoped,
    )

    out: dict[str, Any] = {"business": access.business.name, "from": date_from, "to": date_to}
    if "sales" in sections and access.can("invoices.view"):
        count = db.scalar(_scoped(select(func.count()), access).where(
            Document.status == DocStatus.FINAL, Document.doc_type == DocType.SALES_INVOICE,
            Document.doc_date >= date_from, Document.doc_date <= date_to)) or 0
        prev_to = date_from - timedelta(days=1)
        prev_from = prev_to - (date_to - date_from)
        out["sales"] = {"total": _net_total(db, access, SALES_PLUS, SALES_MINUS, date_from, date_to), "invoices": int(count),
                        "previous": _net_total(db, access, SALES_PLUS, SALES_MINUS, prev_from, prev_to)}
        if access.can("purchases.view"):
            out["sales"]["purchases"] = _net_total(db, access, PURCHASE_PLUS, PURCHASE_MINUS, date_from, date_to)
    if "collections" in sections and access.can("payments.view"):
        rows = db.execute(_scoped(select(Payment.direction, Payment.mode, func.sum(Payment.amount)), access, Payment)
                          .where(Payment.payment_date >= date_from, Payment.payment_date <= date_to,
                                 Payment.cancelled_at.is_(None)).group_by(Payment.direction, Payment.mode)).all()
        got = {"received": ZERO, "paid": ZERO, "by_mode": {}}
        for direction, mode, raw in rows:
            amount = Decimal(raw or 0)
            if direction == PaymentDirection.IN:
                got["received"] += amount
                got["by_mode"][mode.value] = got["by_mode"].get(mode.value, ZERO) + amount
            else:
                got["paid"] += amount
        out["collections"] = got
    if "dues" in sections:
        dues: dict[str, Any] = {}
        if access.can("invoices.view"):
            due, overdue, n = _outstanding(db, access, SALES_PLUS)
            dues.update(receivable=due, receivable_overdue=overdue, open_invoices=n)
        if access.can("purchases.view"):
            due, overdue, n = _outstanding(db, access, PURCHASE_PLUS)
            dues.update(payable=due, payable_overdue=overdue, open_bills=n)
        out["dues"] = dues
    if "profit" in sections and access.can("accounting.view"):
        from app.modules.accounting.reports import profit_and_loss

        pl = profit_and_loss(db, access.business.id, date_from=date_from, date_to=date_to)
        out["profit"] = {"revenue": pl["total_revenue"], "gross_profit": pl["gross_profit"],
                         "expenses": pl["total_expenses"], "net_profit": pl["net_profit"]}
    if "top_products" in sections and access.can("invoices.view"):
        rows = db.execute(_scoped(select(DocumentLine.description, func.sum(DocumentLine.quantity),
                                         func.sum(DocumentLine.taxable_value)), access)
                          .join(Document, Document.id == DocumentLine.document_id)
                          .where(Document.status == DocStatus.FINAL, Document.doc_type == DocType.SALES_INVOICE,
                                 Document.doc_date >= date_from, Document.doc_date <= date_to,
                                 DocumentLine.product_id.is_not(None))
                          .group_by(DocumentLine.description).order_by(func.sum(DocumentLine.taxable_value).desc())
                          .limit(5)).all()
        out["top_products"] = [{"name": n, "quantity": q, "value": v} for n, q, v in rows]
    if ("low_stock" in sections or "expiry" in sections) and access.can("stock.view"):
        if "low_stock" in sections:
            from app.modules.inventory.reports import low_stock

            low = low_stock(db, access, warehouse_id=None)
            out["low_stock"] = [{"name": s.name, "on_hand": s.on_hand, "reorder_level": s.reorder_level} for s in low[:10]]
            out["low_stock_count"] = len(low)
        if "expiry" in sections:
            from app.modules.inventory.expiry import expiry_summary

            out["expiry"] = expiry_summary(db, access)
    if "licences" in sections:
        from app.modules.compliance.service import listing, status_of

        today = local_today()
        out["licences"] = [{"name": x.name, "number": x.number, "expiry_date": x.expiry_date, **status_of(x, today)}
                           for x in listing(db, access) if x.is_active and status_of(x, today)["status"] in ("due", "expired")]
    return out


def _money(v: Any) -> str:
    d = Decimal(v or 0)
    s = f"{abs(d):,.2f}"
    whole, frac = s.split(".")
    whole = whole.replace(",", "")
    if len(whole) > 3:  # Indian grouping: 12,34,567.00
        head, tail = whole[:-3], whole[-3:]
        head = ",".join([head[max(i - 2, 0):i] for i in range(len(head), 0, -2)][::-1])
        whole = f"{head},{tail}"
    return f"{'−' if d < 0 else ''}₹{whole}.{frac}"


def render(data: dict[str, Any], title: str, period: str) -> str:
    """A simple, inline-styled e-mail; every value is escaped."""
    e = escape
    row = '<tr><td style="padding:4px 0;color:#555">{}</td><td style="padding:4px 0;text-align:right;font-weight:600">{}</td></tr>'
    parts = [('<div style="font-family:Arial,sans-serif;max-width:560px;color:#1d2b24">'
              f'<h2 style="margin:0;color:#1f6b45">{e(data["business"])}</h2>'
              f'<p style="margin:4px 0 16px;color:#666">{e(title)} — {e(period)}</p>')]

    def block(heading: str, rows: list[tuple[str, str]]) -> None:
        if rows:
            cells = "".join(row.format(e(k.strip()), e(v)).replace('color:#555"', 'color:#555;padding-left:16px"', 1)
                            if k.startswith("  ") else row.format(e(k), e(v)) for k, v in rows)
            parts.append(f'<h3 style="margin:18px 0 6px;font-size:15px">{e(heading)}</h3>'
                         f'<table style="width:100%;border-collapse:collapse">{cells}</table>')

    if s := data.get("sales"):
        change = ""
        if s["previous"]:
            pct = (s["total"] - s["previous"]) / s["previous"] * 100
            change = f" ({'+' if pct >= 0 else ''}{pct:.0f}% vs previous)"
        rows = [("Sales", _money(s["total"]) + change), ("Invoices", str(s["invoices"]))]
        if "purchases" in s:
            rows.append(("Purchases", _money(s["purchases"])))
        block("Sales", rows)
    if c := data.get("collections"):
        block("Cash", [("Received", _money(c["received"])), ("Paid out", _money(c["paid"]))]
              + [(f"  by {m.replace('_', ' ')}", _money(a)) for m, a in sorted(c["by_mode"].items())])
    if d := data.get("dues"):
        rows = []
        if "receivable" in d:
            rows += [("To receive", _money(d["receivable"])), ("  of which overdue", _money(d["receivable_overdue"]))]
        if "payable" in d:
            rows += [("To pay", _money(d["payable"])), ("  of which overdue", _money(d["payable_overdue"]))]
        block("Dues", rows)
    if p := data.get("profit"):
        block("Profit", [("Revenue", _money(p["revenue"])), ("Gross profit", _money(p["gross_profit"])),
                         ("Expenses", _money(p["expenses"])), ("Net profit", _money(p["net_profit"]))])
    if t := data.get("top_products"):
        block("Best sellers", [(f'{x["name"]} × {Decimal(x["quantity"]).normalize():f}', _money(x["value"])) for x in t])
    if "low_stock" in data:
        block(f'Low stock ({data["low_stock_count"]})',
              [(x["name"], f'{Decimal(x["on_hand"]).normalize():f} left') for x in data["low_stock"]] or [("All stocked", "✓")])
    if x := data.get("expiry"):
        block("Expiry", [("Expired batches", str(x["expired"]["batches"])), ("Expiring in 30 days", str(x["d30"]["batches"])),
                         ("Expiring in 31–60 days", str(x["d60"]["batches"]))])
    if "licences" in data:
        block("Licences", [(f'{x["name"]} ({x["number"]})', "expired" if x["status"] == "expired" else f'{x["days_left"]} days left')
                           for x in data["licences"]] or [("All valid", "✓")])
    parts.append('<p style="margin-top:24px;font-size:12px;color:#888">Sent by your ERP. Change or stop it under Reports → Scheduled.</p></div>')
    return "".join(parts)


# ---------------------------------------------------------------------------- sending


def preview(db: Session, access: BusinessAccess, rep: ScheduledReport, at: datetime | None = None) -> str:
    date_from, date_to, period = period_for(rep, at or datetime.now(UTC))
    return render(build(db, access, rep.sections, date_from, date_to), rep.name, period)


def send(db: Session, access: BusinessAccess, rep: ScheduledReport, at: datetime) -> list[uuid.UUID]:
    from app.modules.integrations.models import MessageChannel, MessageStatus, OutboundMessage
    from app.modules.integrations.service import active_for

    integration = active_for(db, access.business.id, "send_email", rep.integration_id)
    date_from, date_to, period = period_for(rep, at)
    html = render(build(db, access, rep.sections, date_from, date_to), rep.name, period)
    ids = []
    for to in rep.recipients:
        msg = OutboundMessage(business_id=access.business.id, integration_id=integration.id, channel=MessageChannel.EMAIL,
                              recipient=to, subject=f"{rep.name} — {access.business.name} — {period}", body=html,
                              template=None, variables={}, document_id=None, status=MessageStatus.QUEUED,
                              next_attempt_at=datetime.now(UTC), created_by_id=access.user.id)
        db.add(msg)
        db.flush()
        ids.append(msg.id)
    return ids


def send_due(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Worker: queue every report whose time has come; failures are kept on the report."""
    from app.modules.integrations.sync import system_access

    now = now or datetime.now(UTC)
    due = list(db.scalars(select(ScheduledReport).where(ScheduledReport.is_active.is_(True),
                                                         ScheduledReport.next_run_at <= now)))
    stats = {"sent": 0, "failed": 0}
    for rep in due:
        sp = db.begin_nested()
        try:
            send(db, system_access(db, rep.business_id), rep, now)
            sp.commit()
            rep.last_sent_at, rep.last_error = now, None
            stats["sent"] += 1
        except Exception as exc:  # noqa: BLE001 - kept on the report for the owner to see
            sp.rollback()
            rep.last_error = (getattr(exc, "message", None) or str(exc))[:500]
            stats["failed"] += 1
        rep.next_run_at = next_run(rep, now)
        db.commit()
    if not due:
        db.rollback()
    return stats
