"""Budgets vs actual, the Income-tax Act depreciation schedule, daily cash closing and balance
confirmations."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting.chart import Accounts
from app.modules.accounting.ledger import Line, post
from app.modules.accounting.models import (
    Account,
    AccountSubtype,
    AccountType,
    CostCentre,
    FixedAsset,
    JournalLine,
    JournalSource,
)
from app.modules.audit import service as audit
from app.modules.businesses.models import FinancialYear
from app.modules.finance.models import BalanceConfirmation, Budget, CashClosing, ConfirmationStatus
from app.modules.parties.models import Party

ZERO = Decimal(0)


def q2(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fy(db: Session, business_id: uuid.UUID, d: date) -> FinancialYear:
    fy = db.scalar(select(FinancialYear).where(FinancialYear.business_id == business_id,
                                               FinancialYear.start_date <= d, FinancialYear.end_date >= d))
    if fy is None:
        raise ValidationFailed(f"No financial year covers {d.isoformat()}")
    return fy


def _months(start: date, end: date) -> list[date]:
    out, d = [], start.replace(day=1)
    while d <= end:
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def _account(db: Session, access: BusinessAccess, account_id: uuid.UUID) -> Account:
    a = db.get(Account, account_id)
    if a is None or a.business_id != access.business.id:
        raise ValidationFailed("Account not found")
    return a


# ============================================================================ budgets


def set_budget(db: Session, access: BusinessAccess, *, account_id: uuid.UUID, cost_centre_id: uuid.UUID | None,
               fy_start: date, months: list[Decimal]) -> list[Budget]:
    """Twelve monthly amounts for an income or expense account for one financial year."""
    access.require("accounting.post")
    a = _account(db, access, account_id)
    if a.type not in (AccountType.INCOME, AccountType.EXPENSE):
        raise ValidationFailed("Budgets are for income and expense accounts")
    if cost_centre_id:
        cc = db.get(CostCentre, cost_centre_id)
        if cc is None or cc.business_id != access.business.id:
            raise ValidationFailed("Cost centre not found")
    fy = _fy(db, access.business.id, fy_start)
    slots = _months(fy.start_date, fy.end_date)
    if len(months) != len(slots):
        raise ValidationFailed(f"Give {len(slots)} monthly amounts")
    existing = {b.month: b for b in db.scalars(select(Budget).where(
        Budget.business_id == access.business.id, Budget.account_id == a.id, Budget.month.in_(slots),
        Budget.cost_centre_id.is_(None) if cost_centre_id is None else Budget.cost_centre_id == cost_centre_id))}
    out = []
    for m, amt in zip(slots, months, strict=True):
        b = existing.get(m)
        if b is None:
            b = Budget(business_id=access.business.id, account_id=a.id, cost_centre_id=cost_centre_id, month=m, amount=q2(amt))
            db.add(b)
        else:
            b.amount = q2(amt)
        out.append(b)
    db.flush()
    audit.record(db, action="budget.set", entity_type="account", entity_id=a.id, business_id=access.business.id,
                 actor_id=access.user.id, changes={"fy": fy.code, "total": sum((q2(x) for x in months), ZERO)})
    return out


def budget_report(db: Session, access: BusinessAccess, *, date_from: date, date_to: date,
                  cost_centre_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Budget (months in the period) vs actual from the books, per account."""
    access.require("accounting.view")
    bid = access.business.id
    budgets: dict[uuid.UUID, Decimal] = {}
    stmt = select(Budget.account_id, func.sum(Budget.amount)).where(
        Budget.business_id == bid, Budget.month >= date_from.replace(day=1), Budget.month <= date_to)
    stmt = stmt.where(Budget.cost_centre_id.is_(None) if cost_centre_id is None else Budget.cost_centre_id == cost_centre_id)
    for aid, amt in db.execute(stmt.group_by(Budget.account_id)).all():
        budgets[aid] = Decimal(amt)
    if not budgets:
        return {"date_from": date_from, "date_to": date_to, "rows": [], "totals": {}}
    actual_stmt = select(JournalLine.account_id, func.sum(JournalLine.debit), func.sum(JournalLine.credit)).where(
        JournalLine.business_id == bid, JournalLine.entry_date >= date_from, JournalLine.entry_date <= date_to,
        JournalLine.account_id.in_(list(budgets))).group_by(JournalLine.account_id)
    if cost_centre_id:
        actual_stmt = actual_stmt.where(JournalLine.cost_centre_id == cost_centre_id)
    actual = {aid: (Decimal(dr or 0), Decimal(cr or 0)) for aid, dr, cr in db.execute(actual_stmt).all()}
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(list(budgets))))}
    rows = []
    totals = {"income_budget": ZERO, "income_actual": ZERO, "expense_budget": ZERO, "expense_actual": ZERO}
    for aid, bud in budgets.items():
        a = accounts[aid]
        dr, cr = actual.get(aid, (ZERO, ZERO))
        act = cr - dr if a.type == AccountType.INCOME else dr - cr
        var = act - bud
        # Over budget is bad for expenses, good for income.
        favourable = var >= 0 if a.type == AccountType.INCOME else var <= 0
        rows.append({"account_id": aid, "code": a.code, "name": a.name, "type": a.type.value, "budget": bud, "actual": act,
                     "variance": var, "used_percent": round(float(act / bud * 100), 1) if bud else None, "favourable": favourable})
        k = "income" if a.type == AccountType.INCOME else "expense"
        totals[f"{k}_budget"] += bud
        totals[f"{k}_actual"] += act
    rows.sort(key=lambda r: (r["type"] != "income", r["code"]))
    return {"date_from": date_from, "date_to": date_to, "rows": rows, "totals": totals}


