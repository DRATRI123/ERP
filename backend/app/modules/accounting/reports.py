"""Financial reports from the journal.

Conventions: balances are debit-positive internally; statements present each section in its
natural sign (income and liabilities positive). Period reports (P&L, cash flow) exclude
year-closing entries; position reports (balance sheet, trial balance) include them. Amounts are
presented in rupees and paise.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.modules.accounting.chart import Accounts, ensure_chart
from app.modules.accounting.models import (
    Account,
    AccountSubtype,
    AccountType,
    JournalEntry,
    JournalLine,
    JournalSource,
)
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import (
    DocStatus,
    Document,
    Payment,
    PaymentDirection,
    PaymentState,
)
from app.modules.gst.reports import present
from app.modules.gst.tax import ZERO
from app.modules.parties.models import Party

S = AccountSubtype


def _sums(db: Session, business_id: uuid.UUID, *, date_from: date | None = None,
          date_to: date | None = None, exclude_closing: bool = False,
          branch_id: uuid.UUID | None = None, cost_centre_id: uuid.UUID | None = None,
          party_id: uuid.UUID | None = None, account_ids: list[uuid.UUID] | None = None
          ) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    stmt: Select = (select(JournalLine.account_id, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
                    .where(JournalLine.business_id == business_id).group_by(JournalLine.account_id))
    if date_from:
        stmt = stmt.where(JournalLine.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(JournalLine.entry_date <= date_to)
    if exclude_closing or branch_id:
        stmt = stmt.join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        if exclude_closing:
            stmt = stmt.where(JournalEntry.source_type != JournalSource.CLOSING)
        if branch_id:
            stmt = stmt.where(JournalEntry.branch_id == branch_id)
    if cost_centre_id:
        stmt = stmt.where(JournalLine.cost_centre_id == cost_centre_id)
    if party_id:
        stmt = stmt.where(JournalLine.party_id == party_id)
    if account_ids is not None:
        stmt = stmt.where(JournalLine.account_id.in_(account_ids))
    return {aid: (Decimal(dr or 0), Decimal(cr or 0)) for aid, dr, cr in db.execute(stmt)}


def _accounts(db: Session, business_id: uuid.UUID) -> dict[uuid.UUID, Account]:
    ensure_chart(db, business_id)
    return {a.id: a for a in db.scalars(select(Account).where(Account.business_id == business_id))}


def _row(a: Account, amount: Decimal) -> dict[str, Any]:
    return {"account_id": a.id, "code": a.code, "name": a.name, "subtype": a.subtype.value,
            "amount": amount}


# ---------------------------------------------------------------------------- trial balance


def trial_balance(db: Session, business_id: uuid.UUID, *, date_from: date, date_to: date) -> dict[str, Any]:
    accounts = _accounts(db, business_id)
    opening = _sums(db, business_id, date_to=date_from - timedelta(days=1))
    period = _sums(db, business_id, date_from=date_from, date_to=date_to)
    rows, totals = [], defaultdict(lambda: ZERO)
    for aid in sorted(set(opening) | set(period), key=lambda i: accounts[i].code):
        a = accounts[aid]
        o = opening.get(aid, (ZERO, ZERO))
        p = period.get(aid, (ZERO, ZERO))
        ob = o[0] - o[1]
        cb = ob + p[0] - p[1]
        row = {"account_id": aid, "code": a.code, "name": a.name, "type": a.type.value,
               "opening_debit": max(ob, ZERO), "opening_credit": max(-ob, ZERO),
               "debit": p[0], "credit": p[1],
               "closing_debit": max(cb, ZERO), "closing_credit": max(-cb, ZERO)}
        rows.append(row)
        for k in ("opening_debit", "opening_credit", "debit", "credit", "closing_debit", "closing_credit"):
            totals[k] += row[k]
    return present({"from": date_from.isoformat(), "to": date_to.isoformat(), "rows": rows,
                    "totals": dict(totals),
                    "balanced": totals["closing_debit"] == totals["closing_credit"]})


# ---------------------------------------------------------------------------- ledgers


def ledger(db: Session, business_id: uuid.UUID, *, account_ids: list[uuid.UUID], date_from: date,
           date_to: date, party_id: uuid.UUID | None = None,
           cost_centre_id: uuid.UUID | None = None) -> dict[str, Any]:
    """General ledger for one or more accounts (also cash book, bank book, party ledgers)."""
    ob = _sums(db, business_id, date_to=date_from - timedelta(days=1), party_id=party_id,
               cost_centre_id=cost_centre_id, account_ids=account_ids)
    opening = sum((dr - cr for dr, cr in ob.values()), ZERO)
    stmt = (select(JournalLine, JournalEntry)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalLine.business_id == business_id, JournalLine.account_id.in_(account_ids),
                   JournalLine.entry_date >= date_from, JournalLine.entry_date <= date_to)
            .order_by(JournalLine.entry_date, JournalEntry.posted_at, JournalLine.line_no))
    if party_id:
        stmt = stmt.where(JournalLine.party_id == party_id)
    if cost_centre_id:
        stmt = stmt.where(JournalLine.cost_centre_id == cost_centre_id)
    running, lines = opening, []
    for jl, je in db.execute(stmt):
        running += jl.debit - jl.credit
        lines.append({"date": jl.entry_date, "entry_id": je.id, "number": je.number,
                      "source_type": je.source_type.value, "source_ref": je.source_ref,
                      "narration": je.narration, "description": jl.description,
                      "party_id": jl.party_id, "debit": jl.debit, "credit": jl.credit,
                      "balance": running})
    return present({"from": date_from.isoformat(), "to": date_to.isoformat(), "opening": opening,
                    "lines": lines, "closing": running,
                    "total_debit": sum((x["debit"] for x in lines), ZERO),
                    "total_credit": sum((x["credit"] for x in lines), ZERO)})


def day_book(db: Session, business_id: uuid.UUID, *, date_from: date, date_to: date) -> list[dict[str, Any]]:
    accounts = _accounts(db, business_id)
    entries = db.scalars(select(JournalEntry).where(
        JournalEntry.business_id == business_id, JournalEntry.entry_date >= date_from,
        JournalEntry.entry_date <= date_to).order_by(JournalEntry.entry_date, JournalEntry.posted_at))
    return present([{"entry_id": e.id, "number": e.number, "date": e.entry_date,
                     "source_type": e.source_type.value, "source_ref": e.source_ref,
                     "narration": e.narration,
                     "lines": [{"account": f"{accounts[ln.account_id].code} {accounts[ln.account_id].name}",
                                "debit": ln.debit, "credit": ln.credit, "party_id": ln.party_id}
                               for ln in e.lines]} for e in entries])


# ---------------------------------------------------------------------------- statements


def profit_and_loss(db: Session, business_id: uuid.UUID, *, date_from: date, date_to: date,
                    branch_id: uuid.UUID | None = None, cost_centre_id: uuid.UUID | None = None) -> dict[str, Any]:
    accounts = _accounts(db, business_id)
    sums = _sums(db, business_id, date_from=date_from, date_to=date_to, exclude_closing=True,
                 branch_id=branch_id, cost_centre_id=cost_centre_id)
    sections: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for aid, (dr, cr) in sums.items():
        a = accounts[aid]
        if dr == cr:
            continue  # e.g. an expense posted and cancelled within the period
        if a.type == AccountType.INCOME:
            sections["revenue" if a.subtype == S.SALES else "other_income"].append(_row(a, cr - dr))
        elif a.type == AccountType.EXPENSE:
            key = {S.COST_OF_GOODS: "cost_of_goods_sold", S.DEPRECIATION: "depreciation",
                   S.FINANCE_COST: "finance_costs"}.get(a.subtype, "operating_expenses")
            sections[key].append(_row(a, dr - cr))

    def total(k: str) -> Decimal:
        return sum((r["amount"] for r in sections.get(k, [])), ZERO)

    revenue, cogs = total("revenue"), total("cost_of_goods_sold")
    gross = revenue - cogs
    other = total("other_income")
    expenses = total("operating_expenses") + total("depreciation") + total("finance_costs")
    for rows in sections.values():
        rows.sort(key=lambda r: r["code"])
    return present({
        "from": date_from.isoformat(), "to": date_to.isoformat(),
        "revenue": sections.get("revenue", []), "total_revenue": revenue,
        "cost_of_goods_sold": sections.get("cost_of_goods_sold", []), "total_cost_of_goods_sold": cogs,
        "gross_profit": gross,
        "gross_margin_percent": (gross * 100 / revenue).quantize(Decimal("0.01")) if revenue else None,
        "other_income": sections.get("other_income", []), "total_other_income": other,
        "operating_expenses": sections.get("operating_expenses", []),
        "depreciation": sections.get("depreciation", []), "finance_costs": sections.get("finance_costs", []),
        "total_expenses": expenses,
        "net_profit": gross + other - expenses,
    })


_BS_GROUPS = {
    "current_assets": (S.CASH, S.BANK, S.RECEIVABLE, S.INVENTORY, S.TAX_ASSET, S.CURRENT_ASSET),
    "fixed_assets": (S.FIXED_ASSET, S.ACCUMULATED_DEPRECIATION),
    "other_assets": (S.OTHER_ASSET,),
    "current_liabilities": (S.PAYABLE, S.TAX_LIABILITY, S.CURRENT_LIABILITY),
    "long_term_liabilities": (S.LOAN, S.OTHER_LIABILITY),
    "equity": (S.CAPITAL, S.RETAINED_EARNINGS, S.OPENING_BALANCE, S.DRAWINGS),
}


def balance_sheet(db: Session, business_id: uuid.UUID, *, as_of: date) -> dict[str, Any]:
    accounts = _accounts(db, business_id)
    sums = _sums(db, business_id, date_to=as_of)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unclosed_profit = ZERO
    for aid, (dr, cr) in sums.items():
        a = accounts[aid]
        bal = dr - cr
        if a.type in (AccountType.INCOME, AccountType.EXPENSE):
            unclosed_profit -= bal  # results not yet moved to retained earnings
            continue
        group = next(g for g, subs in _BS_GROUPS.items() if a.subtype in subs)
        if bal:
            groups[group].append(_row(a, bal if a.type == AccountType.ASSET else -bal))
    for rows in groups.values():
        rows.sort(key=lambda r: r["code"])

    def total(*names: str) -> Decimal:
        return sum((r["amount"] for n in names for r in groups.get(n, [])), ZERO)

    assets = total("current_assets", "fixed_assets", "other_assets")
    liabilities = total("current_liabilities", "long_term_liabilities")
    equity = total("equity") + unclosed_profit
    return present({
        "as_of": as_of.isoformat(),
        "assets": {g: groups.get(g, []) for g in ("current_assets", "fixed_assets", "other_assets")},
        "total_assets": assets,
        "liabilities": {g: groups.get(g, []) for g in ("current_liabilities", "long_term_liabilities")},
        "total_liabilities": liabilities,
        "equity": groups.get("equity", []), "profit_not_yet_closed": unclosed_profit,
        "total_equity": equity,
        "balanced": assets == liabilities + equity,
    })


def cash_flow(db: Session, business_id: uuid.UUID, *, date_from: date, date_to: date) -> dict[str, Any]:
    """Direct method: every movement in cash/bank accounts, classified by what the other side of
    its journal entry was (fixed assets → investing; loans/capital → financing; else operating)."""
    accounts = _accounts(db, business_id)
    cash_ids = [a.id for a in accounts.values() if a.subtype in (S.CASH, S.BANK)]
    opening = sum((dr - cr for dr, cr in _sums(db, business_id, date_to=date_from - timedelta(days=1),
                                                 account_ids=cash_ids).values()), ZERO)
    flows: dict[str, dict[str, Decimal]] = {k: defaultdict(lambda: ZERO)
                                            for k in ("operating", "investing", "financing")}
    entries = db.scalars(select(JournalEntry).where(
        JournalEntry.business_id == business_id, JournalEntry.entry_date >= date_from,
        JournalEntry.entry_date <= date_to, JournalEntry.source_type != JournalSource.CLOSING))
    for e in entries:
        cash_move = sum((ln.debit - ln.credit for ln in e.lines if ln.account_id in cash_ids), ZERO)
        if not cash_move:
            continue  # includes transfers between two cash/bank accounts
        others = [accounts[ln.account_id] for ln in e.lines if ln.account_id not in cash_ids]
        subs = {a.subtype for a in others}
        if subs & {S.FIXED_ASSET, S.ACCUMULATED_DEPRECIATION}:
            section = "investing"
        elif subs & {S.LOAN, S.CAPITAL, S.DRAWINGS}:
            section = "financing"
        else:
            section = "operating"
        label = {JournalSource.DOCUMENT: "Sales & purchases settled at the counter",
                 JournalSource.PAYMENT: "Receipts from customers" if cash_move > 0 else "Payments to suppliers",
                 JournalSource.EXPENSE: "Expenses paid"}.get(e.source_type, others[0].name if others else "Other")
        flows[section][label] += cash_move
    out = {k: [{"item": item, "amount": amt} for item, amt in sorted(v.items())] for k, v in flows.items()}
    totals = {f"net_{k}": sum(v.values(), ZERO) for k, v in flows.items()}
    net = sum(totals.values(), ZERO)
    return present({"from": date_from.isoformat(), "to": date_to.isoformat(), "opening_cash": opening,
                    **out, **totals, "net_change": net, "closing_cash": opening + net})


# ---------------------------------------------------------------------------- receivables & payables


def aging(db: Session, business_id: uuid.UUID, *, side: str, as_of: date) -> dict[str, Any]:
    """Outstanding invoices by days past due, per party, with unapplied credits alongside."""
    plus = (DocType.SALES_INVOICE, DocType.DEBIT_NOTE) if side == "receivable" else (DocType.PURCHASE_INVOICE,)
    minus = (DocType.CREDIT_NOTE,) if side == "receivable" else (DocType.PURCHASE_RETURN,)
    buckets = ("not_due", "1_30", "31_60", "61_90", "over_90")
    parties: dict[uuid.UUID | None, dict[str, Any]] = {}

    def entry(pid: uuid.UUID | None, name: str | None) -> dict[str, Any]:
        return parties.setdefault(pid, {"party_id": pid, "name": name or "Cash / walk-in",
                                        **dict.fromkeys(buckets, ZERO), "credits": ZERO})

    docs = db.scalars(select(Document).where(
        Document.business_id == business_id, Document.status == DocStatus.FINAL,
        Document.doc_type.in_(plus + minus), Document.balance_due > 0, Document.doc_date <= as_of))
    for d in docs:
        e = entry(d.party_id, d.party_name)
        if d.doc_type in minus:
            e["credits"] += d.balance_due
            continue
        days = (as_of - (d.due_date or d.doc_date)).days
        b = "not_due" if days <= 0 else "1_30" if days <= 30 else "31_60" if days <= 60 else \
            "61_90" if days <= 90 else "over_90"
        e[b] += d.balance_due
    direction = PaymentDirection.IN if side == "receivable" else PaymentDirection.OUT
    for pay in db.scalars(select(Payment).where(
            Payment.business_id == business_id, Payment.direction == direction,
            Payment.status == PaymentState.POSTED, Payment.payment_date <= as_of)):
        advance = pay.amount - sum((a.amount for a in pay.allocations), ZERO)
        if advance > 0:
            name = db.get(Party, pay.party_id).name if pay.party_id else None
            entry(pay.party_id, name)["credits"] += advance
    rows = []
    for e in parties.values():
        e["total_due"] = sum((e[b] for b in buckets), ZERO)
        e["net"] = e["total_due"] - e["credits"]
        rows.append(e)
    rows.sort(key=lambda r: -r["net"])
    totals = {k: sum((r[k] for r in rows), ZERO) for k in (*buckets, "credits", "total_due", "net")}
    return present({"side": side, "as_of": as_of.isoformat(), "parties": rows, "totals": totals})


def party_balance_from_books(db: Session, business_id: uuid.UUID, party_id: uuid.UUID, side: str) -> Decimal:
    """What the party owes us (receivable) or we owe them (payable), per the general ledger."""
    key = "ar" if side == "receivable" else "ap"
    acc = Accounts(db, business_id)[key]
    dr, cr = _sums(db, business_id, party_id=party_id, account_ids=[acc.id]).get(acc.id, (ZERO, ZERO))
    return dr - cr if side == "receivable" else cr - dr


def verify_books(db: Session, business_id: uuid.UUID) -> list[str]:
    """Integrity checks across subsystems. Empty list = consistent."""
    from app.modules.inventory.models import StockBalance

    problems = []
    for e in db.scalars(select(JournalEntry).where(JournalEntry.business_id == business_id)):
        dr = sum((ln.debit for ln in e.lines), ZERO)
        cr = sum((ln.credit for ln in e.lines), ZERO)
        if dr != cr:
            problems.append(f"{e.number} does not balance ({dr} vs {cr})")
    acc = Accounts(db, business_id)
    inv = _sums(db, business_id, account_ids=[acc["inventory"].id]).get(acc["inventory"].id, (ZERO, ZERO))
    stock = Decimal(db.scalar(select(func.coalesce(func.sum(StockBalance.value), 0)).where(
        StockBalance.business_id == business_id)) or 0)
    if inv[0] - inv[1] != stock:
        problems.append(f"Inventory account {inv[0] - inv[1]} ≠ stock valuation {stock}")
    return problems
