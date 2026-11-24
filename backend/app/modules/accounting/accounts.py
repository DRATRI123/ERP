"""Chart of accounts, cost centres, manual journals and opening balances."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting.chart import Accounts, ensure_chart
from app.modules.accounting.ledger import Line, post, reverse
from app.modules.accounting.models import (
    SUBTYPE_TYPE,
    Account,
    AccountSubtype,
    CostCentre,
    JournalEntry,
    JournalLine,
    JournalSource,
)
from app.modules.audit import service as audit
from app.modules.businesses.models import FinancialYear
from app.modules.gst.tax import ZERO
from app.modules.parties.models import Party

# Control accounts are maintained by documents and payments only; posting to them by hand
# would make customer/supplier, stock and GST balances disagree with their sub-ledgers.
CONTROL_KEYS = {"ar", "ap", "inventory", "input_cgst", "input_sgst", "input_igst", "input_cess",
                "output_cgst", "output_sgst", "output_igst", "output_cess"}


def _record(db: Session, access: BusinessAccess, action: str, entity: str, eid: Any, changes: dict | None = None) -> None:
    audit.record(db, action=action, entity_type=entity, entity_id=eid,
                 business_id=access.business.id, actor_id=access.user.id, changes=changes)


# ---------------------------------------------------------------------------- accounts


def list_accounts(db: Session, access: BusinessAccess, *, include_inactive: bool = False) -> list[Account]:
    ensure_chart(db, access.business.id)
    stmt = select(Account).where(Account.business_id == access.business.id).order_by(Account.code)
    if not include_inactive:
        stmt = stmt.where(Account.is_active.is_(True))
    return list(db.scalars(stmt))


def get_account(db: Session, access: BusinessAccess, account_id: uuid.UUID) -> Account:
    a = db.get(Account, account_id)
    if a is None or a.business_id != access.business.id:
        raise NotFound("Account not found")
    return a


def _parent(db: Session, access: BusinessAccess, parent_id: uuid.UUID | None, subtype: AccountSubtype) -> None:
    if parent_id is None:
        return
    p = get_account(db, access, parent_id)
    if SUBTYPE_TYPE[p.subtype] != SUBTYPE_TYPE[subtype]:
        raise ValidationFailed("A sub-account must have the same type as its parent")


def create_account(db: Session, access: BusinessAccess, data: dict[str, Any]) -> Account:
    access.require("accounting.post")
    ensure_chart(db, access.business.id)
    if db.scalar(select(Account.id).where(Account.business_id == access.business.id,
                                          Account.code == data["code"])):
        raise Conflict(f"Account code {data['code']} is already used")
    _parent(db, access, data.get("parent_id"), data["subtype"])
    a = Account(business_id=access.business.id, type=SUBTYPE_TYPE[data["subtype"]], **data)
    db.add(a)
    db.flush()
    _record(db, access, "account.created", "account", a.id,
            {"code": a.code, "name": a.name, "subtype": a.subtype.value})
    return a


def has_postings(db: Session, account_id: uuid.UUID) -> bool:
    return db.scalar(select(JournalLine.id).where(JournalLine.account_id == account_id).limit(1)) is not None


def update_account(db: Session, access: BusinessAccess, a: Account, data: dict[str, Any]) -> Account:
    access.require("accounting.post")
    if "subtype" in data and data["subtype"] != a.subtype:
        if a.system_key:
            raise ValidationFailed("System accounts keep their type")
        if SUBTYPE_TYPE[data["subtype"]] != a.type and has_postings(db, a.id):
            raise ValidationFailed("An account with postings cannot change between asset, "
                                   "liability, equity, income and expense")
        data["type"] = SUBTYPE_TYPE[data["subtype"]]
    if data.get("is_active") is False and a.system_key:
        raise ValidationFailed("System accounts cannot be deactivated")
    if "code" in data and db.scalar(select(Account.id).where(
            Account.business_id == a.business_id, Account.code == data["code"], Account.id != a.id)):
        raise Conflict(f"Account code {data['code']} is already used")
    if "parent_id" in data:
        cursor = data["parent_id"]
        while cursor is not None:
            if cursor == a.id:
                raise ValidationFailed("An account cannot be placed under itself")
            cursor = get_account(db, access, cursor).parent_id
        _parent(db, access, data["parent_id"], data.get("subtype", a.subtype))
    fields = ["code", "name", "subtype", "parent_id", "description", "is_active", "bank_details"]
    before = audit.snapshot(a, fields)
    for k, v in data.items():
        setattr(a, k, v)
    _record(db, access, "account.updated", "account", a.id, audit.diff(before, audit.snapshot(a, fields)))
    return a


# ---------------------------------------------------------------------------- cost centres


def list_cost_centres(db: Session, access: BusinessAccess) -> list[CostCentre]:
    return list(db.scalars(select(CostCentre).where(CostCentre.business_id == access.business.id)
                           .order_by(CostCentre.code)))


def get_cost_centre(db: Session, access: BusinessAccess, cid: uuid.UUID) -> CostCentre:
    c = db.get(CostCentre, cid)
    if c is None or c.business_id != access.business.id:
        raise NotFound("Cost centre not found")
    return c


def save_cost_centre(db: Session, access: BusinessAccess, c: CostCentre | None, data: dict[str, Any]) -> CostCentre:
    access.require("accounting.post")
    if "code" in data and db.scalar(select(CostCentre.id).where(
            CostCentre.business_id == access.business.id, CostCentre.code == data["code"],
            *([CostCentre.id != c.id] if c else []))):
        raise Conflict(f"Cost centre {data['code']} already exists")
    if c is None:
        c = CostCentre(business_id=access.business.id, **data)
        db.add(c)
    else:
        for k, v in data.items():
            setattr(c, k, v)
    db.flush()
    _record(db, access, "cost_centre.saved", "cost_centre", c.id, {k: str(v) for k, v in data.items()})
    return c


# ---------------------------------------------------------------------------- journals


def _lines_from_input(db: Session, access: BusinessAccess, items: list[dict[str, Any]],
                      *, allow_control: bool) -> list[Line]:
    out = []
    for i, it in enumerate(items, 1):
        a = get_account(db, access, it["account_id"])
        if a.system_key in CONTROL_KEYS and not allow_control:
            raise ValidationFailed(
                f"Line {i}: {a.name} is maintained by invoices, purchases and payments; "
                "use those documents instead of a manual journal")
        if it.get("party_id"):
            p = db.get(Party, it["party_id"])
            if p is None or p.business_id != access.business.id:
                raise ValidationFailed(f"Line {i}: party not found")
        if it.get("cost_centre_id"):
            get_cost_centre(db, access, it["cost_centre_id"])
        out.append(Line(a, debit=it.get("debit") or ZERO, credit=it.get("credit") or ZERO,
                        party_id=it.get("party_id"), cost_centre_id=it.get("cost_centre_id"),
                        description=it.get("description")))
    return out


def manual_journal(db: Session, access: BusinessAccess, *, entry_date: date, narration: str,
                   lines: list[dict[str, Any]], branch_id: uuid.UUID | None) -> JournalEntry:
    """Journal voucher: contra (cash deposited in bank), capital introduced, loans, accruals…"""
    access.require("accounting.post")
    built = _lines_from_input(db, access, lines, allow_control=False)
    if len(built) < 2:
        raise ValidationFailed("A journal needs at least two lines")
    entry = post(db, access.business.id, entry_date=entry_date, source_type=JournalSource.MANUAL,
                 lines=built, narration=narration, branch_id=branch_id, created_by=access.user.id)
    if entry is None:
        raise ValidationFailed("The journal has no amounts")
    _record(db, access, "journal.posted", "journal_entry", entry.id, {"number": entry.number})
    return entry


def get_entry(db: Session, access: BusinessAccess, entry_id: uuid.UUID) -> JournalEntry:
    e = db.get(JournalEntry, entry_id)
    if e is None or e.business_id != access.business.id:
        raise NotFound("Journal entry not found")
    return e


def reverse_manual(db: Session, access: BusinessAccess, entry: JournalEntry, *, on: date,
                   reason: str) -> JournalEntry:
    access.require("accounting.post")
    if entry.source_type not in (JournalSource.MANUAL, JournalSource.OPENING):
        raise ValidationFailed("Automatic entries are reversed by cancelling their document, "
                               "payment or expense")
    out = reverse(db, entry, on=on, created_by=access.user.id, narration=f"Reversal: {reason}")
    _record(db, access, "journal.reversed", "journal_entry", entry.id,
            {"number": entry.number, "reversal": out.number, "reason": reason})
    return out


# ---------------------------------------------------------------------------- opening balances


def post_opening_balances(db: Session, access: BusinessAccess, *, as_of: date,
                          lines: list[dict[str, Any]]) -> JournalEntry:
    """Balances brought forward from previous books. Control accounts are allowed here
    (customer/supplier dues, GST credit) and the difference goes to Opening Balance Equity."""
    access.require("accounting.post")
    fy = db.scalar(select(FinancialYear).where(FinancialYear.business_id == access.business.id,
                                               FinancialYear.start_date == as_of))
    if fy is None:
        raise ValidationFailed("Opening balances are dated on the first day of a financial year")
    built = _lines_from_input(db, access, lines, allow_control=True)
    for ln, it in zip(built, lines, strict=True):
        if ln.account.system_key == "inventory":
            raise ValidationFailed("Enter opening stock through inventory opening-stock documents "
                                   "so quantities and values stay together")
        if ln.account.system_key in ("ar", "ap") and not it.get("party_id"):
            raise ValidationFailed(f"{ln.account.name}: give the customer/supplier for each balance")
    diff = sum((ln.debit - ln.credit for ln in built), ZERO)
    if diff:
        obe = Accounts(db, access.business.id)["opening_balance"]
        built.append(Line(obe, credit=diff) if diff > 0 else Line(obe, debit=-diff))
    entry = post(db, access.business.id, entry_date=as_of, source_type=JournalSource.OPENING,
                 lines=built, narration=f"Opening balances as of {as_of.isoformat()}",
                 created_by=access.user.id)
    if entry is None:
        raise ValidationFailed("No opening balances given")
    _record(db, access, "opening_balances.posted", "journal_entry", entry.id, {"lines": len(built)})
    return entry


def account_balance(db: Session, account_id: uuid.UUID, *, up_to: date | None = None) -> Decimal:
    """Debit-positive balance. Debit and credit are summed separately (each SUM keeps the
    exact decimal column type on every database) and subtracted in Python."""
    stmt = select(func.coalesce(func.sum(JournalLine.debit), 0),
                  func.coalesce(func.sum(JournalLine.credit), 0)).where(JournalLine.account_id == account_id)
    if up_to:
        stmt = stmt.where(JournalLine.entry_date <= up_to)
    dr, cr = db.execute(stmt).one()
    return Decimal(dr) - Decimal(cr)