# ============================================================================ Income-tax Act depreciation

IT_BLOCKS: dict[str, tuple[str, Decimal]] = {
    "building_res": ("Buildings — residential", Decimal(5)),
    "building": ("Buildings — other", Decimal(10)),
    "furniture": ("Furniture and fittings", Decimal(10)),
    "plant": ("Plant and machinery (general)", Decimal(15)),
    "motor_car": ("Motor cars (not for hire)", Decimal(15)),
    "vehicle_hire": ("Vehicles used for hire", Decimal(30)),
    "computer": ("Computers and software", Decimal(40)),
    "intangible": ("Intangible assets (licences, trademarks)", Decimal(25)),
}


def it_depreciation(db: Session, access: BusinessAccess, *, fy_start: date) -> dict[str, Any]:
    """Block-wise written-down value under section 32: opening WDV + additions − sale proceeds,
    full rate on additions used 180 days or more in the year, half rate on the rest. Earlier years
    are rolled forward from the opening WDVs entered in Settings (``settings.it_opening_wdv``)."""
    access.require("accounting.view")
    target = _fy(db, access.business.id, fy_start)
    assets = list(db.scalars(select(FixedAsset).where(FixedAsset.business_id == access.business.id,
                                                     FixedAsset.it_block.is_not(None))))
    cfg = (access.business.settings or {}).get("it_opening_wdv") or {}
    base_start = date.fromisoformat(cfg["fy_start"]) if cfg.get("fy_start") else None
    openings = {k: Decimal(str(v)) for k, v in (cfg.get("blocks") or {}).items()}
    years = list(db.scalars(select(FinancialYear).where(FinancialYear.business_id == access.business.id,
                                                       FinancialYear.end_date <= target.end_date).order_by(FinancialYear.start_date)))
    if base_start:
        years = [y for y in years if y.start_date >= base_start]
    wdv = {k: openings.get(k, ZERO) for k in IT_BLOCKS}
    result: list[dict[str, Any]] = []
    for fy in years:
        result = []
        for key, (label, rate) in IT_BLOCKS.items():
            mine = [a for a in assets if a.it_block == key]
            cutoff = fy.end_date - timedelta(days=179)  # put to use on or before this date: 180 days or more
            full = sum((a.cost for a in mine if fy.start_date <= a.acquired_on <= cutoff), ZERO)
            half = sum((a.cost for a in mine if cutoff < a.acquired_on <= fy.end_date), ZERO)
            sold = sum((a.disposal_proceeds or ZERO for a in mine
                        if a.disposed_on and fy.start_date <= a.disposed_on <= fy.end_date), ZERO)
            opening = wdv[key]
            base_full = max(ZERO, opening + full - sold)
            base_half = half if opening + full - sold >= 0 else max(ZERO, opening + full + half - sold)
            dep = q2(base_full * rate / 100 + base_half * rate / 200)
            closing = max(ZERO, opening + full + half - sold - dep)
            wdv[key] = closing
            if opening or full or half or sold:
                result.append({"block": key, "label": label, "rate": rate, "opening_wdv": opening, "additions_180_plus": full,
                               "additions_under_180": half, "sale_proceeds": sold, "depreciation": dep, "closing_wdv": closing})
    return {"financial_year": target.code, "blocks": result,
            "total_depreciation": sum((r["depreciation"] for r in result), ZERO),
            "blocks_available": [{"key": k, "label": v[0], "rate": v[1]} for k, v in IT_BLOCKS.items()],
            "note": "For the tax computation only; the books keep their own depreciation. If a block's sale proceeds exceed "
                    "its WDV, the excess is a short-term capital gain."}


