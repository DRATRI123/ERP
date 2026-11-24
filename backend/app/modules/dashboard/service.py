"""Dashboard figures, computed by the database (sums and groupings), never in the browser.

Each section is included only when the member may see it (sales needs ``invoices.view``,
purchases ``purchases.view``, profit ``accounting.view``, stock ``stock.view``), and document
figures respect the member's branch scope.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.modules.access.service import BusinessAccess
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document, Payment, PaymentDirection

ZERO = Decimal(0)
SALES_PLUS = (DocType.SALES_INVOICE, DocType.DEBIT_NOTE)
SALES_MINUS = (DocType.CREDIT_NOTE,)
PURCHASE_PLUS = (DocType.PURCHASE_INVOICE,)
PURCHASE_MINUS = (DocType.PURCHASE_RETURN,)


def _scoped(stmt: Select, access: BusinessAccess, model: Any = Document) -> Select:
    stmt = stmt.where(model.business_id == access.business.id)
    if not access.all_branches:
        stmt = stmt.where(model.branch_id.in_(access.branch_ids or [None]))
    return stmt


def _net_total(db: Session, access: BusinessAccess, plus: tuple, minus: tuple,
               date_from: date, date_to: date) -> Decimal:
    signed = func.sum(Document.grand_total)
    def total(types: tuple) -> Decimal:
        v = db.scalar(_scoped(select(signed), access).where(
            Document.status == DocStatus.FINAL, Document.doc_type.in_(types),
            Document.doc_date >= date_from, Document.doc_date <= date_to))
        return Decimal(v or 0)
    return total(plus) - total(minus)


def _outstanding(db: Session, access: BusinessAccess, types: tuple) -> tuple[Decimal, Decimal, int]:
    """(total due, overdue, number of open documents)."""
    today = local_today()
    base = _scoped(select(func.coalesce(func.sum(Document.balance_due), 0), func.count()), access).where(
        Document.status == DocStatus.FINAL, Document.doc_type.in_(types), Document.balance_due > 0)
    due, count = db.execute(base).one()
    overdue = db.execute(base.where(Document.due_date < today)).one()[0]
    return Decimal(due or 0), Decimal(overdue or 0), int(count)


def _daily(db: Session, access: BusinessAccess, days: int) -> list[dict[str, Any]]:
    today = local_today()
    start = today - timedelta(days=days - 1)
    rows = db.execute(_scoped(select(Document.doc_date, Document.doc_type, func.sum(Document.grand_total),
                                     func.count()), access)
                      .where(Document.status == DocStatus.FINAL, Document.doc_date >= start,
                             Document.doc_type.in_(SALES_PLUS + SALES_MINUS + PURCHASE_PLUS + PURCHASE_MINUS))
                      .group_by(Document.doc_date, Document.doc_type)).all()
    by_day: dict[date, dict[str, Any]] = {
        start + timedelta(days=i): {"date": (start + timedelta(days=i)).isoformat(), "sales": ZERO,
                                    "purchases": ZERO, "invoices": 0} for i in range(days)}
    for d, t, raw, n in rows:
        slot = by_day.get(d)
        if slot is None:
            continue
        amount = Decimal(raw or 0)
        if t in SALES_PLUS:
            slot["sales"] += amount
            slot["invoices"] += int(n) if t == DocType.SALES_INVOICE else 0
        elif t in SALES_MINUS:
            slot["sales"] -= amount
        elif t in PURCHASE_PLUS:
            slot["purchases"] += amount
        else:
            slot["purchases"] -= amount
    return [{**v, "sales": str(v["sales"]), "purchases": str(v["purchases"])} for v in by_day.values()]


def business_dashboard(db: Session, access: BusinessAccess, *, days: int = 30) -> dict[str, Any]:
    today = local_today()
    month_start = today.replace(day=1)
    out: dict[str, Any] = {"business_id": str(access.business.id), "business_name": access.business.name,
                           "today": today.isoformat(), "month_start": month_start.isoformat()}

    if access.can("invoices.view"):
        due, overdue, open_docs = _outstanding(db, access, SALES_PLUS)
        out["sales"] = {
            "today": str(_net_total(db, access, SALES_PLUS, SALES_MINUS, today, today)),
            "month": str(_net_total(db, access, SALES_PLUS, SALES_MINUS, month_start, today)),
            "invoices_today": int(db.scalar(_scoped(select(func.count()), access).where(
                Document.status == DocStatus.FINAL, Document.doc_type == DocType.SALES_INVOICE,
                Document.doc_date == today)) or 0),
            "receivable": str(due), "receivable_overdue": str(overdue), "open_invoices": open_docs,
        }
        top = db.execute(_scoped(select(Document.party_name, func.sum(Document.grand_total).label("t")), access)
                         .where(Document.status == DocStatus.FINAL, Document.doc_type == DocType.SALES_INVOICE,
                                Document.doc_date >= month_start, Document.party_name.is_not(None))
                         .group_by(Document.party_name).order_by(func.sum(Document.grand_total).desc())
                         .limit(5)).all()
        out["top_customers"] = [{"name": n, "amount": str(Decimal(t or 0))} for n, t in top]
    if access.can("purchases.view"):
        due, overdue, open_docs = _outstanding(db, access, PURCHASE_PLUS)
        out["purchases"] = {
            "month": str(_net_total(db, access, PURCHASE_PLUS, PURCHASE_MINUS, month_start, today)),
            "payable": str(due), "payable_overdue": str(overdue), "open_bills": open_docs,
        }
    if access.can("invoices.view") or access.can("purchases.view"):
        out["daily"] = _daily(db, access, days)
        recent_types = ([*SALES_PLUS, *SALES_MINUS] if access.can("invoices.view") else []) + \
                       ([*PURCHASE_PLUS, *PURCHASE_MINUS] if access.can("purchases.view") else [])
        recent = db.scalars(_scoped(select(Document), access).where(
            Document.status != DocStatus.DRAFT, Document.doc_type.in_(recent_types))
            .order_by(Document.created_at.desc()).limit(8)).all()
        out["recent"] = [{"id": str(d.id), "doc_type": d.doc_type.value, "number": d.number,
                          "doc_date": d.doc_date.isoformat(), "party_name": d.party_name,
                          "grand_total": str(d.grand_total), "status": d.status.value,
                          "payment_status": d.payment_status.value} for d in recent]
    if access.can("payments.view"):
        rows = db.execute(_scoped(select(Payment.direction, func.sum(Payment.amount)), access, Payment)
                          .where(Payment.payment_date == today, Payment.cancelled_at.is_(None))
                          .group_by(Payment.direction)).all()
        got = {d: Decimal(a or 0) for d, a in rows}
        out["payments_today"] = {"received": str(got.get(PaymentDirection.IN, ZERO)),
                                 "paid": str(got.get(PaymentDirection.OUT, ZERO))}
    if access.can("accounting.view"):
        from app.modules.accounting.reports import profit_and_loss

        pl = profit_and_loss(db, access.business.id, date_from=month_start, date_to=today)
        out["profit"] = {"revenue": pl["total_revenue"], "cost_of_goods_sold": pl["total_cost_of_goods_sold"],
                         "expenses": pl["total_expenses"], "gross_profit": pl["gross_profit"],
                         "net_profit": pl["net_profit"], "gross_margin_percent": pl["gross_margin_percent"]}
    if access.can("stock.view"):
        from app.modules.inventory.reports import expiring_batches, low_stock

        low = low_stock(db, access, warehouse_id=None)
        expiring = db.execute(select(func.count()).select_from(
            expiring_batches(db, access, within_days=30, today=today).subquery())).scalar()
        out["stock"] = {
            "low_stock_count": len(low), "expiring_30d": int(expiring or 0),
            "low_stock": [{"product_id": str(s.product_id), "sku": s.sku, "name": s.name,
                           "on_hand": str(s.on_hand), "reorder_level": str(s.reorder_level)} for s in low[:8]],
        }
    return out


def overview(db: Session, accesses: list[BusinessAccess]) -> list[dict[str, Any]]:
    """Business-wise performance across every business the user can open (this month)."""
    today = local_today()
    month_start = today.replace(day=1)
    prev_end = month_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    out = []
    for a in accesses:
        row: dict[str, Any] = {"business_id": str(a.business.id), "name": a.business.name}
        if a.can("invoices.view"):
            month = _net_total(db, a, SALES_PLUS, SALES_MINUS, month_start, today)
            # Same number of days last month, for a fair comparison.
            prev_to = min(prev_start + (today - month_start), prev_end)
            prev = _net_total(db, a, SALES_PLUS, SALES_MINUS, prev_start, prev_to)
            due, overdue, _ = _outstanding(db, a, SALES_PLUS)
            row.update({"sales_month": str(month), "sales_prev_period": str(prev),
                        "growth_percent": str(((month - prev) * 100 / prev).quantize(Decimal("0.1"))) if prev else None,
                        "receivable": str(due), "receivable_overdue": str(overdue)})
        out.append(row)
    return out
