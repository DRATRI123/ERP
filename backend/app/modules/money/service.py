"""Cheques (post-dated register and printing), TDS (report and deposits) and interest on overdue bills."""

from __future__ import annotations

import html
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting.chart import Accounts
from app.modules.accounting.ledger import Line, post
from app.modules.accounting.models import Account, AccountSubtype, JournalSource
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
)
from app.modules.billing.payments import SETTLES, cancel_payment, create_payment
from app.modules.events import service as events
from app.modules.gst.words import rupees_in_words
from app.modules.money.models import Cheque, ChequeStatus, TdsDeposit
from app.modules.parties.models import Party

ZERO = Decimal(0)
CENT = Decimal("0.01")


def q2(v: Decimal) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def _party(db: Session, access: BusinessAccess, party_id: uuid.UUID) -> Party:
    p = db.get(Party, party_id)
    if p is None or p.business_id != access.business.id:
        raise ValidationFailed("Party not found")
    return p


def _bank_account(db: Session, access: BusinessAccess, account_id: uuid.UUID | None) -> Account | None:
    if account_id is None:
        return None
    a = db.get(Account, account_id)
    if a is None or a.business_id != access.business.id or a.subtype not in (AccountSubtype.BANK, AccountSubtype.CASH):
        raise ValidationFailed("Choose a bank account")
    return a


# ============================================================================ cheques


def _check_allocations(db: Session, access: BusinessAccess, direction: PaymentDirection, party_id: uuid.UUID,
                       allocations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for a in allocations:
        doc = db.get(Document, a["document_id"])
        if doc is None or doc.business_id != access.business.id or doc.party_id != party_id:
            raise ValidationFailed("A bill to settle was not found for this party")
        if doc.doc_type not in SETTLES[direction] or doc.status != DocStatus.FINAL:
            raise ValidationFailed(f"{doc.number or 'A draft'} cannot be settled by this cheque")
        amt = q2(Decimal(a["amount"]))
        if amt <= 0:
            raise ValidationFailed("Allocation amounts must be positive")
        out.append({"document_id": str(doc.id), "number": doc.number, "amount": str(amt)})
    return out


def create_cheque(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Cheque:
    direction = PaymentDirection(data["direction"])
    access.require("payments.create")
    party = _party(db, access, data["party_id"])
    amount = q2(data["amount"])
    allocations = _check_allocations(db, access, direction, party.id, data.get("allocations") or [])
    if sum((Decimal(a["amount"]) for a in allocations), ZERO) > amount:
        raise ValidationFailed("Bills to settle add up to more than the cheque")
    _bank_account(db, access, data.get("account_id"))
    dup = db.scalar(select(Cheque.id).where(
        Cheque.business_id == access.business.id, Cheque.party_id == party.id, Cheque.cheque_no == data["cheque_no"],
        Cheque.direction == direction, Cheque.status != ChequeStatus.CANCELLED))
    if dup:
        raise Conflict(f"Cheque {data['cheque_no']} of {party.name} is already in the register")
    c = Cheque(business_id=access.business.id, direction=direction, party_id=party.id, cheque_no=data["cheque_no"],
               cheque_date=data["cheque_date"], bank_name=data.get("bank_name"), amount=amount,
               account_id=data.get("account_id"), branch_id=data.get("branch_id"),
               payee_name=data.get("payee_name") or (party.name if direction == PaymentDirection.OUT else None),
               allocations=allocations, notes=data.get("notes"), status=ChequeStatus.PENDING,
               created_by_id=access.user.id)
    db.add(c)
    db.flush()
    audit.record(db, action="cheque.created", entity_type="cheque", entity_id=c.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"direction": direction.value, "cheque_no": c.cheque_no,
                                                   "amount": amount, "cheque_date": c.cheque_date})
    return c


def get_cheque(db: Session, access: BusinessAccess, cheque_id: uuid.UUID) -> Cheque:
    c = db.get(Cheque, cheque_id)
    if c is None or c.business_id != access.business.id:
        raise NotFound("Cheque not found")
    return c


def list_cheques(db: Session, access: BusinessAccess, *, status: ChequeStatus | None = None,
                 direction: PaymentDirection | None = None, due_by: date | None = None) -> list[Cheque]:
    access.require("payments.view")
    stmt = select(Cheque).where(Cheque.business_id == access.business.id)
    if status:
        stmt = stmt.where(Cheque.status == status)
    if direction:
        stmt = stmt.where(Cheque.direction == direction)
    if due_by:
        stmt = stmt.where(Cheque.cheque_date <= due_by)
    return list(db.scalars(stmt.order_by(Cheque.cheque_date, Cheque.cheque_no).limit(1000)))


def cheque_summary(db: Session, access: BusinessAccess, today: date | None = None) -> dict[str, Any]:
    access.require("payments.view")
    today = today or local_today()
    out: dict[str, Any] = {}
    for d in PaymentDirection:
        base = select(func.count(Cheque.id), func.coalesce(func.sum(Cheque.amount), 0)).where(
            Cheque.business_id == access.business.id, Cheque.direction == d, Cheque.status == ChequeStatus.PENDING)
        n, amt = db.execute(base).one()
        n7, amt7 = db.execute(base.where(Cheque.cheque_date <= today + timedelta(days=7))).one()
        out[d.value] = {"pending": int(n), "pending_amount": Decimal(amt), "due_7d": int(n7), "due_7d_amount": Decimal(amt7)}
    return out


def clear_cheque(db: Session, access: BusinessAccess, c: Cheque, *, on: date) -> Cheque:
    """The bank cleared it: record the receipt / payment and settle the chosen bills (up to what
    is still due on each; the rest stays as an advance)."""
    if c.status != ChequeStatus.PENDING:
        raise Conflict(f"This cheque is {c.status.value}")
    if on < c.cheque_date:
        raise ValidationFailed(f"A cheque dated {c.cheque_date:%d %b %Y} cannot clear before its date")
    allocations = []
    for a in c.allocations:
        doc = db.get(Document, uuid.UUID(a["document_id"]))
        if doc is None or doc.status != DocStatus.FINAL:
            continue
        amt = min(Decimal(a["amount"]), doc.balance_due)
        if amt > 0:
            allocations.append({"document_id": doc.id, "amount": amt})
    pay = create_payment(db, access, direction=c.direction, party_id=c.party_id, payment_date=on,
                         mode=PaymentMode.CHEQUE, amount=c.amount,
                         reference=f"Chq {c.cheque_no}" + (f" {c.bank_name}" if c.bank_name else ""),
                         notes=c.notes, allocations=allocations, branch_id=c.branch_id, account_id=c.account_id)
    c.payment_id, c.cleared_on, c.status = pay.id, on, ChequeStatus.CLEARED
    audit.record(db, action="cheque.cleared", entity_type="cheque", entity_id=c.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"payment": pay.number, "on": on})
    return c