# ============================================================================ daily cash closing


def _cash_account(db: Session, access: BusinessAccess, account_id: uuid.UUID | None) -> Account:
    a = _account(db, access, account_id) if account_id else Accounts(db, access.business.id)["cash"]
    if a.subtype != AccountSubtype.CASH:
        raise ValidationFailed("Choose a cash account")
    return a


def cash_position(db: Session, access: BusinessAccess, *, on: date, account_id: uuid.UUID | None) -> dict[str, Any]:
    access.require("accounting.view")
    a = _cash_account(db, access, account_id)
    def total(stmt: Any) -> tuple[Decimal, Decimal]:
        dr, cr = db.execute(stmt).one()
        return Decimal(dr or 0), Decimal(cr or 0)
    base = select(func.sum(JournalLine.debit), func.sum(JournalLine.credit)).where(
        JournalLine.business_id == access.business.id, JournalLine.account_id == a.id)
    odr, ocr = total(base.where(JournalLine.entry_date < on))
    ddr, dcr = total(base.where(JournalLine.entry_date == on))
    done = db.scalar(select(CashClosing).where(CashClosing.business_id == access.business.id, CashClosing.account_id == a.id,
                                               CashClosing.closing_date == on))
    return {"account_id": a.id, "account": a.name, "date": on, "opening": odr - ocr, "received": ddr, "paid": dcr,
            "expected": odr - ocr + ddr - dcr, "closed": _closing_out(done) if done else None}


def _closing_out(c: CashClosing) -> dict[str, Any]:
    return {"id": c.id, "date": c.closing_date, "expected": c.expected, "counted": c.counted, "difference": c.difference,
            "denominations": c.denominations, "notes": c.notes}


NOTES = ("2000", "500", "200", "100", "50", "20", "10", "5", "2", "1")


def close_cash(db: Session, access: BusinessAccess, *, on: date, account_id: uuid.UUID | None,
               denominations: dict[str, Any], notes: str | None) -> CashClosing:
    """Count the drawer: note counts by denomination (plus loose coins). A difference from the books
    is posted to Cash shortage / Cash excess so the cash account matches the drawer."""
    access.require("accounting.post")
    pos = cash_position(db, access, on=on, account_id=account_id)
    if pos["closed"]:
        raise Conflict("Cash for this day is already closed")
    counted = ZERO
    clean: dict[str, Any] = {}
    for k, v in denominations.items():
        if k in NOTES:
            n = int(v or 0)
            if n < 0:
                raise ValidationFailed("Counts cannot be negative")
            if n:
                clean[k] = n
                counted += Decimal(k) * n
        elif k == "coins":
            coins = q2(Decimal(str(v or 0)))
            if coins < 0:
                raise ValidationFailed("Counts cannot be negative")
            clean[k] = str(coins)
            counted += coins
        else:
            raise ValidationFailed(f"Unknown denomination {k}")
    counted = q2(counted)
    diff = counted - q2(pos["expected"])
    c = CashClosing(business_id=access.business.id, account_id=pos["account_id"], closing_date=on, expected=q2(pos["expected"]),
                    counted=counted, difference=diff, denominations=clean, notes=notes, closed_by_id=access.user.id)
    db.add(c)
    db.flush()
    if diff:
        acc = Accounts(db, access.business.id)
        cash = db.get(Account, pos["account_id"])
        assert cash is not None
        lines = ([Line(cash, debit=diff), Line(acc["cash_excess"], credit=diff)] if diff > 0
                 else [Line(acc["cash_shortage"], debit=-diff), Line(cash, credit=-diff)])
        post(db, access.business.id, entry_date=on, source_type=JournalSource.CASH_CLOSING, source_id=c.id,
             source_ref=f"Cash {on:%d-%m-%Y}", lines=lines, created_by=access.user.id,
             narration=f"Cash {'excess' if diff > 0 else 'shortage'} found at closing on {on:%d %b %Y}")
    audit.record(db, action="cash.closed", entity_type="account", entity_id=pos["account_id"], business_id=access.business.id,
                 actor_id=access.user.id, changes={"date": on, "expected": c.expected, "counted": counted, "difference": diff})
    return c


