"""Discounts above a limit need a manager.

``business.settings["discount_limit_percent"]`` (empty = no limit) is the highest line discount a
biller may give on a sales invoice or order. Scheme discounts and free goods do not count. Anyone
with ``discounts.approve`` may give more. Others ask for approval: a manager approves in the app,
or reads out the 6-digit code their screen shows, which the biller types when saving the bill.

The code is an HMAC of the request with the server secret, so it is never stored; it lasts
15 minutes, allows five wrong tries, and covers this bill up to the discount asked.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document
from app.modules.crm.models import ApprovalStatus, DiscountApproval
from app.modules.pricing.service import FREE_FLAG

ZERO = Decimal(0)
CHECKED = (DocType.SALES_INVOICE, DocType.SALES_ORDER)
CODE_MINUTES = 15
MAX_TRIES = 5


class DiscountApprovalRequired(AppError):
    status_code = 409
    code = "discount_approval_required"


def limit_of(settings: dict[str, Any] | None) -> Decimal | None:
    raw = (settings or {}).get("discount_limit_percent")
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except ArithmeticError:
        return None


def biggest_discount(doc: Document) -> tuple[Decimal, Decimal]:
    """(highest line discount %, total discount ₹) leaving out scheme lines."""
    top, total = ZERO, ZERO
    for ln in doc.lines:
        cf = ln.custom_fields or {}
        if cf.get(FREE_FLAG) or cf.get("scheme_id") or ln.gross_amount <= 0:
            continue
        pct = (ln.discount_total * 100 / ln.gross_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        top = max(top, pct)
        total += ln.discount_total
    return top, total


def _code(a: DiscountApproval) -> str:
    msg = f"{a.id}:{a.code_expires_at.timestamp():.0f}".encode()
    digest = hmac.new(get_settings().secret_key.encode(), msg, hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


def _now() -> datetime:
    return datetime.now(UTC)


def _latest(db: Session, doc: Document) -> DiscountApproval | None:
    return db.scalar(select(DiscountApproval).where(DiscountApproval.document_id == doc.id)
                     .order_by(DiscountApproval.created_at.desc()).limit(1))


def check(db: Session, access: BusinessAccess, doc: Document, *, code: str | None) -> None:
    """Called when a sales bill is finalized. Raises unless the discount is allowed."""
    if doc.doc_type not in CHECKED:
        return
    limit = limit_of(access.business.settings)
    if limit is None or access.can("discounts.approve"):
        return
    pct, amount = biggest_discount(doc)
    if pct <= limit:
        return
    a = _latest(db, doc)
    if a and a.status == ApprovalStatus.APPROVED and a.discount_percent >= pct:
        return
    if code and a and a.status == ApprovalStatus.PENDING:
        if a.attempts >= MAX_TRIES or a.code_expires_at < _now():
            raise ValidationFailed("This approval code has expired — ask again")
        if hmac.compare_digest(code.strip(), _code(a)) and a.discount_percent >= pct:
            a.status, a.decided_at, a.note = ApprovalStatus.APPROVED, _now(), "Approved with the manager's code"
            audit.record(db, action="discount.approved_by_code", entity_type="document", entity_id=doc.id,
                         business_id=access.business.id, actor_id=access.user.id, changes={"discount_percent": pct})
            return
        a.attempts += 1
        db.flush()
        raise ValidationFailed("Wrong approval code", details={"tries_left": MAX_TRIES - a.attempts})
    raise DiscountApprovalRequired(
        f"A discount of {pct} % is above your limit of {limit} % — ask a manager to approve it",
        details={"discount_percent": str(pct), "limit_percent": str(limit), "discount_amount": str(amount),
                 "pending": bool(a and a.status == ApprovalStatus.PENDING and a.code_expires_at > _now())})


def request(db: Session, access: BusinessAccess, doc: Document, *, reason: str | None) -> DiscountApproval:
    if doc.business_id != access.business.id:
        raise NotFound("Document not found")
    if doc.status != DocStatus.DRAFT:
        raise Conflict("Only a draft bill needs discount approval")
    limit = limit_of(access.business.settings)
    pct, amount = biggest_discount(doc)
    if limit is None or pct <= limit:
        raise ValidationFailed("This discount is within the limit — no approval needed")
    for old in db.scalars(select(DiscountApproval).where(DiscountApproval.document_id == doc.id,
                                                         DiscountApproval.status == ApprovalStatus.PENDING)):
        old.status, old.note = ApprovalStatus.REJECTED, "Replaced by a new request"
    a = DiscountApproval(business_id=access.business.id, document_id=doc.id, discount_percent=pct, discount_amount=amount,
                         limit_percent=limit, reason=reason, status=ApprovalStatus.PENDING,
                         code_expires_at=_now() + timedelta(minutes=CODE_MINUTES), requested_by_id=access.user.id)
    db.add(a)
    db.flush()
    audit.record(db, action="discount.approval_requested", entity_type="document", entity_id=doc.id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"discount_percent": pct, "reason": reason})
    return a


def get(db: Session, access: BusinessAccess, approval_id: uuid.UUID) -> DiscountApproval:
    a = db.get(DiscountApproval, approval_id)
    if a is None or a.business_id != access.business.id:
        raise NotFound("Approval request not found")
    return a


def decide(db: Session, access: BusinessAccess, a: DiscountApproval, *, approve: bool, note: str | None) -> DiscountApproval:
    access.require("discounts.approve")
    if a.status != ApprovalStatus.PENDING:
        raise Conflict(f"Already {a.status.value}")
    a.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    a.decided_by_id, a.decided_at, a.note = access.user.id, _now(), note
    audit.record(db, action=f"discount.{a.status.value}", entity_type="document", entity_id=a.document_id,
                 business_id=access.business.id, actor_id=access.user.id, changes={"discount_percent": a.discount_percent})
    return a


def listing(db: Session, access: BusinessAccess, *, pending_only: bool) -> list[dict[str, Any]]:
    """Approvers see the code to read out; others see their own requests without it."""
    approver = access.can("discounts.approve")
    stmt = select(DiscountApproval, Document).join(Document, Document.id == DiscountApproval.document_id).where(
        DiscountApproval.business_id == access.business.id)
    if pending_only:
        stmt = stmt.where(DiscountApproval.status == ApprovalStatus.PENDING, DiscountApproval.code_expires_at > _now())
    if not approver:
        stmt = stmt.where(DiscountApproval.requested_by_id == access.user.id)
    from app.modules.users.models import User

    out = []
    for a, d in db.execute(stmt.order_by(DiscountApproval.created_at.desc()).limit(200)).all():
        who = db.get(User, a.requested_by_id)
        live = a.status == ApprovalStatus.PENDING and a.code_expires_at > _now() and a.attempts < MAX_TRIES
        out.append({"id": a.id, "document_id": d.id, "doc_type": d.doc_type, "party_name": d.party_name,
                    "amount_payable": d.amount_payable, "discount_percent": a.discount_percent,
                    "discount_amount": a.discount_amount, "limit_percent": a.limit_percent, "reason": a.reason,
                    "status": a.status, "requested_by": who.full_name if who else None, "requested_at": a.created_at,
                    "expires_at": a.code_expires_at, "note": a.note,
                    "code": _code(a) if approver and live else None})
    return out
