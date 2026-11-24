"""Outbound messages: queue in the request, send from the worker with retries.

Retry schedule after a failed attempt: 1 min, 5 min, 30 min, 2 h, 6 h; then the message is
marked failed and shows the last error. A delivery failure never affects the invoice itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.doctypes import SPECS
from app.modules.billing.models import DocStatus, Document
from app.modules.billing.printing import html_to_pdf, render_html
from app.modules.billing.template_service import resolve as resolve_template
from app.modules.businesses.models import Business
from app.modules.gst.words import rupees_in_words
from app.modules.integrations.base import CallLog, IntegrationError
from app.modules.integrations.models import (
    Integration,
    IntegrationStatus,
    MessageChannel,
    MessageStatus,
    OutboundMessage,
)
from app.modules.integrations.providers import commerce, messaging
from app.modules.integrations.service import active_for, credentials

BACKOFF = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2),
           timedelta(hours=6)]
CAPABILITY = {MessageChannel.EMAIL: "send_email", MessageChannel.WHATSAPP: "send_whatsapp",
              MessageChannel.SMS: "send_sms"}


def _default_recipient(doc: Document, channel: MessageChannel) -> str | None:
    p = doc.party_snapshot or {}
    return p.get("email") if channel == MessageChannel.EMAIL else p.get("phone")


def queue_document(db: Session, access: BusinessAccess, doc: Document, *, channel: MessageChannel,
                   recipient: str | None, integration_id: uuid.UUID | None,
                   message: str | None, payment_link: str | None = None) -> OutboundMessage:
    if doc.status != DocStatus.FINAL:
        raise ValidationFailed("Only finalized documents can be sent")
    integration = active_for(db, access.business.id, CAPABILITY[channel], integration_id)
    to = recipient or _default_recipient(doc, channel)
    if not to:
        raise ValidationFailed(f"No {'e-mail address' if channel == MessageChannel.EMAIL else 'phone number'} "
                               "for this customer; give a recipient")
    label = SPECS[doc.doc_type].label
    variables = {"name": doc.party_name or "Customer", "number": doc.number or "",
                 "amount": f"{doc.grand_total:.2f}", "balance": f"{doc.balance_due:.2f}",
                 "link": payment_link or ""}
    msg = OutboundMessage(
        business_id=access.business.id, integration_id=integration.id, channel=channel, recipient=to,
        subject=f"{label} {doc.number} from {access.business.name}", body=message, document_id=doc.id,
        variables=variables, status=MessageStatus.QUEUED, next_attempt_at=datetime.now(UTC),
        created_by_id=access.user.id)
    db.add(msg)
    db.flush()
    audit.record(db, action="document.send_queued", entity_type="document", entity_id=doc.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"channel": channel.value, "recipient": to, "message_id": msg.id})
    return msg


def _email_html(doc: Document, business: Business, msg: OutboundMessage) -> str:
    from html import escape

    v = msg.variables
    lines = [f"<p>Dear {escape(v['name'])},</p>",
             f"<p>Please find attached {escape(SPECS[doc.doc_type].label.lower())} <b>{escape(v['number'])}</b> "
             f"for ₹{escape(v['amount'])} ({escape(rupees_in_words(doc.grand_total))}).</p>"]
    if msg.body:
        lines.append(f"<p>{escape(msg.body)}</p>")
    if v.get("link"):
        lines.append(f'<p>Pay online: <a href="{escape(v["link"])}">{escape(v["link"])}</a></p>')
    lines.append(f"<p>Regards,<br>{escape(business.name)}</p>")
    return "\n".join(lines)


def _send(db: Session, msg: OutboundMessage, integration: Integration) -> str:
    creds = credentials(integration)
    business = db.get(Business, msg.business_id)
    doc = db.get(Document, msg.document_id) if msg.document_id else None
    pdf = filename = None
    if doc is not None:
        cfg = resolve_template(db, doc.business_id, doc.branch_id, doc.doc_type)
        pdf = html_to_pdf(render_html(doc, cfg, business_name=business.name))
        filename = f"{(doc.number or 'document').replace('/', '-')}.pdf"
    v = msg.variables
    if msg.channel == MessageChannel.EMAIL:
        html = _email_html(doc, business, msg) if doc else (msg.body or "")
        attachments = [(filename, pdf, "application/pdf")] if pdf else []
        sender = commerce.gmail_send if integration.provider == "google" else messaging.smtp_send
        return sender(integration.config, creds, to=msg.recipient, subject=msg.subject or "", html=html,
                      attachments=attachments)
    if msg.channel == MessageChannel.WHATSAPP:
        if pdf is None:
            raise IntegrationError("WhatsApp messages carry a document")
        return messaging.whatsapp_send_document(integration.config, creds, to=msg.recipient, pdf=pdf,
                                                filename=filename, body_params=[v["name"], v["number"], v["amount"]])
    flow = integration.config.get(msg.template or "invoice_flow_id") or integration.config["invoice_flow_id"]
    return messaging.msg91_send(integration.config, creds, to=msg.recipient, flow_id=flow,
                                variables={k: v[k] for k in ("name", "number", "amount", "link")})


def process_due(db: Session, *, now: datetime | None = None, limit: int = 50) -> dict[str, int]:
    """Send messages that are due. Each message commits on its own, so one failure never
    rolls back another's success. Run by ``python -m app.cli worker``."""
    now = now or datetime.now(UTC)
    ids = list(db.scalars(select(OutboundMessage.id).where(
        OutboundMessage.status == MessageStatus.QUEUED,
        or_(OutboundMessage.next_attempt_at.is_(None), OutboundMessage.next_attempt_at <= now))
        .order_by(OutboundMessage.next_attempt_at).limit(limit)))
    stats = {"sent": 0, "retrying": 0, "failed": 0}
    for mid in ids:
        msg = db.scalar(select(OutboundMessage).where(OutboundMessage.id == mid,
                                                      OutboundMessage.status == MessageStatus.QUEUED)
                        .with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if msg is None:
            continue  # taken by another worker
        integration = db.get(Integration, msg.integration_id)
        msg.attempts += 1
        try:
            if integration is None or integration.status == IntegrationStatus.DISCONNECTED:
                raise IntegrationError("The integration was disconnected")
            with CallLog(db, integration, f"send_{msg.channel.value}") as log:
                msg.provider_message_id = _send(db, msg, integration)[:200] or None
                log.detail = f"to {msg.recipient}"
            msg.status, msg.sent_at, msg.last_error, msg.next_attempt_at = MessageStatus.SENT, now, None, None
            stats["sent"] += 1
        except Exception as exc:  # noqa: BLE001 - any failure is recorded and retried
            msg.last_error = str(getattr(exc, "message", exc))[:500]
            if msg.attempts > len(BACKOFF) or isinstance(exc, ValidationFailed):
                msg.status, msg.next_attempt_at = MessageStatus.FAILED, None
                stats["failed"] += 1
            else:
                msg.next_attempt_at = now + BACKOFF[msg.attempts - 1]
                stats["retrying"] += 1
        db.commit()
    db.rollback()  # end the read transaction when nothing was due
    return stats


def retry(db: Session, access: BusinessAccess, msg: OutboundMessage) -> OutboundMessage:
    if msg.status != MessageStatus.FAILED:
        raise ValidationFailed("Only failed messages can be retried")
    msg.status, msg.next_attempt_at, msg.attempts = MessageStatus.QUEUED, datetime.now(UTC), 0
    audit.record(db, action="message.retried", entity_type="outbound_message", entity_id=msg.id,
                 business_id=access.business.id, actor_id=access.user.id)
    return msg


def apply_whatsapp_statuses(db: Session, integration: Integration, statuses: list[tuple[str, str, str | None]]) -> int:
    rank = {MessageStatus.SENT: 0, MessageStatus.DELIVERED: 1, MessageStatus.READ: 2}
    updated = 0
    for provider_id, status, error in statuses:
        msg = db.scalar(select(OutboundMessage).where(OutboundMessage.integration_id == integration.id,
                                                      OutboundMessage.provider_message_id == provider_id))
        if msg is None:
            continue
        if status == "failed":
            msg.status, msg.last_error = MessageStatus.FAILED, (error or "Delivery failed")[:500]
        else:
            new = {"sent": MessageStatus.SENT, "delivered": MessageStatus.DELIVERED, "read": MessageStatus.READ}.get(status)
            if new and rank.get(new, -1) > rank.get(msg.status, -1):  # webhooks can arrive out of order
                msg.status = new
        updated += 1
    return updated


def message_summary(msg: OutboundMessage) -> dict[str, Any]:
    return {"id": msg.id, "channel": msg.channel.value, "recipient": msg.recipient, "status": msg.status.value,
            "attempts": msg.attempts, "last_error": msg.last_error, "sent_at": msg.sent_at,
            "document_id": msg.document_id, "created_at": msg.created_at}
