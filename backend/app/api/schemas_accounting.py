"""Schemas for the accounting API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.api.schemas_billing import MoneyOut
from app.api.schemas_masters import MoneyIn, RateIn
from app.core.clock import local_today
from app.modules.accounting.models import (
    AccountSubtype,
    AccountType,
    DepreciationMethod,
    ExpenseStatus,
    JournalSource,
)

Code = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9\-_.]{1,20}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AccountIn(BaseModel):
    code: Code
    name: Name
    subtype: AccountSubtype
    parent_id: uuid.UUID | None = None
    description: Annotated[str, StringConstraints(max_length=300)] | None = None
    bank_details: dict[str, str] | None = None


class AccountUpdateIn(BaseModel):
    code: Code | None = None
    name: Name | None = None
    subtype: AccountSubtype | None = None
    parent_id: uuid.UUID | None = None
    description: Annotated[str, StringConstraints(max_length=300)] | None = None
    bank_details: dict[str, str] | None = None
    is_active: bool | None = None


class AccountOut(ORM):
    id: uuid.UUID
    code: str
    name: str
    type: AccountType
    subtype: AccountSubtype
    parent_id: uuid.UUID | None
    system_key: str | None
    description: str | None
    bank_details: dict[str, Any] | None
    is_active: bool
    balance: MoneyOut | None = None


class CostCentreIn(BaseModel):
    code: Code
    name: Name


class CostCentreUpdateIn(BaseModel):
    name: Name | None = None
    is_active: bool | None = None


class CostCentreOut(ORM):
    id: uuid.UUID
    code: str
    name: str
    is_active: bool


class JournalLineIn(BaseModel):
    account_id: uuid.UUID
    debit: MoneyIn = Decimal("0.0000")
    credit: MoneyIn = Decimal("0.0000")
    party_id: uuid.UUID | None = None
    cost_centre_id: uuid.UUID | None = None
    description: Annotated[str, StringConstraints(max_length=300)] | None = None

    @model_validator(mode="after")
    def _one_side(self) -> JournalLineIn:
        if (self.debit == 0) == (self.credit == 0):
            raise ValueError("Each line needs either a debit or a credit")
        return self


class JournalIn(BaseModel):
    entry_date: date
    narration: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
    branch_id: uuid.UUID | None = None
    lines: list[JournalLineIn] = Field(min_length=2, max_length=200)


class OpeningBalancesIn(BaseModel):
    as_of: date = Field(description="First day of the financial year")
    lines: list[JournalLineIn] = Field(min_length=1, max_length=2000)


class ReverseIn(BaseModel):
    reason: Reason
    on: date | None = None


class JournalLineOut(ORM):
    id: uuid.UUID
    line_no: int
    account_id: uuid.UUID
    debit: MoneyOut
    credit: MoneyOut
    party_id: uuid.UUID | None
    cost_centre_id: uuid.UUID | None
    description: str | None


class JournalOut(ORM):
    id: uuid.UUID
    number: str
    entry_date: date
    source_type: JournalSource
    source_id: uuid.UUID | None
    source_event: str
    source_ref: str | None
    narration: str | None
    branch_id: uuid.UUID | None
    reverses_id: uuid.UUID | None
    posted_at: datetime
    lines: list[JournalLineOut]


class ExpenseIn(BaseModel):
    expense_date: date = Field(default_factory=local_today)
    account_id: uuid.UUID
    amount: MoneyIn = Field(description="Before GST")
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=300)]
    gst_rate: RateIn = Decimal("0.0000")
    itc_eligible: bool = True
    is_interstate: bool | None = None
    paid_from_account_id: uuid.UUID | None = None
    party_id: uuid.UUID | None = None
    cost_centre_id: uuid.UUID | None = None
    reference: Annotated[str, StringConstraints(max_length=60)] | None = None
    branch_id: uuid.UUID | None = None


class ExpenseOut(ORM):
    id: uuid.UUID
    number: str
    expense_date: date
    account_id: uuid.UUID
    paid_from_account_id: uuid.UUID | None
    party_id: uuid.UUID | None
    cost_centre_id: uuid.UUID | None
    branch_id: uuid.UUID
    description: str
    reference: str | None
    amount: MoneyOut
    gst_rate: Decimal
    itc_eligible: bool
    is_interstate: bool
    cgst: MoneyOut
    sgst: MoneyOut
    igst: MoneyOut
    total: MoneyOut
    status: ExpenseStatus
    cancel_reason: str | None
    created_at: datetime


class AssetIn(BaseModel):
    code: Code
    name: Name
    asset_account_id: uuid.UUID
    acquired_on: date
    cost: MoneyIn
    salvage_value: MoneyIn = Decimal("0.0000")
    method: DepreciationMethod
    useful_life_months: int | None = Field(None, ge=1, le=1200)
    annual_rate: RateIn | None = None
    branch_id: uuid.UUID | None = None
    cost_centre_id: uuid.UUID | None = None
    notes: Annotated[str, StringConstraints(max_length=2000)] | None = None
    it_block: Annotated[str, StringConstraints(pattern=r"^[a-z_]{2,30}$")] | None = Field(
        None, description="Income-tax Act block, e.g. computer, plant, furniture (see /assets/it-depreciation)")
    capitalise_from_account_id: uuid.UUID | None = Field(
        None, description="Also post the purchase: Dr asset, Cr this account (bank, loan, capital…)")


class AssetOut(ORM):
    id: uuid.UUID
    code: str
    name: str
    asset_account_id: uuid.UUID
    acquired_on: date
    cost: MoneyOut
    salvage_value: MoneyOut
    method: DepreciationMethod
    useful_life_months: int | None
    annual_rate: Decimal | None
    accumulated_depreciation: MoneyOut
    book_value: MoneyOut | None = None
    depreciated_to: date | None
    disposed_on: date | None
    it_block: str | None = None
    disposal_proceeds: Decimal | None = None



class DepreciationRunIn(BaseModel):
    period_end: date


class DisposeIn(BaseModel):
    on: date
    proceeds: MoneyIn = Decimal("0.0000")
    proceeds_account_id: uuid.UUID | None = None


class StatementLineIn(BaseModel):
    txn_date: date
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    reference: Annotated[str, StringConstraints(max_length=100)] | None = None
    amount: Annotated[Decimal, Field(max_digits=18, decimal_places=4)] = Field(
        description="Positive for deposits, negative for withdrawals")


class StatementImportIn(BaseModel):
    lines: list[StatementLineIn] | None = Field(None, max_length=10000)
    csv: Annotated[str, StringConstraints(max_length=5_000_000)] | None = None


class StatementLineOut(ORM):
    id: uuid.UUID
    txn_date: date
    description: str
    reference: str | None
    amount: MoneyOut
    matched_line_id: uuid.UUID | None


class MatchIn(BaseModel):
    journal_line_id: uuid.UUID


class CloseYearIn(BaseModel):
    financial_year_id: uuid.UUID


class ReopenYearIn(BaseModel):
    financial_year_id: uuid.UUID
    reason: Reason
