"""Chart of accounts, journals, expenses, fixed assets, bank reconciliation, year closing and
financial reports."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.api.deps import DB, Access, OwnerAccess, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas_accounting import (
    AccountIn,
    AccountOut,
    AccountUpdateIn,
    AssetIn,
    AssetOut,
    CloseYearIn,
    CostCentreIn,
    CostCentreOut,
    CostCentreUpdateIn,
    DepreciationRunIn,
    DisposeIn,
    ExpenseIn,
    ExpenseOut,
    JournalIn,
    JournalOut,
    MatchIn,
    OpeningBalancesIn,
    ReopenYearIn,
    ReverseIn,
    StatementImportIn,
    StatementLineOut,
)
from app.core.clock import local_today
from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting import accounts as acc_service
from app.modules.accounting import assets as asset_service
from app.modules.accounting import bank, closing, reports
from app.modules.accounting import expenses as expense_service
from app.modules.accounting.chart import system_account
from app.modules.accounting.models import (
    AccountSubtype,
    Expense,
    FixedAsset,
    JournalEntry,
    JournalSource,
)
from app.modules.parties.models import Party

router = APIRouter(prefix="/businesses/{business_id}/accounting", tags=["accounting"])

View = Annotated[BusinessAccess, Depends(require("accounting.view"))]
Post = Annotated[BusinessAccess, Depends(require("accounting.post"))]
ProfitView = Annotated[BusinessAccess, Depends(require("accounting.view", "profit.view"))]


def _period(date_from: date, date_to: date) -> None:
    if date_to < date_from:
        raise ValidationFailed("date_to must be on or after date_from")


# ---------------------------------------------------------------------------- accounts


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(access: View, db: DB, include_inactive: bool = False,
                  with_balances: bool = Query(False, description="Add balances as of today")) -> list[AccountOut]:
    rows = acc_service.list_accounts(db, access, include_inactive=include_inactive)
    out = [AccountOut.model_validate(a) for a in rows]
    if with_balances:
        for o in out:
            o.balance = acc_service.account_balance(db, o.id)
    return out


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(body: AccountIn, access: Post, db: DB) -> AccountOut:
    a = acc_service.create_account(db, access, body.model_dump())
    db.commit()
    return AccountOut.model_validate(a)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: uuid.UUID, body: AccountUpdateIn, access: Post, db: DB) -> AccountOut:
    a = acc_service.update_account(db, access, acc_service.get_account(db, access, account_id),
                                   body.model_dump(exclude_unset=True))
    db.commit()
    return AccountOut.model_validate(a)


@router.get("/cost-centres", response_model=list[CostCentreOut])
def list_cost_centres(access: View, db: DB) -> list[CostCentreOut]:
    return [CostCentreOut.model_validate(c) for c in acc_service.list_cost_centres(db, access)]


@router.post("/cost-centres", response_model=CostCentreOut, status_code=status.HTTP_201_CREATED)
def create_cost_centre(body: CostCentreIn, access: Post, db: DB) -> CostCentreOut:
    c = acc_service.save_cost_centre(db, access, None, body.model_dump())
    db.commit()
    return CostCentreOut.model_validate(c)


@router.patch("/cost-centres/{cost_centre_id}", response_model=CostCentreOut)
def update_cost_centre(cost_centre_id: uuid.UUID, body: CostCentreUpdateIn, access: Post, db: DB) -> CostCentreOut:
    c = acc_service.save_cost_centre(db, access, acc_service.get_cost_centre(db, access, cost_centre_id),
                                     body.model_dump(exclude_unset=True))
    db.commit()
    return CostCentreOut.model_validate(c)


# ---------------------------------------------------------------------------- journals


@router.get("/journals", response_model=Page[JournalOut])
def list_journals(access: View, db: DB, params: Annotated[PageParams, Depends(page_params)],
                  date_from: date | None = None, date_to: date | None = None,
                  source_type: JournalSource | None = None,
                  q: str | None = Query(None, max_length=60, description="Journal number or source reference")
                  ) -> Page[JournalOut]:
    stmt = (select(JournalEntry).where(JournalEntry.business_id == access.business.id)
            .order_by(JournalEntry.entry_date.desc(), JournalEntry.posted_at.desc()))
    if date_from:
        stmt = stmt.where(JournalEntry.entry_date >= date_from)
    if date_to:
        stmt = stmt.where(JournalEntry.entry_date <= date_to)
    if source_type:
        stmt = stmt.where(JournalEntry.source_type == source_type)
    if q:
        stmt = stmt.where((JournalEntry.number == q) | (JournalEntry.source_ref == q))
    rows, total = paginate(db, stmt, params)
    return Page(items=[JournalOut.model_validate(e) for e in rows], total=total,
                limit=params.limit, offset=params.offset)


@router.get("/journals/{entry_id}", response_model=JournalOut)
def get_journal(entry_id: uuid.UUID, access: View, db: DB) -> JournalOut:
    return JournalOut.model_validate(acc_service.get_entry(db, access, entry_id))


@router.post("/journals", response_model=JournalOut, status_code=status.HTTP_201_CREATED)
def post_journal(body: JournalIn, access: Post, db: DB) -> JournalOut:
    e = acc_service.manual_journal(db, access, entry_date=body.entry_date, narration=body.narration,
                                   lines=[ln.model_dump() for ln in body.lines], branch_id=body.branch_id)
    db.commit()
    return JournalOut.model_validate(e)


@router.post("/journals/{entry_id}/reverse", response_model=JournalOut, status_code=status.HTTP_201_CREATED)
def reverse_journal(entry_id: uuid.UUID, body: ReverseIn, access: Post, db: DB) -> JournalOut:
    e = acc_service.reverse_manual(db, access, acc_service.get_entry(db, access, entry_id),
                                   on=body.on or local_today(), reason=body.reason)
    db.commit()
    return JournalOut.model_validate(e)


@router.post("/opening-balances", response_model=JournalOut, status_code=status.HTTP_201_CREATED)
def opening_balances(body: OpeningBalancesIn, access: Post, db: DB) -> JournalOut:
    e = acc_service.post_opening_balances(db, access, as_of=body.as_of,
                                          lines=[ln.model_dump() for ln in body.lines])
    db.commit()
    return JournalOut.model_validate(e)


# ---------------------------------------------------------------------------- expenses


@router.get("/expenses", response_model=Page[ExpenseOut], tags=["expenses"])
def list_expenses(access: Annotated[BusinessAccess, Depends(require("expenses.view"))], db: DB,
                  params: Annotated[PageParams, Depends(page_params)], date_from: date | None = None,
                  date_to: date | None = None, account_id: uuid.UUID | None = None) -> Page[ExpenseOut]:
    stmt = select(Expense).where(Expense.business_id == access.business.id).order_by(
        Expense.expense_date.desc(), Expense.number.desc())
    if not access.all_branches:
        stmt = stmt.where(Expense.branch_id.in_(access.branch_ids))
    if date_from:
        stmt = stmt.where(Expense.expense_date >= date_from)
    if date_to:
        stmt = stmt.where(Expense.expense_date <= date_to)
    if account_id:
        stmt = stmt.where(Expense.account_id == account_id)
    rows, total = paginate(db, stmt, params)
    return Page(items=[ExpenseOut.model_validate(e) for e in rows], total=total,
                limit=params.limit, offset=params.offset)


@router.post("/expenses", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED, tags=["expenses"])
def create_expense(body: ExpenseIn, access: Annotated[BusinessAccess, Depends(require("expenses.manage"))],
                   db: DB) -> ExpenseOut:
    e = expense_service.create_expense(db, access, **body.model_dump())
    db.commit()
    return ExpenseOut.model_validate(e)


@router.post("/expenses/{expense_id}/cancel", response_model=ExpenseOut, tags=["expenses"])
def cancel_expense(expense_id: uuid.UUID, body: ReverseIn,
                   access: Annotated[BusinessAccess, Depends(require("expenses.manage"))], db: DB) -> ExpenseOut:
    e = expense_service.cancel_expense(db, access, expense_service.get_expense(db, access, expense_id),
                                       reason=body.reason, on=body.on or local_today())
    db.commit()
    return ExpenseOut.model_validate(e)


# ---------------------------------------------------------------------------- fixed assets


def _asset_out(a: FixedAsset) -> AssetOut:
    out = AssetOut.model_validate(a)
    out.book_value = a.cost - a.accumulated_depreciation
    return out


@router.get("/fixed-assets", response_model=list[AssetOut], tags=["fixed assets"])
def list_assets(access: View, db: DB) -> list[AssetOut]:
    rows = db.scalars(select(FixedAsset).where(FixedAsset.business_id == access.business.id)
                      .order_by(FixedAsset.code))
    return [_asset_out(a) for a in rows]


@router.post("/fixed-assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED, tags=["fixed assets"])
def create_asset(body: AssetIn, access: Post, db: DB) -> AssetOut:
    data = body.model_dump(exclude={"capitalise_from_account_id"})
    a = asset_service.create_asset(db, access, data, body.capitalise_from_account_id)
    db.commit()
    return _asset_out(a)


@router.post("/fixed-assets/depreciation", response_model=JournalOut | None, tags=["fixed assets"])
def run_depreciation(body: DepreciationRunIn, access: Post, db: DB) -> JournalOut | None:
    e = asset_service.run_depreciation(db, access, period_end=body.period_end)
    db.commit()
    return JournalOut.model_validate(e) if e else None


@router.post("/fixed-assets/{asset_id}/dispose", response_model=JournalOut, tags=["fixed assets"])
def dispose_asset(asset_id: uuid.UUID, body: DisposeIn, access: Post, db: DB) -> JournalOut:
    e = asset_service.dispose_asset(db, access, asset_service.get_asset(db, access, asset_id),
                                    on=body.on, proceeds=body.proceeds,
                                    proceeds_account_id=body.proceeds_account_id)
    db.commit()
    return JournalOut.model_validate(e)


# ---------------------------------------------------------------------------- bank reconciliation


@router.post("/bank/{account_id}/statement", tags=["bank reconciliation"])
def import_statement(account_id: uuid.UUID, body: StatementImportIn, access: Post, db: DB) -> dict:
    if (body.lines is None) == (body.csv is None):
        raise ValidationFailed("Send either lines or csv")
    lines = [ln.model_dump() for ln in body.lines] if body.lines is not None else bank.parse_csv(body.csv or "")
    added, skipped = bank.import_lines(db, access, account_id, lines)
    matched = bank.auto_match(db, access, account_id)
    db.commit()
    return {"added": added, "skipped_duplicates": skipped, "auto_matched": matched}


@router.get("/bank/{account_id}/statement", response_model=list[StatementLineOut], tags=["bank reconciliation"])
def statement(account_id: uuid.UUID, access: View, db: DB, only_unmatched: bool = False) -> list[StatementLineOut]:
    return [StatementLineOut.model_validate(s)
            for s in bank.statement_lines(db, access, account_id, only_unmatched=only_unmatched)]


@router.post("/bank/{account_id}/auto-match", tags=["bank reconciliation"])
def auto_match(account_id: uuid.UUID, access: Post, db: DB) -> dict:
    n = bank.auto_match(db, access, account_id)
    db.commit()
    return {"matched": n}


@router.post("/bank/statement-lines/{line_id}/match", response_model=StatementLineOut, tags=["bank reconciliation"])
def match_line(line_id: uuid.UUID, body: MatchIn, access: Post, db: DB) -> StatementLineOut:
    s = bank.match(db, access, line_id, body.journal_line_id)
    db.commit()
    return StatementLineOut.model_validate(s)


@router.post("/bank/statement-lines/{line_id}/unmatch", response_model=StatementLineOut, tags=["bank reconciliation"])
def unmatch_line(line_id: uuid.UUID, access: Post, db: DB) -> StatementLineOut:
    s = bank.unmatch(db, access, line_id)
    db.commit()
    return StatementLineOut.model_validate(s)


@router.get("/bank/{account_id}/reconciliation", tags=["bank reconciliation"])
def reconciliation(account_id: uuid.UUID, access: View, db: DB, as_of: date = Query(default_factory=local_today),
                   statement_opening: Decimal = Decimal(0)) -> dict:
    r = bank.reconciliation(db, access, account_id, as_of=as_of, statement_opening=statement_opening)
    return reports.present(r.__dict__)


# ---------------------------------------------------------------------------- year end


@router.post("/close-year", response_model=JournalOut | None, tags=["year end"])
def close_year(body: CloseYearIn, access: Post, db: DB) -> JournalOut | None:
    e = closing.close_year(db, access, body.financial_year_id)
    db.commit()
    return JournalOut.model_validate(e) if e else None


@router.post("/reopen-year", tags=["year end"])
def reopen_year(body: ReopenYearIn, access: OwnerAccess, db: DB) -> dict:
    fy = closing.reopen_year(db, access, body.financial_year_id, reason=body.reason)
    db.commit()
    return {"financial_year_id": fy.id, "code": fy.code, "is_closed": fy.is_closed}


# ---------------------------------------------------------------------------- reports


@router.get("/reports/trial-balance", tags=["accounting reports"])
def trial_balance(access: View, db: DB, date_from: date, date_to: date) -> dict:
    _period(date_from, date_to)
    return reports.trial_balance(db, access.business.id, date_from=date_from, date_to=date_to)


@router.get("/reports/ledger", tags=["accounting reports"])
def general_ledger(access: View, db: DB, account_id: uuid.UUID, date_from: date, date_to: date,
                   party_id: uuid.UUID | None = None, cost_centre_id: uuid.UUID | None = None) -> dict:
    _period(date_from, date_to)
    acc_service.get_account(db, access, account_id)
    return reports.ledger(db, access.business.id, account_ids=[account_id], date_from=date_from,
                          date_to=date_to, party_id=party_id, cost_centre_id=cost_centre_id)


@router.get("/reports/cash-book", tags=["accounting reports"])
def cash_book(access: View, db: DB, date_from: date, date_to: date,
              kind: str = Query("cash", pattern="^(cash|bank)$")) -> dict:
    _period(date_from, date_to)
    subtype = AccountSubtype.CASH if kind == "cash" else AccountSubtype.BANK
    ids = [a.id for a in acc_service.list_accounts(db, access) if a.subtype == subtype]
    return reports.ledger(db, access.business.id, account_ids=ids, date_from=date_from, date_to=date_to)


@router.get("/reports/day-book", tags=["accounting reports"])
def day_book(access: View, db: DB, date_from: date, date_to: date) -> list:
    _period(date_from, date_to)
    if (date_to - date_from).days > 92:
        raise ValidationFailed("The day book covers at most three months at a time")
    return reports.day_book(db, access.business.id, date_from=date_from, date_to=date_to)


@router.get("/reports/party-ledger", tags=["accounting reports"])
def party_ledger(access: Access, db: DB, party_id: uuid.UUID, date_from: date, date_to: date,
                 side: str = Query("receivable", pattern="^(receivable|payable)$")) -> dict:
    """Customer ledger (receivable) or supplier ledger (payable), from the books."""
    access.require("customer_balances.view" if side == "receivable" else "supplier_balances.view")
    _period(date_from, date_to)
    p = db.get(Party, party_id)
    if p is None or p.business_id != access.business.id:
        raise ValidationFailed("Party not found")
    key = "ar" if side == "receivable" else "ap"
    out = reports.ledger(db, access.business.id, account_ids=[system_account(db, access.business.id, key).id],
                         date_from=date_from, date_to=date_to, party_id=party_id)
    out["party"] = {"id": p.id, "name": p.name, "gstin": p.gstin}
    return out


@router.get("/reports/aging", tags=["accounting reports"])
def aging(access: Access, db: DB, side: str = Query("receivable", pattern="^(receivable|payable)$"),
          as_of: date = Query(default_factory=local_today)) -> dict:
    access.require("customer_balances.view" if side == "receivable" else "supplier_balances.view")
    return reports.aging(db, access.business.id, side=side, as_of=as_of)


@router.get("/reports/profit-and-loss", tags=["accounting reports"])
def profit_and_loss(access: ProfitView, db: DB, date_from: date, date_to: date,
                    branch_id: uuid.UUID | None = None, cost_centre_id: uuid.UUID | None = None) -> dict:
    _period(date_from, date_to)
    return reports.profit_and_loss(db, access.business.id, date_from=date_from, date_to=date_to,
                                   branch_id=branch_id, cost_centre_id=cost_centre_id)


@router.get("/reports/balance-sheet", tags=["accounting reports"])
def balance_sheet(access: View, db: DB, as_of: date = Query(default_factory=local_today)) -> dict:
    return reports.balance_sheet(db, access.business.id, as_of=as_of)


@router.get("/reports/cash-flow", tags=["accounting reports"])
def cash_flow(access: View, db: DB, date_from: date, date_to: date) -> dict:
    _period(date_from, date_to)
    return reports.cash_flow(db, access.business.id, date_from=date_from, date_to=date_to)


@router.get("/reports/integrity", tags=["accounting reports"])
def integrity(access: View, db: DB) -> dict:
    """Cross-checks: every journal balances; the Inventory account equals stock valuation."""
    problems = reports.verify_books(db, access.business.id)
    return {"ok": not problems, "problems": problems}
