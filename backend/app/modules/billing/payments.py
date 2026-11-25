"""Receipts and payments, allocated against documents.

Allocation rules:

* Receipts (money in) settle sales invoices and debit notes, or refunds from suppliers against
  purchase returns.
* Payments (money out) settle purchase invoices, or refunds to customers against credit notes.
* An allocation never exceeds the document's balance; any unallocated remainder is an advance
  (Phase 4 accounts for it in the party ledger).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting.models import JournalSource
from app.modules.accounting.posting import cash_or_bank, post_payment
from app.modules.accounting.posting import reverse as reverse_posting
from app.modules.audit import service as audit
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import (
    DocStatus,
    Document,
    Payment,
    PaymentAllocation,
    PaymentDirection,
    PaymentMode,
    PaymentState,
    PaymentStatus,
)
from app.modules.businesses.models import Branch, FinancialYear
from app.modules.events import service as events
from app.modules.gst.tax import ZERO, q2
from app.modules.numbering.service import next_number
from app.modules.parties.models import Party

SETTLES = {
    PaymentDirection.IN: (DocType.SALES_INVOICE, DocType.DEBIT_NOTE, DocType.PURCHASE_RETURN),
    PaymentDirection.OUT: (DocType.PURCHASE_INVOICE, DocType.CREDIT_NOTE),
}


def _require(access: BusinessAccess, direction: PaymentDirection, action: str) -> None:
    access.require(f"payments.{action}")
    access.require("invoices.view" if direction == PaymentDirection.IN else "purchases.view")


def _fy(db: Session, business_id: uuid.UUID, d: date) -> FinancialYear:
    fy = db.scalar(select(FinancialYear).where(
        FinancialYear.business_id == business_id, FinancialYear.start_date <= d,
        FinancialYear.end_date >= d))
    if fy is None or fy.is_closed:
        raise ValidationFailed(f"No open financial year covers {d.isoformat()}")
    return fy


def _locked_document(db: Session, doc_id: uuid.UUID) -> Document | None:
    return db.scalar(select(Document).where(Document.id == doc_id).with_for_update()
                     .execution_options(populate_existing=True))


def _apply(doc: Document, delta: Decimal) -> None:
    doc.amount_paid += delta
    doc.balance_due = doc.amount_payable - doc.amount_paid
    if doc.amount_paid <= 0:
        doc.payment_status = PaymentStatus.UNPAID
    elif doc.balance_due <= 0:
        doc.payment_status = PaymentStatus.PAID
    else:
        doc.payment_status = PaymentStatus.PARTIAL


def create_payment(db: Session, access: BusinessAccess, *, direction: PaymentDirection,
                   party_id: uuid.UUID | None, payment_date: date, mode: PaymentMode,
                   amount: Decimal, reference: str | None, notes: str | None,
                   allocations: list[dict[str, Any]], branch_id: uuid.UUID | None = None,
                   account_id: uuid.UUID | None = None, tds_amount: Decimal = ZERO,
                   tds_section: str | None = None) -> Payment:
    _require(access, direction, "create")
    amount = q2(amount)
    tds_amount = q2(Decimal(tds_amount or 0))
    if amount <= 0:
        raise ValidationFailed("Amount must be positive")
    if tds_amount < 0:
        raise ValidationFailed("TDS cannot be negative")
    if tds_amount > 0:
        if party_id is None:
            raise ValidationFailed("TDS needs a party")
        if not tds_section:
            raise ValidationFailed("Choose the TDS section (e.g. 194C)")
    else:
        tds_section = None
    party = db.get(Party, party_id) if party_id else None
    if party_id and (party is None or party.business_id != access.business.id):
        raise ValidationFailed("Party not found in this business")
    if branch_id is None:
        branch_id = db.scalar(select(Branch.id).where(
            Branch.business_id == access.business.id, Branch.kind == "head_office"))
    branch = db.get(Branch, branch_id) if branch_id else None
    if branch is None or branch.business_id != access.business.id or not access.can_access_branch(branch.id):
        raise ValidationFailed("Branch not found or not accessible")
    fy = _fy(db, access.business.id, payment_date)
    account = cash_or_bank(db, access.business.id, mode, account_id)
    prefix = "RCT" if direction == PaymentDirection.IN else "PAY"
    number = next_number(db, access.business.id, f"payment.{direction.value}/{fy.code}",
                         prefix=f"{prefix}/{fy.code}/")
    pay = Payment(business_id=access.business.id, branch_id=branch.id, financial_year_id=fy.id,
                  direction=direction, number=number, payment_date=payment_date,
                  party_id=party_id, mode=mode, account_id=account.id, amount=amount,
                  reference=reference, notes=notes, tds_amount=tds_amount, tds_section=tds_section,
                  created_by_id=access.user.id)
    db.add(pay)
    db.flush()

    total = ZERO
    touched: list[Document] = []
    for a in allocations:
        doc = _locked_document(db, a["document_id"])
        if doc is None or doc.business_id != access.business.id or not access.can_access_branch(doc.branch_id):
            raise ValidationFailed("Document to settle not found")
        alloc = q2(Decimal(a["amount"]))
        if doc.doc_type not in SETTLES[direction]:
            raise ValidationFailed(f"A {'receipt' if direction == PaymentDirection.IN else 'payment'} "
                                   f"cannot settle a {doc.doc_type.value.replace('_', ' ')}")
        if doc.status != DocStatus.FINAL:
            raise ValidationFailed(f"{doc.number or 'Draft'} is not a finalized document")
        if doc.party_id != party_id:
            raise ValidationFailed(f"{doc.number} belongs to another party")
        if alloc <= 0 or alloc > doc.balance_due:
            raise ValidationFailed(f"Allocation to {doc.number} must be between 0.01 and its "
                                   f"balance {doc.balance_due}")
        if any(x.document_id == doc.id for x in pay.allocations):
            raise ValidationFailed(f"{doc.number} is listed twice")
        pay.allocations.append(PaymentAllocation(document_id=doc.id, amount=alloc))
        _apply(doc, alloc)
        touched.append(doc)
        total += alloc
    if total > amount + tds_amount:
        raise ValidationFailed(f"Allocations ({total}) exceed the amount ({amount + tds_amount})")
    db.flush()
    audit.record(db, action="payment.created", entity_type="payment", entity_id=pay.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"number": number, "amount": amount, "mode": mode.value,
                          **({"tds_amount": tds_amount, "tds_section": tds_section} if tds_amount else {}),
                          "allocations": [{"document_id": d.id, "amount": a.amount}
                                          for d, a in zip(touched, pay.allocations, strict=True)]})
    if mode == PaymentMode.LOYALTY:
        if direction != PaymentDirection.IN:
            raise ValidationFailed("Loyalty points can only pay a customer's bill")
        from app.modules.stockx.loyalty import redeem

        redeem(db, access.business, party_id=party_id, amount=amount, payment_id=pay.id, on=payment_date,
               number=number, user_id=access.user.id)
    post_payment(db, access.business, pay, user_id=access.user.id)
    payload = {"id": pay.id, "number": number, "branch_id": pay.branch_id, "direction": direction.value,
               "amount": amount,
               "mode": mode.value, "party_id": party_id, "payment_date": payment_date}
    events.emit(db, "payment.created", aggregate_type="payment", aggregate_id=pay.id,
                business_id=access.business.id, payload=payload)
    events.emit(db, "payment.success", aggregate_type="payment", aggregate_id=pay.id,
                business_id=access.business.id, payload=payload)
    for doc in touched:
        if doc.doc_type == DocType.SALES_INVOICE and doc.payment_status == PaymentStatus.PAID:
            events.emit(db, "invoice.paid", aggregate_type="document", aggregate_id=doc.id,
                        business_id=access.business.id,
                        payload={"id": doc.id, "number": doc.number, "grand_total": doc.grand_total})
    return pay


def pay_document(db: Session, access: BusinessAccess, doc: Document, *, mode: PaymentMode,
                 amount: Decimal, reference: str | None = None,
                 account_id: uuid.UUID | None = None) -> Payment:
    """Payment taken together with the document (e.g. at the billing counter)."""
    direction = next(d for d, types in SETTLES.items() if doc.doc_type in types)
    return create_payment(db, access, direction=direction, party_id=doc.party_id,
                          payment_date=doc.doc_date, mode=mode, amount=amount,
                          reference=reference, notes=f"Against {doc.number}",
                          allocations=[{"document_id": doc.id, "amount": amount}],
                          branch_id=doc.branch_id, account_id=account_id)


def get_payment(db: Session, access: BusinessAccess, pid: uuid.UUID) -> Payment:
    p = db.get(Payment, pid)
    if p is None or p.business_id != access.business.id or not access.can_access_branch(p.branch_id):
        raise NotFound("Payment not found")
    _require(access, p.direction, "view")
    return p


def cancel_payment(db: Session, access: BusinessAccess, pay: Payment, *, reason: str) -> Payment:
    _require(access, pay.direction, "create")
    if pay.status == PaymentState.CANCELLED:
        raise Conflict("This payment is already cancelled")
    for a in pay.allocations:
        doc = _locked_document(db, a.document_id)
        assert doc is not None
        if doc.status == DocStatus.FINAL:
            _apply(doc, -a.amount)
    reverse_posting(db, access.business, JournalSource.PAYMENT, pay.id, on=local_today(),
                    user_id=access.user.id)
    if pay.mode == PaymentMode.LOYALTY:
        from app.modules.stockx.loyalty import unredeem

        unredeem(db, access.business, pay, local_today(), user_id=access.user.id)
    pay.status = PaymentState.CANCELLED
    pay.cancelled_at = datetime.now(UTC)
    pay.cancel_reason = reason
    audit.record(db, action="payment.cancelled", entity_type="payment", entity_id=pay.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"number": pay.number, "reason": reason})
    events.emit(db, "payment.cancelled", aggregate_type="payment", aggregate_id=pay.id,
                business_id=access.business.id,
                payload={"id": pay.id, "number": pay.number, "amount": pay.amount})
    return pay