def bounce_cheque(db: Session, access: BusinessAccess, c: Cheque, *, on: date, reason: str) -> Cheque:
    """Dishonoured. If it had cleared, its receipt / payment is cancelled, so the bills are due again."""
    access.require("payments.create")
    if c.status not in (ChequeStatus.PENDING, ChequeStatus.CLEARED):
        raise Conflict(f"This cheque is {c.status.value}")
    if c.payment_id:
        pay = db.get(Payment, c.payment_id)
        if pay is not None and pay.status == PaymentState.POSTED:
            cancel_payment(db, access, pay, reason=f"Cheque {c.cheque_no} bounced: {reason}"[:300])
    c.status, c.bounced_on, c.bounce_reason = ChequeStatus.BOUNCED, on, reason
    audit.record(db, action="cheque.bounced", entity_type="cheque", entity_id=c.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"reason": reason, "on": on})
    party = db.get(Party, c.party_id)
    events.emit(db, "cheque.bounced", aggregate_type="cheque", aggregate_id=c.id, business_id=access.business.id,
                payload=_event_payload(c, party))
    return c


def cancel_cheque(db: Session, access: BusinessAccess, c: Cheque, *, reason: str) -> Cheque:
    access.require("payments.create")
    if c.status != ChequeStatus.PENDING:
        raise Conflict("Only a pending cheque can be cancelled; a cleared one is bounced or its payment cancelled")
    c.status, c.bounce_reason = ChequeStatus.CANCELLED, reason
    audit.record(db, action="cheque.cancelled", entity_type="cheque", entity_id=c.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"reason": reason})
    return c


def _event_payload(c: Cheque, party: Party | None) -> dict[str, Any]:
    return {"id": c.id, "direction": c.direction.value, "cheque_no": c.cheque_no, "cheque_date": c.cheque_date,
            "amount": c.amount, "party_name": party.name if party else None, "bank_name": c.bank_name,
            "status": c.status.value}