def closings(db: Session, access: BusinessAccess, *, date_from: date, date_to: date) -> list[dict[str, Any]]:
    access.require("accounting.view")
    return [_closing_out(c) | {"account_id": c.account_id} for c in db.scalars(select(CashClosing).where(
        CashClosing.business_id == access.business.id, CashClosing.closing_date >= date_from,
        CashClosing.closing_date <= date_to).order_by(CashClosing.closing_date.desc()))]


# ============================================================================ balance confirmation


def _party_ledger(db: Session, business_id: uuid.UUID, party_id: uuid.UUID, date_from: date, as_of: date) -> dict[str, Any]:
    """Receivable / payable movements of a party: opening, entries, closing (+ they owe us)."""
    from app.modules.accounting.models import JournalEntry

    acc = Accounts(db, business_id)
    ids = [acc["ar"].id, acc["ap"].id]
    base = select(func.sum(JournalLine.debit), func.sum(JournalLine.credit)).where(
        JournalLine.business_id == business_id, JournalLine.party_id == party_id, JournalLine.account_id.in_(ids))
    dr, cr = db.execute(base.where(JournalLine.entry_date < date_from)).one()
    opening = Decimal(dr or 0) - Decimal(cr or 0)
    rows, bal = [], opening
    for ln, je in db.execute(select(JournalLine, JournalEntry).join(JournalEntry, JournalEntry.id == JournalLine.entry_id).where(
            JournalLine.business_id == business_id, JournalLine.party_id == party_id, JournalLine.account_id.in_(ids),
            JournalLine.entry_date >= date_from, JournalLine.entry_date <= as_of).order_by(JournalLine.entry_date, JournalEntry.number)).all():
        bal += ln.debit - ln.credit
        rows.append({"date": ln.entry_date.isoformat(), "ref": je.source_ref or je.number, "narration": je.narration,
                     "debit": str(ln.debit), "credit": str(ln.credit), "balance": str(bal)})
    return {"from": date_from.isoformat(), "to": as_of.isoformat(), "opening": str(opening), "entries": rows, "closing": str(bal)}


def send_confirmation(db: Session, access: BusinessAccess, *, party_id: uuid.UUID, as_of: date,
                      date_from: date | None) -> BalanceConfirmation:
    access.require("accounting.view")
    party = db.get(Party, party_id)
    if party is None or party.business_id != access.business.id:
        raise ValidationFailed("Party not found")
    start = date_from or _fy(db, access.business.id, as_of).start_date
    st = _party_ledger(db, access.business.id, party.id, start, as_of)
    b = access.business
    st.update({"party": party.name, "business": b.legal_name or b.name, "business_gstin": b.gstin})
    c = BalanceConfirmation(business_id=b.id, party_id=party.id, as_of=as_of, balance=Decimal(st["closing"]), statement=st,
                            token=secrets.token_urlsafe(24), status=ConfirmationStatus.SENT, created_by_id=access.user.id)
    db.add(c)
    db.flush()
    audit.record(db, action="balance_confirmation.sent", entity_type="party", entity_id=party.id, business_id=b.id,
                 actor_id=access.user.id, changes={"as_of": as_of, "balance": c.balance})
    return c


def list_confirmations(db: Session, access: BusinessAccess, party_id: uuid.UUID | None = None) -> list[BalanceConfirmation]:
    access.require("accounting.view")
    stmt = select(BalanceConfirmation).where(BalanceConfirmation.business_id == access.business.id)
    if party_id:
        stmt = stmt.where(BalanceConfirmation.party_id == party_id)
    return list(db.scalars(stmt.order_by(BalanceConfirmation.created_at.desc()).limit(500)))


def by_token(db: Session, token: str) -> BalanceConfirmation:
    c = db.scalar(select(BalanceConfirmation).where(BalanceConfirmation.token == token))
    if c is None or c.created_at < datetime.now(UTC) - timedelta(days=90):
        raise NotFound("This confirmation link is not valid any more")
    return c


def respond(db: Session, token: str, *, agree: bool, note: str | None, their_balance: Decimal | None) -> BalanceConfirmation:
    c = by_token(db, token)
    if c.status != ConfirmationStatus.SENT:
        raise Conflict("This balance has already been answered")
    if not agree and not (note or their_balance is not None):
        raise ValidationFailed("Tell us the balance in your books or what differs")
    c.status = ConfirmationStatus.CONFIRMED if agree else ConfirmationStatus.DISPUTED
    c.response_note, c.their_balance, c.responded_at = note, their_balance, datetime.now(UTC)
    return c
