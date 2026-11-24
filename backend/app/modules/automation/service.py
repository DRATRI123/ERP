"""Automation rules: "when <event> [if <conditions>] [after <delay>] then <action>".

How a rule runs (all in the background worker):

1. ``dispatch`` reads new outbox events. For every active rule of the same business whose
   trigger is that event type, created *before* the event, whose conditions hold, it creates an
   ``AutomationRun`` due at ``event time + delay``.
2. ``execute_due`` runs due runs. The record is re-read and the conditions are checked again,
   so "remind 3 days after the due date" is skipped when the invoice was paid meanwhile.
   Each run commits on its own; a failure is recorded with its reason and never affects the
   invoice, payment or other runs.

Actions act as the owner (like other automatic actions) and are audited.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.errors import NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.automation.models import AutomationRule, AutomationRun, RunStatus
from app.modules.billing.models import DocStatus, Document, PaymentStatus
from app.modules.events.models import OutboxEvent
from app.modules.integrations.models import MessageChannel, MessageStatus, OutboundMessage

# ---------------------------------------------------------------------------- catalogue

_DOC_FIELDS = [
    {"key": "number", "label": "Number", "type": "text"},
    {"key": "party_name", "label": "Party name", "type": "text"},
    {"key": "grand_total", "label": "Total (₹)", "type": "number"},
    {"key": "balance_due", "label": "Balance due (₹)", "type": "number"},
    {"key": "payment_status", "label": "Payment status", "type": "choice",
     "choices": [s.value for s in PaymentStatus]},
    {"key": "days_overdue", "label": "Days overdue", "type": "number"},
]
_DOC_VARS = ["number", "party_name", "grand_total", "balance_due", "doc_date", "due_date", "days_overdue"]

TRIGGERS: dict[str, dict[str, Any]] = {
    "invoice.created": {"label": "A sales invoice is finalized", "document": True, "fields": _DOC_FIELDS},
    "invoice.overdue": {"label": "A sales invoice becomes overdue", "document": True, "fields": _DOC_FIELDS},
    "invoice.paid": {"label": "A sales invoice is fully paid", "document": True, "fields": _DOC_FIELDS},
    "invoice.cancelled": {"label": "A sales invoice is cancelled", "document": True, "fields": _DOC_FIELDS},
    "purchase.created": {"label": "A purchase bill is recorded", "document": True, "fields": _DOC_FIELDS},
    "payment.created": {"label": "A receipt or payment is recorded", "fields": [
        {"key": "amount", "label": "Amount (₹)", "type": "number"},
        {"key": "direction", "label": "Direction", "type": "choice", "choices": ["in", "out"]},
        {"key": "mode", "label": "Mode", "type": "choice",
         "choices": ["cash", "upi", "card", "bank_transfer", "cheque", "other"]}],
        "vars": ["number", "amount", "mode", "direction", "payment_date"]},
    "stock.low": {"label": "A product falls to its reorder level", "fields": [
        {"key": "sku", "label": "SKU", "type": "text"}, {"key": "name", "label": "Product name", "type": "text"},
        {"key": "on_hand", "label": "On hand", "type": "number"}],
        "vars": ["sku", "name", "on_hand", "reorder_level"]},
    "customer.created": {"label": "A customer is added", "fields": [
        {"key": "name", "label": "Name", "type": "text"}, {"key": "city", "label": "City", "type": "text"}],
        "vars": ["name", "phone", "email"]},
    "expense.created": {"label": "An expense is recorded", "fields": [
        {"key": "total", "label": "Amount (₹)", "type": "number"}],
        "vars": ["number", "total", "expense_date"]},
}
ACTIONS = {
    "send_document": "Send the invoice to the customer (e-mail, WhatsApp or SMS)",
    "notify_email": "Send an e-mail to me or my team",
}
OPS = {"eq": "is", "ne": "is not", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "contains": "contains",
       "in": "is one of"}
MAX_DELAY_MINUTES = 60 * 24 * 90  # 90 days
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def catalog() -> dict[str, Any]:
    return {
        "triggers": [{"type": t, "label": spec["label"], "fields": spec["fields"],
                      "variables": _DOC_VARS if spec.get("document") else spec["vars"],
                      "actions": [a for a in ACTIONS if a != "send_document" or t.startswith("invoice.")]}
                     for t, spec in TRIGGERS.items()],
        "actions": [{"type": a, "label": label} for a, label in ACTIONS.items()],
        "operators": [{"op": o, "label": label} for o, label in OPS.items()],
        "max_delay_minutes": MAX_DELAY_MINUTES,
    }


# ---------------------------------------------------------------------------- validation


def _clean_conditions(trigger: str, conditions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = {f["key"]: f for f in TRIGGERS[trigger]["fields"]}
    out = []
    for c in conditions:
        field, op, value = c.get("field"), c.get("op"), c.get("value")
        if field not in fields:
            raise ValidationFailed(f"'{field}' cannot be checked for this trigger")
        if op not in OPS:
            raise ValidationFailed(f"Unknown comparison '{op}'")
        value = "" if value is None else str(value).strip()[:200]
        if not value:
            raise ValidationFailed(f"Give a value to compare {fields[field]['label'].lower()} with")
        if fields[field]["type"] == "number" and op != "in":
            try:
                Decimal(value)
            except InvalidOperation:
                raise ValidationFailed(f"{fields[field]['label']} needs a number") from None
        if fields[field]["type"] == "number" and op == "contains":
            raise ValidationFailed("'contains' works on text fields only")
        out.append({"field": field, "op": op, "value": value})
    return out


def _clean_config(trigger: str, action: str, cfg: dict[str, Any]) -> dict[str, Any]:
    if action not in ACTIONS:
        raise ValidationFailed(f"Unknown action '{action}'")
    if action == "send_document":
        if not trigger.startswith("invoice."):
            raise ValidationFailed("Sending a document works with invoice triggers only")
        channel = cfg.get("channel", "email")
        if channel not in {c.value for c in MessageChannel}:
            raise ValidationFailed("Channel must be email, whatsapp or sms")
        out: dict[str, Any] = {"channel": channel}
        if cfg.get("message"):
            out["message"] = str(cfg["message"])[:1000]
        if cfg.get("integration_id"):
            out["integration_id"] = str(uuid.UUID(str(cfg["integration_id"])))
        return out
    to = [str(t).strip().lower() for t in (cfg.get("to") or []) if str(t).strip()]
    if not to or len(to) > 10:
        raise ValidationFailed("Give 1 to 10 e-mail recipients")
    for t in to:
        if t != "customer" and not _EMAIL.match(t):
            raise ValidationFailed(f"'{t}' is not an e-mail address")
    subject, body = str(cfg.get("subject") or "").strip()[:200], str(cfg.get("body") or "").strip()[:5000]
    if not subject or not body:
        raise ValidationFailed("Give a subject and a message")
    spec = TRIGGERS[trigger]
    allowed = set(_DOC_VARS if spec.get("document") else spec["vars"]) | {"business"}
    for name in _PLACEHOLDER.findall(subject + body):
        if name not in allowed:
            raise ValidationFailed(f"{{{name}}} is not available for this trigger; use one of "
                                   + ", ".join(f"{{{v}}}" for v in sorted(allowed)))
    out = {"to": to, "subject": subject, "body": body}
    if cfg.get("integration_id"):
        out["integration_id"] = str(uuid.UUID(str(cfg["integration_id"])))
    return out


def _apply(rule: AutomationRule, data: dict[str, Any]) -> None:
    trigger = data.get("trigger", rule.trigger)
    if trigger not in TRIGGERS:
        raise ValidationFailed(f"Unknown trigger '{trigger}'")
    action = data.get("action", rule.action)
    if "name" in data:
        rule.name = str(data["name"]).strip()[:120] or "Untitled rule"
    if "is_active" in data:
        rule.is_active = bool(data["is_active"])
    delay = int(data.get("delay_minutes", rule.delay_minutes or 0))
    if not 0 <= delay <= MAX_DELAY_MINUTES:
        raise ValidationFailed("Delay must be between 0 and 90 days")
    rule.trigger, rule.action, rule.delay_minutes = trigger, action, delay
    rule.conditions = _clean_conditions(trigger, data.get("conditions", rule.conditions or []))
    rule.action_config = _clean_config(trigger, action, data.get("action_config", rule.action_config or {}))


def create_rule(db: Session, access: BusinessAccess, data: dict[str, Any]) -> AutomationRule:
    rule = AutomationRule(business_id=access.business.id, created_by_id=access.user.id, name="", is_active=True,
                          trigger=data.get("trigger", ""), action=data.get("action", ""), conditions=[],
                          action_config={}, delay_minutes=0)
    _apply(rule, {"name": "", **data})
    db.add(rule)
    db.flush()
    audit.record(db, action="automation.created", entity_type="automation_rule", entity_id=rule.id,
                 business_id=access.business.id, actor_id=access.user.id, changes=_snap(rule))
    return rule


def update_rule(db: Session, access: BusinessAccess, rule: AutomationRule, data: dict[str, Any]) -> AutomationRule:
    before = _snap(rule)
    _apply(rule, data)
    db.flush()
    audit.record(db, action="automation.updated", entity_type="automation_rule", entity_id=rule.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"before": before, "after": _snap(rule)})
    return rule


def delete_rule(db: Session, access: BusinessAccess, rule: AutomationRule) -> None:
    audit.record(db, action="automation.deleted", entity_type="automation_rule", entity_id=rule.id,
                 business_id=access.business.id, actor_id=access.user.id, changes=_snap(rule))
    db.delete(rule)
    db.flush()


def get_rule(db: Session, access: BusinessAccess, rid: uuid.UUID) -> AutomationRule:
    rule = db.get(AutomationRule, rid)
    if rule is None or rule.business_id != access.business.id:
        raise NotFound("Automation rule not found")
    return rule


def _snap(rule: AutomationRule) -> dict[str, Any]:
    return {"name": rule.name, "is_active": rule.is_active, "trigger": rule.trigger, "conditions": rule.conditions,
            "delay_minutes": rule.delay_minutes, "action": rule.action, "action_config": rule.action_config}


# ---------------------------------------------------------------------------- facts & conditions


def _str(v: Any) -> str:
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return "" if v is None else str(v)


def facts_for(db: Session, ev: OutboxEvent) -> dict[str, Any] | None:
    """What a rule sees: the event payload, refreshed from the record where there is one.
    ``None`` when the record has gone."""
    facts: dict[str, Any] = dict(ev.payload or {})
    if TRIGGERS.get(ev.event_type, {}).get("document"):
        doc = db.get(Document, uuid.UUID(str(ev.aggregate_id)))
        if doc is None or doc.business_id != ev.business_id:
            return None
        party = doc.party_snapshot or {}
        today = local_today()
        facts.update({
            "id": doc.id, "number": doc.number, "party_name": doc.party_name or "", "party_id": doc.party_id,
            "party_email": party.get("email"), "party_phone": party.get("phone"),
            "grand_total": doc.grand_total, "balance_due": doc.balance_due,
            "payment_status": doc.payment_status.value if doc.payment_status else "",
            "status": doc.status.value, "doc_date": doc.doc_date, "due_date": doc.due_date,
            "days_overdue": max(0, (today - doc.due_date).days) if doc.due_date and doc.balance_due > 0 else 0,
        })
    return facts


def _num(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def holds(cond: dict[str, Any], facts: dict[str, Any]) -> bool:
    actual, op, want = facts.get(cond["field"]), cond["op"], cond["value"]
    if op == "in":
        return _str(actual).lower() in {w.strip().lower() for w in want.split(",") if w.strip()}
    if op == "contains":
        return want.lower() in _str(actual).lower()
    a, w = _num(actual), _num(want)
    if a is not None and w is not None:
        return {"eq": a == w, "ne": a != w, "gt": a > w, "gte": a >= w, "lt": a < w, "lte": a <= w}[op]
    a_s, w_s = _str(actual).lower(), want.lower()
    if op in ("eq", "ne"):
        return (a_s == w_s) == (op == "eq")
    return False  # ordering a non-number never holds


def _still_relevant(rule: AutomationRule, facts: dict[str, Any]) -> str | None:
    """Built-in checks so a delayed reminder never goes out for a settled or cancelled invoice."""
    if TRIGGERS[rule.trigger].get("document"):
        if facts.get("status") == DocStatus.CANCELLED.value and rule.trigger != "invoice.cancelled":
            return "The document was cancelled"
        if rule.trigger == "invoice.overdue" and (_num(facts.get("balance_due")) or 0) <= 0:
            return "The invoice has been paid"
    unmet = [c for c in rule.conditions if not holds(c, facts)]
    if unmet:
        c = unmet[0]
        return f"Condition no longer holds: {c['field']} {OPS[c['op']]} {c['value']}"
    return None


# ---------------------------------------------------------------------------- worker


def dispatch(db: Session, *, limit: int = 500) -> int:
    """Match new events against active rules; create (possibly delayed) runs."""
    evs = list(db.scalars(select(OutboxEvent).where(OutboxEvent.automation_dispatched_at.is_(None))
                          .order_by(OutboxEvent.created_at).limit(limit)))
    if not evs:
        db.rollback()
        return 0
    rules = list(db.scalars(select(AutomationRule).where(AutomationRule.is_active.is_(True))))
    now = datetime.now(UTC)
    created = 0
    for ev in evs:
        for rule in rules:
            if rule.business_id != ev.business_id or rule.trigger != ev.event_type or ev.created_at < rule.created_at:
                continue
            facts = facts_for(db, ev)
            if facts is None or _still_relevant(rule, facts) is not None:
                continue
            db.add(AutomationRun(rule_id=rule.id, business_id=rule.business_id, event_id=ev.id,
                                 event_type=ev.event_type, status=RunStatus.SCHEDULED,
                                 run_at=ev.created_at + timedelta(minutes=rule.delay_minutes)))
            created += 1
        ev.automation_dispatched_at = now
    db.commit()
    return created


def _render(template: str, facts: dict[str, Any], business: str, *, html: bool) -> str:
    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        v = business if key == "business" else _str(facts.get(key))
        return escape(v) if html else v

    if not html:
        return _PLACEHOLDER.sub(sub, template)
    return "<br>\n".join(_PLACEHOLDER.sub(sub, escape(line, quote=False))
                         for line in template.splitlines())


def _act(db: Session, access: BusinessAccess, rule: AutomationRule, facts: dict[str, Any]) -> uuid.UUID:
    from app.modules.integrations.outbox import queue_document
    from app.modules.integrations.service import active_for

    cfg = rule.action_config
    integration_id = uuid.UUID(cfg["integration_id"]) if cfg.get("integration_id") else None
    if rule.action == "send_document":
        doc = db.get(Document, facts["id"])
        msg = queue_document(db, access, doc, channel=MessageChannel(cfg["channel"]), recipient=None,
                             integration_id=integration_id, message=cfg.get("message"))
        return msg.id
    integration = active_for(db, access.business.id, "send_email", integration_id)
    to = []
    for t in cfg["to"]:
        addr = (facts.get("party_email") or facts.get("email")) if t == "customer" else t
        if addr and addr not in to:
            to.append(addr)
    if not to:
        raise ValidationFailed("The customer has no e-mail address")
    name = access.business.name
    subject = _render(cfg["subject"], facts, name, html=False)
    body = _render(cfg["body"], facts, name, html=True)
    first = None
    for addr in to:
        msg = OutboundMessage(business_id=access.business.id, integration_id=integration.id,
                              channel=MessageChannel.EMAIL, recipient=addr, subject=subject, body=body,
                              template=None, variables={}, document_id=None, status=MessageStatus.QUEUED,
                              next_attempt_at=datetime.now(UTC), created_by_id=access.user.id)
        db.add(msg)
        db.flush()
        first = first or msg.id
    return first


def execute_due(db: Session, *, now: datetime | None = None, limit: int = 100) -> dict[str, int]:
    from app.modules.integrations.sync import system_access

    now = now or datetime.now(UTC)
    ids = list(db.scalars(select(AutomationRun.id).where(AutomationRun.status == RunStatus.SCHEDULED,
                                                         AutomationRun.run_at <= now)
                          .order_by(AutomationRun.run_at).limit(limit)))
    stats = {"done": 0, "skipped": 0, "failed": 0}
    for rid in ids:
        run = db.scalar(select(AutomationRun).where(AutomationRun.id == rid, AutomationRun.status == RunStatus.SCHEDULED)
                        .with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if run is None:
            continue
        rule, ev = db.get(AutomationRule, run.rule_id), db.get(OutboxEvent, run.event_id)
        run.executed_at = datetime.now(UTC)
        facts = facts_for(db, ev) if ev is not None else None
        reason = ("The rule was switched off" if not rule.is_active else
                  "The record no longer exists" if facts is None else _still_relevant(rule, facts))
        if reason:
            run.status, run.detail = RunStatus.SKIPPED, reason
        else:
            sp = db.begin_nested()
            try:
                access = system_access(db, run.business_id)
                run.message_id = _act(db, access, rule, facts)
                sp.commit()
                run.status, run.detail = RunStatus.DONE, None
                rule.last_run_at, rule.run_count = run.executed_at, rule.run_count + 1
                audit.record(db, action="automation.ran", entity_type="automation_rule", entity_id=rule.id,
                             business_id=run.business_id, actor_id=access.user.id,
                             changes={"event": ev.event_type, "aggregate_id": ev.aggregate_id,
                                      "message_id": run.message_id})
            except Exception as exc:  # noqa: BLE001 - every failure is recorded on the run
                sp.rollback()
                run.status = RunStatus.FAILED
                run.detail = (getattr(exc, "message", None) or str(exc) or exc.__class__.__name__)[:500]
        stats[run.status.value] += 1
        db.commit()
    if not ids:
        db.rollback()
    return stats