def emit_cheque_due(db: Session, *, today: date | None = None) -> int:
    """Daily: announce pending cheques whose date has come (deposit received ones, fund issued ones)."""
    today = today or local_today()
    rows = list(db.scalars(select(Cheque).where(
        Cheque.status == ChequeStatus.PENDING, Cheque.cheque_date <= today, Cheque.due_notified_at.is_(None)).limit(1000)))
    for c in rows:
        events.emit(db, "cheque.due", aggregate_type="cheque", aggregate_id=c.id, business_id=c.business_id,
                    payload=_event_payload(c, db.get(Party, c.party_id)))
        c.due_notified_at = datetime.now(UTC)
    db.commit()
    return len(rows)


# ---------------------------------------------------------------------------- cheque printing

# Positions in mm from the top-left of a CTS-2010 cheque leaf (203 x 92 mm). Every bank's leaf
# differs slightly, so each is adjustable in Settings and a test print on plain paper helps.
DEFAULT_LAYOUT: dict[str, Any] = {
    "width": 203, "height": 92, "offset_x": 0, "offset_y": 0, "font_mm": 3.6,
    "date_x": 153, "date_y": 9, "date_pitch": 5.1,  # eight boxes D D M M Y Y Y Y
    "payee_x": 22, "payee_y": 21,
    "words_x": 32, "words_y": 29.5, "words2_x": 10, "words2_y": 37, "words_width": 120,
    "amount_x": 160, "amount_y": 37.5,
    "ac_payee": True, "ac_payee_x": 6, "ac_payee_y": 6,
}


def cheque_layout(settings: dict[str, Any] | None) -> dict[str, Any]:
    stored = (settings or {}).get("cheque_layout") or {}
    return {k: stored.get(k, v) for k, v in DEFAULT_LAYOUT.items()}


def indian_figures(v: Decimal) -> str:
    """1234567.5 -> 12,34,567.50"""
    v = q2(v)
    whole, frac = f"{v:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join([*groups, tail]) + "." + frac


def _split_words(text: str, first: int) -> tuple[str, str]:
    if len(text) <= first:
        return text, ""
    cut = text.rfind(" ", 0, first)
    cut = cut if cut > 0 else first
    return text[:cut], text[cut:].strip()


def render_cheque(*, payee: str, amount: Decimal, on: date, layout: dict[str, Any], ac_payee: bool | None = None,
                  test: bool = False) -> str:
    L = layout
    ox, oy, f = float(L["offset_x"]), float(L["offset_y"]), float(L["font_mm"])
    words = rupees_in_words(q2(amount)).removeprefix("Rupees ")
    # About 2 mm per character at this size.
    w1, w2 = _split_words(words, max(20, int(float(L["words_width"]) / (f * 0.55))))
    digits = on.strftime("%d%m%Y")

    def at(x: float, y: float, text: str, extra: str = "") -> str:
        return (f'<div style="position:absolute;left:{x + ox:.2f}mm;top:{y + oy:.2f}mm;{extra}">'
                f"{html.escape(text)}</div>")

    parts = [at(float(L["date_x"]) + i * float(L["date_pitch"]), float(L["date_y"]), ch) for i, ch in enumerate(digits)]
    parts.append(at(float(L["payee_x"]), float(L["payee_y"]), payee))
    parts.append(at(float(L["words_x"]), float(L["words_y"]), w1))
    if w2:
        parts.append(at(float(L["words2_x"]), float(L["words2_y"]), w2))
    parts.append(at(float(L["amount_x"]), float(L["amount_y"]), f"**{indian_figures(amount)}/-", "font-weight:700"))
    if L["ac_payee"] if ac_payee is None else ac_payee:
        parts.append(f'<div style="position:absolute;left:{float(L["ac_payee_x"]) + ox:.2f}mm;top:{float(L["ac_payee_y"]) + oy:.2f}mm;'
                     'transform:rotate(-30deg);transform-origin:left top;border-top:0.3mm solid #000;border-bottom:0.3mm solid #000;'
                     'padding:0.5mm 2mm;font-size:0.8em;font-weight:700">A/C PAYEE</div>')
    frame = "outline:0.2mm dashed #999;" if test else ""
    w, h = float(L["width"]), float(L["height"])
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>Cheque</title><style>'
            f"@page{{size:{w}mm {h}mm;margin:0}}html,body{{margin:0;padding:0}}"
            f".leaf{{position:relative;width:{w}mm;height:{h}mm;{frame}font:{f}mm/1.1 Arial,Helvetica,sans-serif;"
            f"color:#000;overflow:hidden}}</style></head><body><div class=\"leaf\">{''.join(parts)}</div></body></html>")


