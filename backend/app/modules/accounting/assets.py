"""Fixed asset register, depreciation runs and disposals.

Depreciation is pro-rated by day: straight-line spreads (cost − salvage) over the useful life;
written-down value applies the annual rate to the remaining book value. An asset never
depreciates below its salvage value, and each period is posted once (``period_end`` is unique
per asset), so re-running a period is refused rather than doubled.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting.accounts import get_account, get_cost_centre
from app.modules.accounting.chart import Accounts
from app.modules.accounting.ledger import Line, post
from app.modules.accounting.models import (
    AccountSubtype,
    DepreciationEntry,
    DepreciationMethod,
    FixedAsset,
    JournalEntry,
    JournalSource,
)
from app.modules.audit import service as audit
from app.modules.gst.tax import ZERO

Q2 = Decimal("0.01")
DAYS_PER_MONTH = Decimal("30.436875")  # 365.2425 / 12


def _q2(d: Decimal) -> Decimal:
    return d.quantize(Q2, rounding=ROUND_HALF_UP)


def get_asset(db: Session, access: BusinessAccess, aid: uuid.UUID) -> FixedAsset:
    a = db.get(FixedAsset, aid)
    if a is None or a.business_id != access.business.id:
        raise NotFound("Fixed asset not found")
    return a


def create_asset(db: Session, access: BusinessAccess, data: dict[str, Any],
                 capitalise_from_account_id: uuid.UUID | None) -> FixedAsset:
    """Register an asset. If ``capitalise_from_account_id`` is given (bank, payable, capital…)
    its purchase is also posted: Dr asset account, Cr that account."""
    access.require("accounting.post")
    account = get_account(db, access, data["asset_account_id"])
    if account.subtype != AccountSubtype.FIXED_ASSET:
        raise ValidationFailed("Choose a fixed-asset account")
    if data["salvage_value"] > data["cost"]:
        raise ValidationFailed("Salvage value cannot exceed cost")
    if data["method"] == DepreciationMethod.STRAIGHT_LINE and not data.get("useful_life_months"):
        raise ValidationFailed("Straight-line depreciation needs a useful life")
    if data["method"] == DepreciationMethod.WRITTEN_DOWN and not data.get("annual_rate"):
        raise ValidationFailed("Written-down-value depreciation needs an annual rate")
    if data.get("cost_centre_id"):
        get_cost_centre(db, access, data["cost_centre_id"])
    if db.scalar(select(FixedAsset.id).where(FixedAsset.business_id == access.business.id,
                                             FixedAsset.code == data["code"])):
        raise Conflict(f"Asset code {data['code']} is already used")
    if data.get("it_block"):
        from app.modules.finance.service import IT_BLOCKS

        if data["it_block"] not in IT_BLOCKS:
            raise ValidationFailed(f"Income-tax block must be one of {', '.join(IT_BLOCKS)}")
    asset = FixedAsset(business_id=access.business.id, accumulated_depreciation=ZERO, **data)
    db.add(asset)
    db.flush()
    if capitalise_from_account_id:
        source = get_account(db, access, capitalise_from_account_id)
        if source.system_key in ("ar", "ap", "inventory"):
            raise ValidationFailed("Capitalise from a bank, cash, loan or capital account; record "
                                   "supplier bills as purchases or expenses")
        post(db, access.business.id, entry_date=asset.acquired_on, source_type=JournalSource.ASSET,
             source_id=asset.id, source_ref=asset.code, created_by=access.user.id,
             narration=f"Acquisition of {asset.name}",
             lines=[Line(account, debit=asset.cost), Line(source, credit=asset.cost)])
    audit.record(db, action="asset.created", entity_type="fixed_asset", entity_id=asset.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"code": asset.code, "cost": asset.cost})
    return asset


def depreciation_for(asset: FixedAsset, start: date, end: date) -> Decimal:
    days = Decimal((end - start).days + 1)
    if days <= 0:
        return ZERO
    room = asset.cost - asset.salvage_value - asset.accumulated_depreciation
    if room <= 0:
        return ZERO
    if asset.method == DepreciationMethod.STRAIGHT_LINE:
        per_day = (asset.cost - asset.salvage_value) / (Decimal(asset.useful_life_months) * DAYS_PER_MONTH)
        amount = per_day * days
    else:
        book = asset.cost - asset.accumulated_depreciation
        amount = book * Decimal(asset.annual_rate) / 100 * days / Decimal(365)
    return min(_q2(amount), room)


def run_depreciation(db: Session, access: BusinessAccess, *, period_end: date) -> JournalEntry | None:
    """Depreciate every active asset from where it last stopped up to ``period_end``."""
    access.require("accounting.post")
    acc = Accounts(db, access.business.id)
    assets = list(db.scalars(select(FixedAsset).where(
        FixedAsset.business_id == access.business.id, FixedAsset.disposed_on.is_(None),
        FixedAsset.acquired_on <= period_end).order_by(FixedAsset.code)))
    plan: list[tuple[FixedAsset, date, Decimal]] = []
    for a in assets:
        start = (a.depreciated_to + timedelta(days=1)) if a.depreciated_to else a.acquired_on
        if start > period_end:
            continue
        amount = depreciation_for(a, start, period_end)
        if amount > 0:
            plan.append((a, start, amount))
    if not plan:
        return None
    lines = []
    for a, _, amount in plan:
        lines.append(Line(acc["depreciation"], debit=amount, cost_centre_id=a.cost_centre_id,
                          description=a.code))
        lines.append(Line(acc["accumulated_depreciation"], credit=amount, description=a.code))
    entry = post(db, access.business.id, entry_date=period_end, source_type=JournalSource.DEPRECIATION,
                 lines=lines, created_by=access.user.id,
                 narration=f"Depreciation up to {period_end.isoformat()} ({len(plan)} assets)")
    assert entry is not None
    for a, start, amount in plan:
        db.add(DepreciationEntry(asset_id=a.id, period_start=start, period_end=period_end,
                                 amount=amount, journal_entry_id=entry.id))
        a.accumulated_depreciation += amount
        a.depreciated_to = period_end
    audit.record(db, action="depreciation.posted", entity_type="journal_entry", entity_id=entry.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"period_end": period_end, "assets": len(plan),
                          "total": sum((x[2] for x in plan), ZERO)})
    return entry


def dispose_asset(db: Session, access: BusinessAccess, asset: FixedAsset, *, on: date,
                  proceeds: Decimal, proceeds_account_id: uuid.UUID | None) -> JournalEntry:
    """Sale/scrap: remove cost and accumulated depreciation; the difference against the
    proceeds is a gain or loss on disposal."""
    access.require("accounting.post")
    if asset.disposed_on:
        raise Conflict("This asset is already disposed of")
    if asset.depreciated_to and on < asset.depreciated_to:
        raise ValidationFailed("Disposal date is before the last depreciation run")
    acc = Accounts(db, access.business.id)
    lines = [Line(get_account(db, access, asset.asset_account_id), credit=asset.cost)]
    if asset.accumulated_depreciation:
        lines.append(Line(acc["accumulated_depreciation"], debit=asset.accumulated_depreciation,
                          description=asset.code))
    asset.disposal_proceeds = proceeds
    if proceeds:
        if proceeds_account_id is None:
            raise ValidationFailed("Say which cash/bank account received the proceeds")
        lines.append(Line(get_account(db, access, proceeds_account_id), debit=proceeds))
    result = proceeds - (asset.cost - asset.accumulated_depreciation)
    if result:  # gain (credit) or loss (debit)
        a = acc["asset_disposal"]
        lines.append(Line(a, credit=result) if result > 0 else Line(a, debit=-result))
    entry = post(db, access.business.id, entry_date=on, source_type=JournalSource.ASSET,
                 source_id=asset.id, source_event="dispose", source_ref=asset.code, lines=lines,
                 created_by=access.user.id, narration=f"Disposal of {asset.name}")
    assert entry is not None
    asset.disposed_on = on
    audit.record(db, action="asset.disposed", entity_type="fixed_asset", entity_id=asset.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"on": on, "proceeds": proceeds, "gain_loss": result})
    return entry
