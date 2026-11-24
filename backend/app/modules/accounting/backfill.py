"""Post journals for activity recorded before the books existed (or missed for any reason).

Idempotent: every posting is keyed by (source type, source id, event), so running it again
only fills gaps. Cancelled items get their posting and their reversal. Items dated in a closed
financial year are reported, not posted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.accounting import posting
from app.modules.accounting.chart import ensure_chart
from app.modules.accounting.ledger import reverse_source
from app.modules.accounting.models import JournalEntry, JournalSource
from app.modules.billing.models import DocStatus, Document, Payment, PaymentState
from app.modules.businesses.models import Business
from app.modules.inventory.models import StockDocument, StockDocumentStatus


@dataclass
class BackfillReport:
    posted: int = 0
    reversed: int = 0
    skipped: list[str] = field(default_factory=list)


def _has(db: Session, business_id: uuid.UUID, source: JournalSource, sid: uuid.UUID, event: str) -> bool:
    return db.scalar(select(JournalEntry.id).where(
        JournalEntry.business_id == business_id, JournalEntry.source_type == source,
        JournalEntry.source_id == sid, JournalEntry.source_event == event)) is not None


def backfill(db: Session, business: Business) -> BackfillReport:
    ensure_chart(db, business.id)
    report = BackfillReport()

    def run(source: JournalSource, item, label: str, do_post, cancelled_on) -> None:  # type: ignore[no-untyped-def]
        try:
            with db.begin_nested():
                if not _has(db, business.id, source, item.id, "post") and do_post() is not None:
                    report.posted += 1
                if cancelled_on and _has(db, business.id, source, item.id, "post") \
                        and not _has(db, business.id, source, item.id, "cancel"):
                    reverse_source(db, business.id, source, item.id, on=cancelled_on, created_by=None)
                    report.reversed += 1
        except AppError as exc:
            report.skipped.append(f"{label}: {exc.message}")

    for d in db.scalars(select(Document).where(
            Document.business_id == business.id,
            Document.status.in_([DocStatus.FINAL, DocStatus.CANCELLED]),
            Document.finalized_at.is_not(None)).order_by(Document.doc_date, Document.finalized_at)):
        run(JournalSource.DOCUMENT, d, d.number or str(d.id),
            lambda d=d: posting.post_document(db, business, d, user_id=None),
            d.cancelled_at.date() if d.status == DocStatus.CANCELLED and d.cancelled_at else None)
    for p in db.scalars(select(Payment).where(Payment.business_id == business.id)
                        .order_by(Payment.payment_date, Payment.created_at)):
        run(JournalSource.PAYMENT, p, p.number,
            lambda p=p: posting.post_payment(db, business, p, user_id=None),
            p.cancelled_at.date() if p.status == PaymentState.CANCELLED and p.cancelled_at else None)
    for s in db.scalars(select(StockDocument).where(StockDocument.business_id == business.id)
                        .order_by(StockDocument.doc_date, StockDocument.created_at)):
        run(JournalSource.STOCK, s, s.number,
            lambda s=s: posting.post_stock_document(db, business, s, user_id=None),
            s.cancelled_at.date() if s.status == StockDocumentStatus.CANCELLED and s.cancelled_at else None)
    return report