def print_cheque(db: Session, access: BusinessAccess, c: Cheque, *, test: bool = False) -> str:
    access.require("payments.view")
    if c.direction != PaymentDirection.OUT:
        raise ValidationFailed("Only cheques you issue can be printed")
    party = db.get(Party, c.party_id)
    return render_cheque(payee=c.payee_name or (party.name if party else ""), amount=c.amount, on=c.cheque_date,
                         layout=cheque_layout(access.business.settings), test=test)


# ============================================================================ TDS

# Suggested rates (%), for a PAN-holding payee. Rates and thresholds change with each Finance Act:
# the rate on each payment is entered by the user; these only pre-fill the form.
TDS_SECTIONS: list[dict[str, Any]] = [
    {"section": "194C", "label": "Contractors / transport (individual or HUF)", "rate": "1"},
    {"section": "194C", "label": "Contractors / transport (others)", "rate": "2"},
    {"section": "194J", "label": "Professional fees", "rate": "10"},
    {"section": "194J", "label": "Technical services, call centre", "rate": "2"},
    {"section": "194H", "label": "Commission or brokerage", "rate": "2"},
    {"section": "194I", "label": "Rent of land, building or furniture", "rate": "10"},
    {"section": "194I", "label": "Rent of plant or machinery", "rate": "2"},
    {"section": "194Q", "label": "Purchase of goods (buyer turnover over ₹10 crore)", "rate": "0.1"},
    {"section": "194A", "label": "Interest (other than on securities)", "rate": "10"},
    {"section": "194T", "label": "Payments by a firm to its partners", "rate": "10"},
    {"section": "192", "label": "Salary", "rate": None},
]


def tds_due_date(month: date) -> date:
    """TDS deducted in a month is deposited by the 7th of the next month; March's by 30 April."""
    if month.month == 3:
        return date(month.year, 4, 30)
    nxt = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return nxt.replace(day=7)


def tds_register(db: Session, access: BusinessAccess, *, direction: PaymentDirection, date_from: date,
                 date_to: date) -> dict[str, Any]:
    """Deductions on payments (by us) or receipts (by customers), with a section-wise summary and,
    for our deductions, the deposit position month by month."""
    access.require("payments.view")
    rows = []
    stmt = (select(Payment, Party).outerjoin(Party, Party.id == Payment.party_id).where(
        Payment.business_id == access.business.id, Payment.direction == direction, Payment.tds_amount > 0,
        Payment.status == PaymentState.POSTED, Payment.payment_date >= date_from, Payment.payment_date <= date_to)
        .order_by(Payment.payment_date, Payment.number))
    by_section: dict[str, dict[str, Decimal]] = {}
    by_month: dict[date, Decimal] = {}
    for pay, party in db.execute(stmt).all():
        gross = pay.amount + pay.tds_amount
        rows.append({"payment_id": pay.id, "number": pay.number, "date": pay.payment_date,
                     "party_name": party.name if party else None, "pan": party.pan if party else None,
                     "section": pay.tds_section, "gross": gross, "tds": pay.tds_amount, "net": pay.amount,
                     "rate": q2(pay.tds_amount * 100 / gross) if gross else ZERO})
        s = by_section.setdefault(pay.tds_section or "—", {"gross": ZERO, "tds": ZERO, "count": ZERO})
        s["gross"] += gross
        s["tds"] += pay.tds_amount
        s["count"] += 1
        m = pay.payment_date.replace(day=1)
        by_month[m] = by_month.get(m, ZERO) + pay.tds_amount
    out: dict[str, Any] = {
        "direction": direction.value, "date_from": date_from, "date_to": date_to, "rows": rows,
        "sections": [{"section": k, "count": int(v["count"]), "gross": v["gross"], "tds": v["tds"]}
                     for k, v in sorted(by_section.items())],
        "total_tds": sum((Decimal(str(r["tds"])) for r in rows), ZERO),
    }
    if direction == PaymentDirection.OUT:
        deposits = {}
        for m, amt in db.execute(select(TdsDeposit.month, func.sum(TdsDeposit.amount)).where(
                TdsDeposit.business_id == access.business.id, TdsDeposit.month >= date_from.replace(day=1),
                TdsDeposit.month <= date_to).group_by(TdsDeposit.month)).all():
            deposits[m] = Decimal(amt)
        today = local_today()
        months = []
        for m in sorted(set(by_month) | set(deposits)):
            due = tds_due_date(m)
            left = by_month.get(m, ZERO) - deposits.get(m, ZERO)
            months.append({"month": m, "deducted": by_month.get(m, ZERO), "deposited": deposits.get(m, ZERO),
                           "balance": left, "due_date": due, "overdue": left > 0 and today > due})
        out["months"] = months
    return out


