"""Automatic journal postings for business events, made in the same transaction as the event.

| Event                     | Debit                                   | Credit                                   |
|---------------------------|-----------------------------------------|------------------------------------------|
| Sales invoice / debit note| Receivable (party)                      | Sales / Service income, Output GST       |
|   + goods issued          | Cost of goods sold                      | Inventory                                |
| Credit note (return)      | Sales returns, Output GST               | Receivable (party)                       |
|   + goods restocked       | Inventory                               | Cost of goods sold                       |
| Delivery challan          | Cost of goods sold                      | Inventory                                |
| Purchase invoice          | Inventory / Purchases, Input GST (ITC)  | Payable (party), Landed-cost clearing    |
|   reverse charge          | Input GST (if eligible)                 | GST payable under RCM                    |
| Purchase return           | Payable (party), stock-cost difference  | Inventory / Purchases, Input GST         |
| Receipt                   | Cash / Bank, TDS receivable (if the     | Receivable (or Payable for refunds)      |
|                           | customer deducted TDS)                  |                                          |
| Payment                   | Payable (or Receivable for refunds)     | Cash / Bank, TDS payable (TDS deducted)  |
| Opening stock             | Inventory                               | Opening balance equity                   |
| Stock adjustment / count  | Inventory or Stock adjustments          | Stock adjustments or Inventory           |

Stock values come from the stock ledger itself, so the Inventory account always equals the
stock valuation. Paisa differences from per-line rounding go to Round Off; anything larger is a
bug and raises instead of being hidden.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounting.chart import Accounts
from app.modules.accounting.ledger import Line, post, reverse_source
from app.modules.accounting.models import (
    Account,
    AccountSubtype,
    JournalEntry,
    JournalSource,
)
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import Document, Payment, PaymentDirection, PaymentMode
from app.modules.businesses.models import Business
from app.modules.gst.tax import ZERO
from app.modules.inventory.models import (
    StockDocument,
    StockDocumentKind,
    StockLedgerEntry,
)

ROUNDING_TOLERANCE = Decimal("0.01")
BILLING_SOURCE = "billing_document"
STOCK_SOURCE = "stock_document"


def _balance(lines: list[Line], acc: Accounts, what: str) -> list[Line]:
    diff = sum((ln.debit - ln.credit for ln in lines), ZERO)
    if diff == 0:
        return lines
    if abs(diff) > ROUNDING_TOLERANCE:
        raise RuntimeError(f"Posting for {what} is out of balance by {diff}")
    ro = acc["round_off"]
    lines.append(Line(ro, credit=diff) if diff > 0 else Line(ro, debit=-diff))
    return lines


def _stock_entries(db: Session, source: str, source_id: uuid.UUID) -> list[StockLedgerEntry]:
    return list(db.scalars(select(StockLedgerEntry).where(
        StockLedgerEntry.source_type == source, StockLedgerEntry.source_id == source_id,
        StockLedgerEntry.reverses_entry_id.is_(None))))


def _tax_lines(acc: Accounts, prefix: str, doc: Document, *, debit: bool) -> list[Line]:
    """One line per GST head (CGST, SGST/UTGST, IGST, cess) from the document totals."""
    out = []
    for head in ("cgst", "sgst", "igst", "cess"):
        amount = getattr(doc, f"{head}_total")
        if amount:
            a = acc[f"{prefix}_{head}"]
            out.append(Line(a, debit=amount) if debit else Line(a, credit=amount))
    return out


# ---------------------------------------------------------------------------- documents


def post_document(db: Session, business: Business, doc: Document, *, user_id: uuid.UUID | None) -> JournalEntry | None:
    acc = Accounts(db, business.id)
    party = doc.party_id
    stock = _stock_entries(db, BILLING_SOURCE, doc.id)
    stock_value = sum((e.value for e in stock), ZERO)  # negative when goods left
    lines: list[Line] = []
    t = doc.doc_type

    def income_lines(credit: bool) -> list[Line]:
        out = []
        for ln in doc.lines:
            a = acc["service_income"] if ln.is_service else acc["sales"]
            if t == DocType.CREDIT_NOTE:
                a = acc["sales_returns"]
            out.append(Line(a, credit=ln.taxable_value) if credit else Line(a, debit=ln.taxable_value))
        return out

    if t in (DocType.SALES_INVOICE, DocType.DEBIT_NOTE):
        lines.append(Line(acc["ar"], debit=doc.amount_payable, party_id=party))
        lines += income_lines(credit=True)
        if not doc.reverse_charge:  # under RCM the buyer pays the tax to the government
            lines += _tax_lines(acc, "output", doc, debit=False)
        lines += _round_off(acc, doc.round_off, increases_party_balance_on_debit=True)
        if stock_value:
            lines += [Line(acc["cogs"], debit=-stock_value), Line(acc["inventory"], credit=-stock_value)]
    elif t == DocType.CREDIT_NOTE:
        lines.append(Line(acc["ar"], credit=doc.amount_payable, party_id=party))
        lines += income_lines(credit=False)
        if not doc.reverse_charge:
            lines += _tax_lines(acc, "output", doc, debit=True)
        lines += _round_off(acc, doc.round_off, increases_party_balance_on_debit=False)
        if stock_value:
            lines += [Line(acc["inventory"], debit=stock_value), Line(acc["cogs"], credit=stock_value)]
    elif t == DocType.DELIVERY_CHALLAN:
        if stock_value:
            lines += [Line(acc["cogs"], debit=-stock_value), Line(acc["inventory"], credit=-stock_value)]
    elif t in (DocType.PURCHASE_INVOICE, DocType.PURCHASE_RETURN):
        lines = _purchase_lines(db, acc, doc, stock)
    else:
        return None  # quotations, orders, proformas: no accounting effect
    _balance(lines, acc, doc.number or str(doc.id))
    return post(db, business.id, entry_date=doc.doc_date, source_type=JournalSource.DOCUMENT,
                source_id=doc.id, source_ref=doc.number, lines=lines, branch_id=doc.branch_id,
                created_by=user_id,
                narration=f"{doc.doc_type.value.replace('_', ' ').title()} {doc.number}"
                          + (f" — {doc.party_name}" if doc.party_name else ""))


def _round_off(acc: Accounts, amount: Decimal, *, increases_party_balance_on_debit: bool) -> list[Line]:
    """The document's rupee round-off. For sales it is income (credit) when the customer pays
    more; for purchases it is a cost (debit) when we pay more."""
    if not amount:
        return []
    ro = acc["round_off"]
    if increases_party_balance_on_debit:  # party is debited (sales invoice / purchase return)
        return [Line(ro, credit=amount)] if amount > 0 else [Line(ro, debit=-amount)]
    return [Line(ro, debit=amount)] if amount > 0 else [Line(ro, credit=-amount)]


def _purchase_lines(db: Session, acc: Accounts, doc: Document,
                    stock: list[StockLedgerEntry]) -> list[Line]:
    """Purchase invoice or purchase return.

    Built as signed amounts (positive = debit) for an invoice; a return flips the sign. Stock
    lines move Inventory by exactly the stock-ledger value; on a return, the gap between what
    the supplier credits us and the stock's carrying cost is a stock gain/loss.
    """
    sign = Decimal(1) if doc.doc_type == DocType.PURCHASE_INVOICE else Decimal(-1)
    by_line = {e.source_line_id: e for e in stock}
    landed_base = sum((ln.taxable_value for ln in doc.lines if ln.product_id), ZERO)
    signed: list[tuple[Account, Decimal]] = []
    for ln in doc.lines:
        tax = ln.cgst_amount + ln.sgst_amount + ln.igst_amount + ln.cess_amount
        claimable = ln.itc_eligible
        entry = by_line.get(ln.id)
        if entry is not None:
            signed.append((acc["inventory"], entry.value))  # already signed by the stock ledger
            if sign < 0:
                credited = ln.taxable_value + (ZERO if claimable else tax)
                signed.append((acc["stock_adjustment"], -(credited + entry.value)))
        else:
            cost = ln.taxable_value + (ZERO if claimable else tax)
            if ln.product_id and doc.landed_cost and landed_base and sign > 0:
                cost += (doc.landed_cost * ln.taxable_value / landed_base).quantize(Decimal("0.0001"))
            signed.append((acc["purchases"], sign * cost))
        if claimable:
            for head in ("cgst", "sgst", "igst", "cess"):
                amt = getattr(ln, f"{head}_amount")
                if amt:
                    signed.append((acc[f"input_{head}"], sign * amt))
    lines = [Line(a, debit=v) if v > 0 else Line(a, credit=-v) for a, v in signed if v]
    if doc.reverse_charge:  # we owe this tax to the government, not to the supplier
        lines += _tax_lines(acc, "rcm", doc, debit=sign < 0)
    lines.append(Line(acc["ap"], credit=doc.amount_payable, party_id=doc.party_id) if sign > 0
                 else Line(acc["ap"], debit=doc.amount_payable, party_id=doc.party_id))
    lines += _round_off(acc, doc.round_off, increases_party_balance_on_debit=sign < 0)
    if doc.landed_cost and sign > 0:
        lines.append(Line(acc["landed_cost_clearing"], credit=doc.landed_cost))
    return lines


# ---------------------------------------------------------------------------- payments


def cash_or_bank(db: Session, business_id: uuid.UUID, mode: PaymentMode,
                 account_id: uuid.UUID | None) -> Account:
    if account_id is not None:
        a = db.get(Account, account_id)
        if a is None or a.business_id != business_id or a.subtype not in (
                AccountSubtype.CASH, AccountSubtype.BANK):
            from app.core.errors import ValidationFailed

            raise ValidationFailed("Choose a cash or bank account for this payment")
        return a
    return Accounts(db, business_id)["cash" if mode == PaymentMode.CASH else "bank"]


def post_payment(db: Session, business: Business, pay: Payment, *, user_id: uuid.UUID | None) -> JournalEntry | None:
    acc = Accounts(db, business.id)
    money = cash_or_bank(db, business.id, pay.mode, pay.account_id)
    docs = {d.id: d for d in db.scalars(select(Document).where(
        Document.id.in_([a.document_id for a in pay.allocations])))} if pay.allocations else {}
    lines: list[Line] = []
    allocated = ZERO
    for a in pay.allocations:
        d = docs[a.document_id]
        # Receipts reduce what customers owe (or what a supplier owes us back on a return);
        # payments reduce what we owe suppliers (or refund a customer's credit note).
        control = acc["ap"] if d.doc_type in (DocType.PURCHASE_INVOICE, DocType.PURCHASE_RETURN) else acc["ar"]
        lines.append(Line(control, credit=a.amount, party_id=pay.party_id)
                     if pay.direction == PaymentDirection.IN else
                     Line(control, debit=a.amount, party_id=pay.party_id))
        allocated += a.amount
    advance = pay.amount + pay.tds_amount - allocated
    if advance:  # unallocated: an advance from a customer / to a supplier
        lines.append(Line(acc["ar"], credit=advance, party_id=pay.party_id)
                     if pay.direction == PaymentDirection.IN else
                     Line(acc["ap"], debit=advance, party_id=pay.party_id))
    lines.append(Line(money, debit=pay.amount) if pay.direction == PaymentDirection.IN
                 else Line(money, credit=pay.amount))
    if pay.tds_amount:  # the part of the bill settled by TDS instead of money
        lines.append(Line(acc["tds_receivable"], debit=pay.tds_amount, party_id=pay.party_id)
                     if pay.direction == PaymentDirection.IN else
                     Line(acc["tds_payable"], credit=pay.tds_amount, party_id=pay.party_id))
    return post(db, business.id, entry_date=pay.payment_date, source_type=JournalSource.PAYMENT,
                source_id=pay.id, source_ref=pay.number, lines=lines, branch_id=pay.branch_id,
                created_by=user_id,
                narration=f"{'Receipt' if pay.direction == PaymentDirection.IN else 'Payment'} "
                          f"{pay.number} ({pay.mode.value})")


# ---------------------------------------------------------------------------- inventory documents


def post_stock_document(db: Session, business: Business, doc: StockDocument, *,
                        user_id: uuid.UUID | None) -> JournalEntry | None:
    if doc.kind == StockDocumentKind.TRANSFER:
        return None  # stock changes place, not value: one Inventory account
    acc = Accounts(db, business.id)
    value = sum((e.value for e in _stock_entries(db, STOCK_SOURCE, doc.id)), ZERO)
    if value == 0:
        return None
    counter = (acc["opening_balance"] if doc.kind == StockDocumentKind.OPENING else
               acc["production_overhead"] if doc.kind == StockDocumentKind.PRODUCTION else acc["stock_adjustment"])
    lines = ([Line(acc["inventory"], debit=value), Line(counter, credit=value)] if value > 0 else
             [Line(counter, debit=-value), Line(acc["inventory"], credit=-value)])
    return post(db, business.id, entry_date=doc.doc_date, source_type=JournalSource.STOCK,
                source_id=doc.id, source_ref=doc.number, lines=lines,
                created_by=user_id, narration=f"Stock {doc.kind.value} {doc.number}")


def reverse(db: Session, business: Business, source_type: JournalSource, source_id: uuid.UUID,
            *, on: date, user_id: uuid.UUID | None) -> JournalEntry | None:
    return reverse_source(db, business.id, source_type, source_id, on=on, created_by=user_id)

