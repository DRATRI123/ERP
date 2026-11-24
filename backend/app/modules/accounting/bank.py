"""Bank reconciliation: import statement lines, match them to book entries, report the gap.

A statement line matches exactly one journal line on the same bank account with the same signed
amount (deposit = debit to the bank account). Auto-matching only pairs lines within a few days
of each other and, when several candidates fit, prefers a matching reference, then the closest
date. Importing the same statement twice adds nothing (per-line fingerprints).
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting.accounts import account_balance, get_account
from app.modules.accounting.models import (
    Account,
    AccountSubtype,
    BankStatementLine,
    JournalEntry,
    JournalLine,
)
from app.modules.audit import service as audit
from app.modules.gst.tax import ZERO

MATCH_WINDOW_DAYS = 5


def _bank(db: Session, access: BusinessAccess, account_id: uuid.UUID) -> Account:
    a = get_account(db, access, account_id)
    if a.subtype not in (AccountSubtype.BANK, AccountSubtype.CASH):
        raise ValidationFailed("Reconciliation is for bank (or cash) accounts")
    return a


def _date(v: str) -> date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(v.strip(), fmt).date()
        except ValueError:
            continue
    raise ValidationFailed(f"Unrecognised date {v!r}")


def _amount(v: str | None) -> Decimal:
    v = (v or "").replace(",", "").replace("₹", "").strip()
    if not v:
        return ZERO
    try:
        return Decimal(v)
    except InvalidOperation as exc:
        raise ValidationFailed(f"Unrecognised amount {v!r}") from exc


def parse_csv(text: str) -> list[dict[str, Any]]:
    """Common bank export layouts: a signed ``amount`` column, or separate withdrawal/deposit
    (debit/credit) columns. Header names are matched case-insensitively."""
    reader = csv.DictReader(io.StringIO(text.strip()))
    if not reader.fieldnames:
        raise ValidationFailed("The CSV has no header row")
    cols = {c.strip().lower(): c for c in reader.fieldnames}

    def col(*names: str) -> str | None:
        return next((cols[n] for n in names if n in cols), None)

    c_date = col("date", "txn date", "transaction date", "value date")
    c_desc = col("description", "narration", "particulars", "remarks")
    c_ref = col("reference", "ref", "ref no", "cheque no", "chq/ref no", "utr")
    c_amt = col("amount")
    c_out = col("withdrawal", "withdrawals", "debit", "dr", "withdrawal amt")
    c_in = col("deposit", "deposits", "credit", "cr", "deposit amt")
    if not c_date or not (c_amt or (c_out and c_in)):
        raise ValidationFailed("CSV needs a date column and either an amount column or "
                               "withdrawal and deposit columns")
    out = []
    for row in reader:
        if not (row.get(c_date) or "").strip():
            continue
        amount = _amount(row.get(c_amt)) if c_amt else _amount(row.get(c_in)) - _amount(row.get(c_out))
        if amount == 0:
            continue
        out.append({"txn_date": _date(row[c_date]), "description": (row.get(c_desc) or "").strip()[:300] or "-",
                    "reference": (row.get(c_ref) or "").strip()[:100] or None, "amount": amount})
    return out


def import_lines(db: Session, access: BusinessAccess, account_id: uuid.UUID,
                 lines: list[dict[str, Any]]) -> tuple[int, int]:
    access.require("accounting.post")
    account = _bank(db, access, account_id)
    seen: Counter[tuple] = Counter()
    added = skipped = 0
    for ln in lines:
        key = (ln["txn_date"].isoformat(), str(ln["amount"]), ln.get("reference") or "", ln["description"])
        seen[key] += 1  # identical transactions on the same day stay distinct
        fp = hashlib.sha256("|".join((*key, str(seen[key]))).encode()).hexdigest()
        if db.scalar(select(BankStatementLine.id).where(BankStatementLine.account_id == account.id,
                                                        BankStatementLine.fingerprint == fp)):
            skipped += 1
            continue
        db.add(BankStatementLine(business_id=access.business.id, account_id=account.id,
                                 txn_date=ln["txn_date"], description=ln["description"],
                                 reference=ln.get("reference"), amount=ln["amount"], fingerprint=fp))
        added += 1
    db.flush()
    audit.record(db, action="bank.statement_imported", entity_type="account", entity_id=account.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"added": added, "skipped_duplicates": skipped})
    return added, skipped


def _signed(jl: JournalLine) -> Decimal:
    return jl.debit - jl.credit


def _unmatched_book_lines(db: Session, account_id: uuid.UUID) -> list[tuple[JournalLine, JournalEntry]]:
    matched = select(BankStatementLine.matched_line_id).where(BankStatementLine.matched_line_id.is_not(None))
    return list(db.execute(select(JournalLine, JournalEntry)
                           .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                           .where(JournalLine.account_id == account_id, JournalLine.id.not_in(matched))
                           .order_by(JournalLine.entry_date)).all())


def auto_match(db: Session, access: BusinessAccess, account_id: uuid.UUID) -> int:
    access.require("accounting.post")
    account = _bank(db, access, account_id)
    book = _unmatched_book_lines(db, account.id)
    used: set[uuid.UUID] = set()
    matched = 0
    statement = db.scalars(select(BankStatementLine).where(
        BankStatementLine.account_id == account.id, BankStatementLine.matched_line_id.is_(None))
        .order_by(BankStatementLine.txn_date))
    for st in statement:
        candidates = [(jl, je) for jl, je in book if jl.id not in used and _signed(jl) == st.amount
                      and abs((jl.entry_date - st.txn_date).days) <= MATCH_WINDOW_DAYS]
        if not candidates:
            continue
        ref = (st.reference or "").lower()

        def score(c: tuple[JournalLine, JournalEntry]) -> tuple[int, int]:
            jl, je = c
            text = " ".join(filter(None, (je.source_ref, je.narration, jl.description))).lower()
            return (0 if ref and ref in text else 1, abs((jl.entry_date - st.txn_date).days))

        jl, _ = min(candidates, key=score)
        st.matched_line_id, st.matched_at = jl.id, datetime.now(UTC)
        used.add(jl.id)
        matched += 1
    db.flush()
    return matched


def match(db: Session, access: BusinessAccess, statement_line_id: uuid.UUID,
          journal_line_id: uuid.UUID) -> BankStatementLine:
    access.require("accounting.post")
    st = db.get(BankStatementLine, statement_line_id)
    jl = db.get(JournalLine, journal_line_id)
    if st is None or st.business_id != access.business.id:
        raise NotFound("Statement line not found")
    if jl is None or jl.account_id != st.account_id:
        raise ValidationFailed("The book entry is not on this bank account")
    if _signed(jl) != st.amount:
        raise ValidationFailed(f"Amounts differ: statement {st.amount}, books {_signed(jl)}")
    if st.matched_line_id:
        raise Conflict("This statement line is already matched")
    if db.scalar(select(BankStatementLine.id).where(BankStatementLine.matched_line_id == jl.id)):
        raise Conflict("That book entry is already matched to another statement line")
    st.matched_line_id, st.matched_at = jl.id, datetime.now(UTC)
    return st


def unmatch(db: Session, access: BusinessAccess, statement_line_id: uuid.UUID) -> BankStatementLine:
    access.require("accounting.post")
    st = db.get(BankStatementLine, statement_line_id)
    if st is None or st.business_id != access.business.id:
        raise NotFound("Statement line not found")
    st.matched_line_id = st.matched_at = None
    return st


@dataclass
class Reconciliation:
    as_of: date
    book_balance: Decimal
    statement_balance: Decimal
    uncleared_book: list[dict[str, Any]]  # in the books, not yet on the statement
    unmatched_statement: list[dict[str, Any]]  # on the statement, not yet in the books
    difference: Decimal  # zero when everything is explained


def reconciliation(db: Session, access: BusinessAccess, account_id: uuid.UUID, *, as_of: date,
                   statement_opening: Decimal = ZERO) -> Reconciliation:
    account = _bank(db, access, account_id)
    book = account_balance(db, account.id, up_to=as_of)
    st_total = Decimal(db.scalar(select(func.coalesce(func.sum(BankStatementLine.amount), 0)).where(
        BankStatementLine.account_id == account.id, BankStatementLine.txn_date <= as_of)) or 0)
    statement_balance = statement_opening + st_total
    uncleared = [{"journal_line_id": jl.id, "entry_number": je.number, "date": jl.entry_date,
                  "narration": je.narration, "amount": _signed(jl)}
                 for jl, je in _unmatched_book_lines(db, account.id) if jl.entry_date <= as_of]
    unmatched = [{"statement_line_id": s.id, "date": s.txn_date, "description": s.description,
                  "reference": s.reference, "amount": s.amount}
                 for s in db.scalars(select(BankStatementLine).where(
                     BankStatementLine.account_id == account.id, BankStatementLine.matched_line_id.is_(None),
                     BankStatementLine.txn_date <= as_of).order_by(BankStatementLine.txn_date))]
    # Books + items only the bank has seen − items only the books have = statement.
    explained = book + sum((u["amount"] for u in unmatched), ZERO) - sum((u["amount"] for u in uncleared), ZERO)
    return Reconciliation(as_of, book, statement_balance, uncleared, unmatched,
                          statement_balance - explained)


def statement_lines(db: Session, access: BusinessAccess, account_id: uuid.UUID,
                    *, only_unmatched: bool) -> list[BankStatementLine]:
    account = _bank(db, access, account_id)
    stmt = select(BankStatementLine).where(BankStatementLine.account_id == account.id)
    if only_unmatched:
        stmt = stmt.where(BankStatementLine.matched_line_id.is_(None))
    return list(db.scalars(stmt.order_by(BankStatementLine.txn_date, BankStatementLine.imported_at)))
