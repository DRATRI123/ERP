"""Export the books to Tally (TallyPrime / Tally.ERP 9) as an XML "Import Data" file.

Two files, both importable in Tally via *Import → Masters / Transactions*:

* **masters**: one LEDGER per account (under the matching Tally group) and one per customer /
  supplier (under Sundry Debtors / Creditors, with GSTIN and state);
* **vouchers**: one VOUCHER per journal entry in the period. Receivable/payable lines go to the
  party's own ledger; Tally's sign convention is used (debits negative, ISDEEMEDPOSITIVE Yes).
  Sales, purchases, receipts and payments get their Tally voucher types; everything else is a
  Journal. The ERP's entry number is the voucher number, so re-importing updates, not duplicates.

Every value is XML-escaped. Only accounts and entries of the one business are exported.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.access.service import BusinessAccess
from app.modules.accounting.models import Account, JournalEntry, JournalLine, JournalSource
from app.modules.accounting.models import AccountSubtype as S
from app.modules.gst.gstin import STATE_CODES
from app.modules.parties.models import Party

GROUP = {
    S.CASH: "Cash-in-Hand", S.BANK: "Bank Accounts", S.RECEIVABLE: "Sundry Debtors", S.INVENTORY: "Stock-in-Hand",
    S.TAX_ASSET: "Duties & Taxes", S.CURRENT_ASSET: "Current Assets", S.FIXED_ASSET: "Fixed Assets",
    S.ACCUMULATED_DEPRECIATION: "Fixed Assets", S.OTHER_ASSET: "Current Assets", S.PAYABLE: "Sundry Creditors",
    S.TAX_LIABILITY: "Duties & Taxes", S.CURRENT_LIABILITY: "Current Liabilities", S.LOAN: "Loans (Liability)",
    S.OTHER_LIABILITY: "Current Liabilities", S.CAPITAL: "Capital Account", S.RETAINED_EARNINGS: "Reserves & Surplus",
    S.OPENING_BALANCE: "Capital Account", S.DRAWINGS: "Capital Account", S.SALES: "Sales Accounts",
    S.OTHER_INCOME: "Indirect Incomes", S.COST_OF_GOODS: "Purchase Accounts", S.OPERATING_EXPENSE: "Indirect Expenses",
    S.DEPRECIATION: "Indirect Expenses", S.FINANCE_COST: "Indirect Expenses", S.OTHER_EXPENSE: "Indirect Expenses",
}
CONTROL = {S.RECEIVABLE, S.PAYABLE}


def _x(v: object) -> str:
    return escape(str(v), {'"': "&quot;"})


def _envelope(report: str, body: str, company: str) -> str:
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>"
            f"<BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>{report}</REPORTNAME><STATICVARIABLES>"
            f"<SVCURRENTCOMPANY>{_x(company)}</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC>"
            f"<REQUESTDATA>{body}</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>\n")


def _party_name(p: Party) -> str:
    return f"{p.name} ({p.code})" if p.code else p.name


def masters(db: Session, access: BusinessAccess) -> str:
    access.require("accounting.view")
    bid = access.business.id
    out = []
    for a in db.scalars(select(Account).where(Account.business_id == bid).order_by(Account.code)):
        if a.subtype in CONTROL:
            continue  # parties carry the balances in Tally
        out.append(f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><LEDGER NAME="{_x(a.name)}" ACTION="Create">'
                   f"<NAME>{_x(a.name)}</NAME><PARENT>{_x(GROUP.get(a.subtype, 'Suspense A/c'))}</PARENT>"
                   f"<ALIAS>{_x(a.code)}</ALIAS></LEDGER></TALLYMESSAGE>")
    for p in db.scalars(select(Party).where(Party.business_id == bid).order_by(Party.name)):
        group = "Sundry Debtors" if p.is_customer else "Sundry Creditors"
        state = STATE_CODES.get(p.state_code or "", "")
        gst = (f"<GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE><PARTYGSTIN>{_x(p.gstin)}</PARTYGSTIN>" if p.gstin
               else "<GSTREGISTRATIONTYPE>Unregistered/Consumer</GSTREGISTRATIONTYPE>")
        out.append(f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><LEDGER NAME="{_x(_party_name(p))}" ACTION="Create">'
                   f"<NAME>{_x(_party_name(p))}</NAME><PARENT>{group}</PARENT><ISBILLWISEON>Yes</ISBILLWISEON>"
                   f"{gst}<LEDSTATENAME>{_x(state)}</LEDSTATENAME>"
                   + (f"<LEDGERPHONE>{_x(p.phone)}</LEDGERPHONE>" if p.phone else "")
                   + (f"<EMAIL>{_x(p.email)}</EMAIL>" if p.email else "") + "</LEDGER></TALLYMESSAGE>")
    return _envelope("All Masters", "".join(out), access.business.name)


def _voucher_type(entry: JournalEntry, lines: list[JournalLine], accounts: dict[uuid.UUID, Account]) -> str:
    subtypes = {accounts[ln.account_id].subtype for ln in lines}
    if entry.source_type == JournalSource.DOCUMENT:
        if S.SALES in subtypes:
            return "Credit Note" if any(ln.debit and accounts[ln.account_id].subtype == S.SALES for ln in lines) else "Sales"
        if S.PAYABLE in subtypes:
            return "Debit Note" if any(ln.debit and accounts[ln.account_id].subtype == S.PAYABLE for ln in lines) else "Purchase"
    if entry.source_type in (JournalSource.PAYMENT, JournalSource.EXPENSE, JournalSource.PAYROLL) and subtypes & {S.CASH, S.BANK}:
        cash_in = any(ln.debit and accounts[ln.account_id].subtype in (S.CASH, S.BANK) for ln in lines)
        return "Receipt" if cash_in else "Payment"
    if subtypes <= {S.CASH, S.BANK}:
        return "Contra"
    return "Journal"


def vouchers(db: Session, access: BusinessAccess, *, date_from: date, date_to: date) -> tuple[str, int]:
    access.require("accounting.view")
    bid = access.business.id
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.business_id == bid))}
    parties = {p.id: p for p in db.scalars(select(Party).where(Party.business_id == bid))}
    entries = list(db.scalars(select(JournalEntry).where(
        JournalEntry.business_id == bid, JournalEntry.entry_date >= date_from, JournalEntry.entry_date <= date_to)
        .order_by(JournalEntry.entry_date, JournalEntry.number)))
    out = []
    for e in entries:
        merged: dict[str, Decimal] = defaultdict(Decimal)  # ledger name → net debit
        for ln in e.lines:
            acc = accounts[ln.account_id]
            name = _party_name(parties[ln.party_id]) if acc.subtype in CONTROL and ln.party_id in parties else acc.name
            merged[name] += ln.debit - ln.credit
        if not merged:
            continue
        vtype = _voucher_type(e, list(e.lines), accounts)
        rows = "".join(
            f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>{_x(name)}</LEDGERNAME>"
            f"<ISDEEMEDPOSITIVE>{'Yes' if amt > 0 else 'No'}</ISDEEMEDPOSITIVE><AMOUNT>{-amt:.2f}</AMOUNT>"
            "</ALLLEDGERENTRIES.LIST>"
            for name, amt in merged.items() if amt != 0)
        narration = e.narration or e.source_ref or ""
        out.append(f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="{vtype}" ACTION="Create" OBJVIEW="Accounting Voucher View">'
                   f"<DATE>{e.entry_date:%Y%m%d}</DATE><VOUCHERTYPENAME>{vtype}</VOUCHERTYPENAME>"
                   f"<VOUCHERNUMBER>{_x(e.number)}</VOUCHERNUMBER>"
                   + (f"<REFERENCE>{_x(e.source_ref)}</REFERENCE>" if e.source_ref else "")
                   + f"<NARRATION>{_x(narration)}</NARRATION><PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>"
                   f"{rows}</VOUCHER></TALLYMESSAGE>")
    return _envelope("Vouchers", "".join(out), access.business.name), len(out)