def list_deposits(db: Session, access: BusinessAccess) -> list[TdsDeposit]:
    access.require("payments.view")
    return list(db.scalars(select(TdsDeposit).where(TdsDeposit.business_id == access.business.id)
                           .order_by(TdsDeposit.deposit_date.desc()).limit(500)))


def record_deposit(db: Session, access: BusinessAccess, data: dict[str, Any]) -> TdsDeposit:
    """TDS paid by challan: Dr TDS Payable, Cr Bank."""
    access.require("payments.create", "accounting.post")
    bank = _bank_account(db, access, data["account_id"])
    assert bank is not None
    amount = q2(data["amount"])
    month = data["month"].replace(day=1)
    dep = TdsDeposit(business_id=access.business.id, month=month, section=data.get("section"), amount=amount,
                     deposit_date=data["deposit_date"], challan_no=data.get("challan_no"), bsr_code=data.get("bsr_code"),
                     account_id=bank.id, created_by_id=access.user.id)
    db.add(dep)
    db.flush()
    acc = Accounts(db, access.business.id)
    post(db, access.business.id, entry_date=dep.deposit_date, source_type=JournalSource.TDS, source_id=dep.id,
         source_ref=dep.challan_no or f"TDS {month:%b %Y}", created_by=access.user.id,
         narration=f"TDS deposited for {month:%B %Y}" + (f" u/s {dep.section}" if dep.section else ""),
         lines=[Line(acc["tds_payable"], debit=amount), Line(bank, credit=amount)])
    audit.record(db, action="tds.deposited", entity_type="tds_deposit", entity_id=dep.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"month": month, "amount": amount, "challan_no": dep.challan_no})
    return dep


# ============================================================================ interest on overdue


def interest_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    s = (settings or {}).get("interest") or {}
    return {"rate": Decimal(str(s.get("rate") or 0)), "grace_days": int(s.get("grace_days") or 0)}


def _charged_until(db: Session, business_id: uuid.UUID) -> dict[str, date]:
    """Latest date interest was already charged up to, per invoice (from interest debit notes)."""
    out: dict[str, date] = {}
    cf: dict[str, Any] | None
    for cf in db.scalars(select(Document.custom_fields).where(
            Document.business_id == business_id, Document.doc_type == DocType.DEBIT_NOTE,
            Document.status != DocStatus.CANCELLED)):
        inv: str | None = (cf or {}).get("interest_for")
        to: str | None = (cf or {}).get("interest_to")
        if inv and to:
            d = date.fromisoformat(to)
            out[inv] = max(out.get(inv, d), d)
    return out


