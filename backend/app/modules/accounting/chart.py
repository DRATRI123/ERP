"""Default chart of accounts (Indian small-business layout) and system-account lookup.

System accounts carry a ``system_key`` so automatic postings never depend on names or codes the
owner may change. Every business gets the chart when it is created; businesses created before
accounting existed get it on first use (``ensure_chart``).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.accounting.models import SUBTYPE_TYPE, Account
from app.modules.accounting.models import AccountSubtype as S

# (code, name, subtype, system_key)
DEFAULT_CHART: list[tuple[str, str, S, str | None]] = [
    ("1000", "Cash in Hand", S.CASH, "cash"),
    ("1010", "Bank Account", S.BANK, "bank"),
    ("1020", "Petty Cash", S.CASH, "petty_cash"),
    ("1100", "Accounts Receivable (Debtors)", S.RECEIVABLE, "ar"),
    ("1200", "Inventory", S.INVENTORY, "inventory"),
    ("1300", "Input CGST", S.TAX_ASSET, "input_cgst"),
    ("1301", "Input SGST/UTGST", S.TAX_ASSET, "input_sgst"),
    ("1302", "Input IGST", S.TAX_ASSET, "input_igst"),
    ("1303", "Input Cess", S.TAX_ASSET, "input_cess"),
    ("1310", "TDS Receivable", S.TAX_ASSET, "tds_receivable"),
    ("1400", "Advances & Deposits", S.CURRENT_ASSET, "advances"),
    ("1500", "Furniture & Equipment", S.FIXED_ASSET, "fixed_assets"),
    ("1510", "Computers", S.FIXED_ASSET, None),
    ("1520", "Vehicles", S.FIXED_ASSET, None),
    ("1590", "Accumulated Depreciation", S.ACCUMULATED_DEPRECIATION, "accumulated_depreciation"),
    ("2000", "Accounts Payable (Creditors)", S.PAYABLE, "ap"),
    ("2100", "Output CGST", S.TAX_LIABILITY, "output_cgst"),
    ("2101", "Output SGST/UTGST", S.TAX_LIABILITY, "output_sgst"),
    ("2102", "Output IGST", S.TAX_LIABILITY, "output_igst"),
    ("2103", "Output Cess", S.TAX_LIABILITY, "output_cess"),
    ("2110", "CGST Payable (Reverse Charge)", S.TAX_LIABILITY, "rcm_cgst"),
    ("2111", "SGST/UTGST Payable (Reverse Charge)", S.TAX_LIABILITY, "rcm_sgst"),
    ("2112", "IGST Payable (Reverse Charge)", S.TAX_LIABILITY, "rcm_igst"),
    ("2113", "Cess Payable (Reverse Charge)", S.TAX_LIABILITY, "rcm_cess"),
    ("2120", "TDS Payable", S.TAX_LIABILITY, "tds_payable"),
    ("2200", "Landed Cost Clearing", S.CURRENT_LIABILITY, "landed_cost_clearing"),
    ("2210", "Salaries Payable", S.CURRENT_LIABILITY, "salaries_payable"),
    ("2220", "PF Payable", S.CURRENT_LIABILITY, "pf_payable"),
    ("2221", "ESI Payable", S.CURRENT_LIABILITY, "esi_payable"),
    ("2222", "Professional Tax Payable", S.CURRENT_LIABILITY, "pt_payable"),
    ("2300", "Loans", S.LOAN, None),
    ("3000", "Owner's Capital", S.CAPITAL, "capital"),
    ("3100", "Retained Earnings", S.RETAINED_EARNINGS, "retained_earnings"),
    ("3200", "Opening Balance Equity", S.OPENING_BALANCE, "opening_balance"),
    ("3300", "Drawings", S.DRAWINGS, None),
    ("4000", "Sales", S.SALES, "sales"),
    ("4010", "Service Income", S.SALES, "service_income"),
    ("4100", "Sales Returns", S.SALES, "sales_returns"),
    ("4200", "Other Income", S.OTHER_INCOME, None),
    ("4210", "Round Off", S.OTHER_INCOME, "round_off"),
    ("4230", "Cash Excess", S.OTHER_INCOME, "cash_excess"),
    ("5000", "Cost of Goods Sold", S.COST_OF_GOODS, "cogs"),
    ("5010", "Purchases (non-stock)", S.COST_OF_GOODS, "purchases"),
    ("5020", "Stock Adjustments", S.COST_OF_GOODS, "stock_adjustment"),
    # Labour, fuel and packing added to the cost of manufactured goods (credited when absorbed).
    ("5030", "Production Overhead Absorbed", S.COST_OF_GOODS, "production_overhead"),
    ("6000", "Rent", S.OPERATING_EXPENSE, None),
    ("6010", "Salaries & Wages", S.OPERATING_EXPENSE, "salaries"),
    ("6015", "Employer PF & ESI", S.OPERATING_EXPENSE, "employer_contributions"),
    ("6020", "Electricity", S.OPERATING_EXPENSE, None),
    ("6030", "Telephone & Internet", S.OPERATING_EXPENSE, None),
    ("6040", "Office Expenses", S.OPERATING_EXPENSE, None),
    ("6050", "Travel & Conveyance", S.OPERATING_EXPENSE, None),
    ("6060", "Repairs & Maintenance", S.OPERATING_EXPENSE, None),
    ("6070", "Professional Fees", S.OPERATING_EXPENSE, None),
    ("6080", "Advertising & Marketing", S.OPERATING_EXPENSE, None),
    ("6090", "Miscellaneous Expenses", S.OPERATING_EXPENSE, None),
    ("6095", "Loyalty Rewards", S.OPERATING_EXPENSE, "loyalty_expense"),
    ("6100", "Depreciation", S.DEPRECIATION, "depreciation"),
    ("6200", "Bank Charges", S.FINANCE_COST, None),
    ("6210", "Interest Paid", S.FINANCE_COST, None),
    ("6900", "Loss on Sale of Assets", S.OTHER_EXPENSE, "asset_disposal"),
    ("6910", "Cash Shortage", S.OTHER_EXPENSE, "cash_shortage"),
]


def seed_chart(db: Session, business_id: uuid.UUID) -> None:
    for code, name, subtype, key in DEFAULT_CHART:
        db.add(Account(business_id=business_id, code=code, name=name, subtype=subtype,
                       type=SUBTYPE_TYPE[subtype], system_key=key))


def ensure_chart(db: Session, business_id: uuid.UUID) -> None:
    if db.scalar(select(Account.id).where(Account.business_id == business_id).limit(1)):
        return
    try:
        with db.begin_nested():
            seed_chart(db, business_id)
    except IntegrityError:
        pass  # seeded concurrently by another transaction


def system_account(db: Session, business_id: uuid.UUID, key: str) -> Account:
    ensure_chart(db, business_id)
    acc = db.scalar(select(Account).where(Account.business_id == business_id,
                                          Account.system_key == key))
    if acc is None:
        acc = _add_missing_system_account(db, business_id, key)
    return acc


def _add_missing_system_account(db: Session, business_id: uuid.UUID, key: str) -> Account:
    """System accounts added in later versions are created on first use in older businesses."""
    spec = next((c for c in DEFAULT_CHART if c[3] == key), None)
    if spec is None:
        raise RuntimeError(f"System account {key!r} is missing from the chart of accounts")
    code, name, subtype, _ = spec
    # Older charts already have this account (same code and name) without the key: adopt it.
    same = db.scalar(select(Account).where(Account.business_id == business_id, Account.code == code,
                                           Account.name == name, Account.system_key.is_(None)))
    if same is not None:
        same.system_key = key
        db.flush()
        return same
    taken = set(db.scalars(select(Account.code).where(Account.business_id == business_id)))
    while code in taken:  # the owner used that code for something else
        code = str(int(code) + 1)
    acc = Account(business_id=business_id, code=code, name=name, subtype=subtype, type=SUBTYPE_TYPE[subtype],
                  system_key=key)
    db.add(acc)
    db.flush()
    return acc


class Accounts:
    """Cached system-account lookup for one posting."""

    def __init__(self, db: Session, business_id: uuid.UUID) -> None:
        self.db, self.business_id = db, business_id
        self._cache: dict[str, Account] = {}

    def __getitem__(self, key: str) -> Account:
        if key not in self._cache:
            self._cache[key] = system_account(self.db, self.business_id, key)
        return self._cache[key]
