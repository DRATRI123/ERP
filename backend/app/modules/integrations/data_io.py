"""Spreadsheet import/export and Tally export.

Imports validate every row first and write nothing unless all rows are valid (or a dry run
was asked for), returning row-numbered errors. Exports produce CSV or Excel. The Tally export
writes journal vouchers and ledger masters in Tally's XML import format.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.sax.saxutils import escape

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.accounting.chart import ensure_chart
from app.modules.accounting.models import Account, AccountSubtype, JournalEntry
from app.modules.catalog.models import Unit
from app.modules.parties.models import Party, PartyRole

MAX_ROWS = 20_000


def read_rows(filename: str, data: bytes) -> list[dict[str, str]]:
    if filename.lower().endswith((".xlsx", ".xlsm")):
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = list(wb.active.iter_rows(values_only=True))
        if not rows:
            return []
        header = [str(h or "").strip().lower() for h in rows[0]]
        out = [{header[i]: ("" if v is None else str(v).strip()) for i, v in enumerate(r) if i < len(header)}
               for r in rows[1:] if any(v not in (None, "") for v in r)]
    else:
        text = data.decode("utf-8-sig")
        out = [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
               for row in csv.DictReader(io.StringIO(text))]
    if len(out) > MAX_ROWS:
        raise ValidationFailed(f"At most {MAX_ROWS} rows per import")
    return out


def _dec(v: str, places: int) -> Decimal | None:
    if v in ("", None):
        return None
    try:
        return Decimal(v.replace(",", "")).quantize(Decimal(1).scaleb(-places))
    except InvalidOperation as exc:
        raise ValidationFailed(f"{v!r} is not a number") from exc


def import_products(db: Session, access: BusinessAccess, rows: list[dict[str, str]], *, dry_run: bool) -> dict[str, Any]:
    """Columns: name, sku, unit, barcode, hsn, selling_price, purchase_price, mrp, gst_rate,
    prices_include_tax (yes/no), reorder_level."""
    from app.modules.catalog.service import create_product

    access.require("products.manage")
    units = {u.code: u for u in db.scalars(select(Unit).where(Unit.business_id == access.business.id))}
    errors, created = [], 0
    with db.begin_nested() as sp:
        for n, r in enumerate(rows, start=2):  # row 1 is the header
            try:
                unit = units.get((r.get("unit") or "NOS").upper())
                if unit is None:
                    raise ValidationFailed(f"unknown unit {r.get('unit')!r}")
                hsn = r.get("hsn") or None
                if hsn and (not hsn.isdigit() or len(hsn) not in (4, 6, 8)):
                    raise ValidationFailed("HSN must be 4, 6 or 8 digits")
                with db.begin_nested():
                    create_product(db, access, {
                        "name": r["name"][:200], "sku": r["sku"][:64], "unit_id": unit.id,
                        "barcode": r.get("barcode") or None, "hsn_sac": hsn,
                        "selling_price": _dec(r.get("selling_price", ""), 4),
                        "purchase_price": _dec(r.get("purchase_price", ""), 4), "mrp": _dec(r.get("mrp", ""), 4),
                        "gst_rate": _dec(r.get("gst_rate", ""), 4) or Decimal(0),
                        "cess_rate": Decimal(0), "reorder_level": _dec(r.get("reorder_level", ""), 3),
                        "prices_include_tax": (r.get("prices_include_tax") or "").lower() in ("yes", "y", "true", "1"),
                        "attributes": {}})
                created += 1
            except KeyError as exc:
                errors.append({"row": n, "error": f"missing column {exc.args[0]}"})
            except AppError as exc:
                errors.append({"row": n, "error": exc.message})
        if errors or dry_run:
            sp.rollback()
    return {"valid_rows": created, "errors": errors, "imported": 0 if (errors or dry_run) else created}


def import_parties(db: Session, access: BusinessAccess, role: PartyRole, rows: list[dict[str, str]],
                   *, dry_run: bool) -> dict[str, Any]:
    """Columns: name, gstin, phone, email, state_code, credit_limit, credit_days, address, city, pincode."""
    from app.modules.parties.service import create_party

    errors, created = [], 0
    with db.begin_nested() as sp:
        for n, r in enumerate(rows, start=2):
            try:
                from app.modules.gst.gstin import validate_gstin

                gstin = validate_gstin(r["gstin"]) if r.get("gstin") else None
                data = {"name": r["name"][:200], "gstin": gstin, "phone": r.get("phone") or None,
                        "email": (r.get("email") or "").lower() or None,
                        "state_code": (r.get("state_code") or "").zfill(2) if r.get("state_code") else None,
                        "credit_limit": _dec(r.get("credit_limit", ""), 4),
                        "credit_days": int(r["credit_days"]) if r.get("credit_days") else None,
                        "gst_type": "regular" if gstin else "unregistered", "attributes": {}}
                addr = [{"kind": "billing", "address_line1": r["address"][:200], "city": r.get("city") or None,
                         "pincode": r.get("pincode") or None, "state_code": data["state_code"], "country": "IN"}] \
                    if r.get("address") else []
                with db.begin_nested():
                    create_party(db, access, role, data, addr)
                created += 1
            except KeyError as exc:
                errors.append({"row": n, "error": f"missing column {exc.args[0]}"})
            except ValueError as exc:
                errors.append({"row": n, "error": str(exc)})
            except AppError as exc:
                errors.append({"row": n, "error": exc.message})
        if errors or dry_run:
            sp.rollback()
    return {"valid_rows": created, "errors": errors, "imported": 0 if (errors or dry_run) else created}


def to_file(rows: list[dict[str, Any]], fmt: str, sheet: str = "Export") -> tuple[bytes, str]:
    columns = list(rows[0].keys()) if rows else []
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=columns)
        w.writeheader()
        w.writerows({k: ("" if v is None else v) for k, v in r.items()} for r in rows)
        return buf.getvalue().encode("utf-8-sig"), "text/csv"
    wb = Workbook()
    ws = wb.active
    ws.title = sheet[:31]
    ws.append(columns)
    for r in rows:
        ws.append([float(v) if isinstance(v, Decimal) else (str(v) if v is not None and not isinstance(v, (int, float, date)) else v)
                   for v in r.values()])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------------------------------------------------------------------------- Tally

TALLY_GROUP = {
    AccountSubtype.CASH: "Cash-in-Hand", AccountSubtype.BANK: "Bank Accounts",
    AccountSubtype.RECEIVABLE: "Sundry Debtors", AccountSubtype.INVENTORY: "Stock-in-Hand",
    AccountSubtype.TAX_ASSET: "Duties & Taxes", AccountSubtype.CURRENT_ASSET: "Current Assets",
    AccountSubtype.FIXED_ASSET: "Fixed Assets", AccountSubtype.ACCUMULATED_DEPRECIATION: "Fixed Assets",
    AccountSubtype.OTHER_ASSET: "Current Assets", AccountSubtype.PAYABLE: "Sundry Creditors",
    AccountSubtype.TAX_LIABILITY: "Duties & Taxes", AccountSubtype.CURRENT_LIABILITY: "Current Liabilities",
    AccountSubtype.LOAN: "Loans (Liability)", AccountSubtype.OTHER_LIABILITY: "Current Liabilities",
    AccountSubtype.CAPITAL: "Capital Account", AccountSubtype.RETAINED_EARNINGS: "Reserves & Surplus",
    AccountSubtype.OPENING_BALANCE: "Capital Account", AccountSubtype.DRAWINGS: "Capital Account",
    AccountSubtype.SALES: "Sales Accounts", AccountSubtype.OTHER_INCOME: "Indirect Incomes",
    AccountSubtype.COST_OF_GOODS: "Direct Expenses", AccountSubtype.OPERATING_EXPENSE: "Indirect Expenses",
    AccountSubtype.DEPRECIATION: "Indirect Expenses", AccountSubtype.FINANCE_COST: "Indirect Expenses",
    AccountSubtype.OTHER_EXPENSE: "Indirect Expenses",
}


def tally_xml(db: Session, business_id, *, date_from: date, date_to: date, company: str) -> str:  # type: ignore[no-untyped-def]
    """Ledger masters + journal vouchers for Tally Prime/ERP 9 (Gateway → Import → Data).
    Customer and supplier lines use the party's own ledger under Sundry Debtors/Creditors."""
    ensure_chart(db, business_id)
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.business_id == business_id))}
    parties = {p.id: p for p in db.scalars(select(Party).where(Party.business_id == business_id))}
    entries = list(db.scalars(select(JournalEntry).where(
        JournalEntry.business_id == business_id, JournalEntry.entry_date >= date_from,
        JournalEntry.entry_date <= date_to).order_by(JournalEntry.entry_date, JournalEntry.posted_at)))

    def ledger_name(line) -> str:  # type: ignore[no-untyped-def]
        a = accounts[line.account_id]
        if line.party_id and a.subtype in (AccountSubtype.RECEIVABLE, AccountSubtype.PAYABLE):
            return parties[line.party_id].name
        if a.subtype in (AccountSubtype.RECEIVABLE, AccountSubtype.PAYABLE):
            return "Cash Customers" if a.subtype == AccountSubtype.RECEIVABLE else "Sundry Creditors (unspecified)"
        return a.name

    masters: dict[str, str] = {}
    for e in entries:
        for ln in e.lines:
            a = accounts[ln.account_id]
            masters.setdefault(ledger_name(ln), TALLY_GROUP[a.subtype])
    msgs = [f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><LEDGER NAME="{escape(n, {chr(34): "&quot;"})}" ACTION="Create">'
            f"<NAME.LIST><NAME>{escape(n)}</NAME></NAME.LIST><PARENT>{escape(g)}</PARENT></LEDGER></TALLYMESSAGE>"
            for n, g in sorted(masters.items())]
    for e in entries:
        parts = ['<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="Journal" ACTION="Create">',
                 f"<DATE>{e.entry_date:%Y%m%d}</DATE><VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>",
                 f"<VOUCHERNUMBER>{escape(e.number)}</VOUCHERNUMBER>",
                 f"<NARRATION>{escape((e.narration or '') + (f' [{e.source_ref}]' if e.source_ref else ''))}</NARRATION>"]
        for ln in e.lines:
            # Tally convention: debits are negative amounts with ISDEEMEDPOSITIVE=Yes.
            debit = ln.debit > 0
            amount = -ln.debit if debit else ln.credit
            parts.append(f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>{escape(ledger_name(ln))}</LEDGERNAME>"
                         f"<ISDEEMEDPOSITIVE>{'Yes' if debit else 'No'}</ISDEEMEDPOSITIVE>"
                         f"<AMOUNT>{amount.quantize(Decimal('0.01'))}</AMOUNT></ALLLEDGERENTRIES.LIST>")
        parts.append("</VOUCHER></TALLYMESSAGE>")
        msgs.append("".join(parts))
    return ("<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA>"
            "<REQUESTDESC><REPORTNAME>All Masters</REPORTNAME><STATICVARIABLES>"
            f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY></STATICVARIABLES></REQUESTDESC>"
            "<REQUESTDATA>" + "".join(msgs) + "</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>")