def overdue_interest(db: Session, access: BusinessAccess, *, as_of: date, party_id: uuid.UUID | None = None,
                     rate: Decimal | None = None) -> list[dict[str, Any]]:
    """Simple interest, day by day, on what was actually unpaid: a part payment stops interest on
    that part from its date. Interest runs from the day after the due date (plus grace days) to
    ``as_of``, and from where the last interest debit note stopped."""
    access.require("invoices.view")
    cfg = interest_settings(access.business.settings)
    stmt = select(Document, Party).join(Party, Party.id == Document.party_id).where(
        Document.business_id == access.business.id, Document.status == DocStatus.FINAL,
        Document.doc_type.in_([DocType.SALES_INVOICE, DocType.DEBIT_NOTE]), Document.due_date.is_not(None),
        Document.due_date < as_of, Document.amount_payable > 0)
    if party_id:
        stmt = stmt.where(Document.party_id == party_id)
    if not access.all_branches:
        stmt = stmt.where(Document.branch_id.in_(access.branch_ids))
    docs = db.execute(stmt.order_by(Document.due_date)).all()
    if not docs:
        return []
    charged = _charged_until(db, access.business.id)
    pays: dict[uuid.UUID, list[tuple[date, Decimal]]] = {}
    for doc_id, d, amt in db.execute(
            select(PaymentAllocation.document_id, Payment.payment_date, PaymentAllocation.amount)
            .join(Payment, Payment.id == PaymentAllocation.payment_id)
            .where(PaymentAllocation.document_id.in_([d.id for d, _ in docs]), Payment.status == PaymentState.POSTED)).all():
        pays.setdefault(doc_id, []).append((d, Decimal(amt)))
    parties: dict[uuid.UUID, dict[str, Any]] = {}
    for doc, party in docs:
        if doc.custom_fields and doc.custom_fields.get("interest_for"):
            continue  # no interest on interest
        r = rate if rate is not None else (party.interest_rate if party.interest_rate is not None else cfg["rate"])
        if not r or r <= 0:
            continue
        assert doc.due_date is not None
        start: date = doc.due_date + timedelta(days=cfg["grace_days"] + 1)
        last = charged.get(str(doc.id))
        if last:
            start = max(start, last + timedelta(days=1))
        if start > as_of:
            continue
        events_ = sorted(pays.get(doc.id, []))
        bal = doc.amount_payable - sum((a for d, a in events_ if d < start), ZERO)
        cur, interest, days = start, ZERO, 0
        for d, a in events_:
            if d < start or d > as_of:
                continue
            if bal > 0:
                interest += bal * r * (d - cur).days / Decimal(36500)
                days += (d - cur).days
            bal -= a
            cur = d
        end = as_of + timedelta(days=1)
        if bal > 0:
            interest += bal * r * (end - cur).days / Decimal(36500)
            days += (end - cur).days
        interest = q2(interest)
        if interest <= 0:
            continue
        p = parties.setdefault(party.id, {"party_id": party.id, "party_name": party.name, "rate": r,
                                          "interest": ZERO, "invoices": []})
        p["invoices"].append({"document_id": doc.id, "number": doc.number, "doc_date": doc.doc_date,
                              "due_date": doc.due_date, "balance_due": doc.balance_due, "interest_from": start,
                              "interest_to": as_of, "days": days, "interest": interest})
        p["interest"] += interest
    return sorted(parties.values(), key=lambda p: p["interest"], reverse=True)


def draft_interest_notes(db: Session, access: BusinessAccess, *, as_of: date, party_id: uuid.UUID,
                         document_ids: list[uuid.UUID] | None = None) -> list[Document]:
    """One draft debit note per invoice for its interest. Interest on delayed payment is part of the
    value of the supply under GST, so the note carries the invoice's own HSN and rate; check before
    finalizing."""
    from app.modules.billing.service import LineIn, create_draft

    rows = overdue_interest(db, access, as_of=as_of, party_id=party_id)
    wanted = set(document_ids or [])
    out = []
    for p in rows:
        for inv in p["invoices"]:
            if wanted and inv["document_id"] not in wanted:
                continue
            src = db.get(Document, inv["document_id"])
            assert src is not None
            main = max(src.lines, key=lambda ln: ln.taxable_value, default=None)
            desc = (f"Interest @ {p['rate'].normalize():f}% p.a. on {src.number} overdue "
                    f"{inv['interest_from']:%d-%m-%Y} to {inv['interest_to']:%d-%m-%Y} ({inv['days']} days)")
            doc = create_draft(db, access, DocType.DEBIT_NOTE, {
                "party_id": party_id, "doc_date": as_of, "reference_document_id": src.id, "branch_id": src.branch_id,
                "custom_fields": {"interest_for": str(src.id), "interest_from": inv["interest_from"].isoformat(),
                                  "interest_to": inv["interest_to"].isoformat()},
            }, [LineIn(description=desc, quantity=Decimal(1), unit_price=inv["interest"], price_includes_tax=False,
                       hsn_sac=main.hsn_sac if main else None, gst_rate=main.gst_rate if main else ZERO,
                       cess_rate=ZERO)])
            out.append(doc)
    if not out:
        raise ValidationFailed("No interest due for this party")
    return out
