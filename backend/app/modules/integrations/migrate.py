"""Moving from Tally, Busy or Marg: Tally XML masters (ledgers, stock items, opening balances)
and the column names of Busy / Marg Excel exports."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from defusedxml import ElementTree  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.catalog.models import Product, Unit
from app.modules.parties.models import Party, PartyRole

# Canonical column -> names used by Busy, Marg, Vyapar, Tally and plain spreadsheets.
PRODUCT_COLUMNS = {
    "name": ("name", "item name", "item", "product name", "product", "stock item", "description", "item description"),
    "sku": ("sku", "item code", "code", "alias", "product code", "item alias"),
    "unit": ("unit", "units", "uom", "main unit", "base unit", "unit name"),
    "barcode": ("barcode", "bar code", "ean"),
    "hsn": ("hsn", "hsn code", "hsn/sac", "hsn sac", "hsn/sac code"),
    "selling_price": ("selling_price", "sale price", "sales price", "selling price", "rate", "sale rate", "retail price"),
    "purchase_price": ("purchase_price", "purchase price", "purc price", "purchase rate", "cost price", "cost"),
    "mrp": ("mrp", "m.r.p.", "m r p"),
    "gst_rate": ("gst_rate", "gst %", "gst", "tax rate", "gst rate", "igst %", "tax %", "tax category"),
    "reorder_level": ("reorder_level", "reorder level", "min stock", "minimum stock", "re-order level"),
    "prices_include_tax": ("prices_include_tax", "inclusive of tax", "tax inclusive", "price incl tax"),
}
PARTY_COLUMNS = {
    "name": ("name", "account name", "party name", "ledger name", "customer name", "supplier name", "party"),
    "gstin": ("gstin", "gst no", "gst number", "gstin/uin", "gst in", "gstno"),
    "phone": ("phone", "mobile", "mobile no", "phone no", "contact no", "mobile number"),
    "email": ("email", "e-mail", "email id"),
    "state_code": ("state_code", "state code", "state"),
    "address": ("address", "address 1", "address line 1", "addr"),
    "city": ("city", "town", "station"),
    "pincode": ("pincode", "pin code", "pin"),
    "credit_limit": ("credit_limit", "credit limit"),
    "credit_days": ("credit_days", "credit days", "due days"),
    "opening_balance": ("opening balance", "op. bal", "opening bal", "op bal", "opening"),
}
UNIT_ALIASES = {"PC": "PCS", "PIECE": "PCS", "PIECES": "PCS", "NO": "NOS", "NUMBER": "NOS", "NUMBERS": "NOS",
                "KGS": "KG", "KILOGRAM": "KG", "GMS": "GM", "GRAM": "GM", "GRAMS": "GM", "LITRE": "LTR", "LITRES": "LTR",
                "LTRS": "LTR", "L": "LTR", "BOTTLE": "BTL", "BOTTLES": "BTL", "BOXES": "BOX", "STRIPS": "STRIP",
                "PACKET": "PKT", "PACKETS": "PKT", "PACK": "PKT", "METER": "MTR", "METRE": "MTR", "MTRS": "MTR"}

STATES = {"jammu and kashmir": "01", "himachal pradesh": "02", "punjab": "03", "chandigarh": "04", "uttarakhand": "05",
          "haryana": "06", "delhi": "07", "rajasthan": "08", "uttar pradesh": "09", "bihar": "10", "sikkim": "11",
          "arunachal pradesh": "12", "nagaland": "13", "manipur": "14", "mizoram": "15", "tripura": "16", "meghalaya": "17",
          "assam": "18", "west bengal": "19", "jharkhand": "20", "odisha": "21", "chhattisgarh": "22", "madhya pradesh": "23",
          "gujarat": "24", "dadra and nagar haveli and daman and diu": "26", "maharashtra": "27", "karnataka": "29", "goa": "30",
          "lakshadweep": "31", "kerala": "32", "tamil nadu": "33", "puducherry": "34", "andaman and nicobar islands": "35",
          "telangana": "36", "andhra pradesh": "37", "ladakh": "38"}


def _key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def state_code(value: str | None) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    if v.isdigit():
        return v.zfill(2)
    return STATES.get(_key(v))


def normalise_rows(rows: list[dict[str, str]], kind: str) -> list[dict[str, str]]:
    """Rename columns from other software to ours; fill a code for items without one; GST "18%" -> 18."""
    table = PRODUCT_COLUMNS if kind == "products" else PARTY_COLUMNS
    lookup = {_key(alias): canon for canon, aliases in table.items() for alias in aliases}
    out, used = [], set()
    for r in rows:
        n: dict[str, str] = {}
        for k, v in r.items():
            canon = lookup.get(_key(k))
            if canon and not n.get(canon):
                n[canon] = (v or "").strip()
        if kind == "products":
            if n.get("unit"):
                u = n["unit"].upper().rstrip(".")
                n["unit"] = UNIT_ALIASES.get(u, u)
            if n.get("gst_rate"):
                m = re.search(r"\d+(\.\d+)?", n["gst_rate"])
                n["gst_rate"] = m.group(0) if m else ""
            if not n.get("sku") and n.get("name"):
                base = re.sub(r"[^A-Z0-9]+", "-", n["name"].upper()).strip("-")[:20] or "ITEM"
                sku, i = base, 1
                while sku in used:
                    i += 1
                    sku = f"{base}-{i}"
                n["sku"] = sku
            if n.get("sku"):
                used.add(n["sku"])
        else:
            if n.get("state_code"):
                n["state_code"] = state_code(n["state_code"]) or ""
        out.append(n)
    return [r for r in out if r.get("name")]


# ---------------------------------------------------------------------------- Tally XML


def _num(text: str | None) -> Decimal:
    m = re.search(r"-?\d[\d,]*(\.\d+)?", text or "")
    if not m:
        return Decimal(0)
    try:
        return Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return Decimal(0)


def _text(el: Any, *tags: str) -> str | None:
    for t in tags:
        x = el.find(f".//{t}")
        if x is not None and (x.text or "").strip():
            return x.text.strip()
    return None


def parse_tally(data: bytes) -> dict[str, list[dict[str, Any]]]:
    """Ledgers under Sundry Debtors / Creditors and stock items from a Tally masters export
    (Gateway of Tally → Export → Masters, XML). Tally writes debit openings as negative numbers."""
    try:
        root = ElementTree.fromstring(data)
    except Exception as exc:
        raise ValidationFailed("This is not a Tally XML export") from exc
    parties, items = [], []
    for led in root.iter("LEDGER"):
        parent = (_text(led, "PARENT") or "").lower()
        if "debtor" not in parent and "creditor" not in parent:
            continue
        name = led.get("NAME") or _text(led, "NAME")
        if not name:
            continue
        addr = [a.text.strip() for a in led.iter("ADDRESS") if a.text and a.text.strip()]
        opening = _num(_text(led, "OPENINGBALANCE"))
        parties.append({"name": name.strip(), "role": "customer" if "debtor" in parent else "supplier",
                        "gstin": _text(led, "PARTYGSTIN", "GSTIN"), "state_code": state_code(_text(led, "LEDSTATENAME", "STATENAME")),
                        "phone": _text(led, "LEDGERMOBILE", "LEDGERPHONE"), "email": _text(led, "EMAIL"),
                        "address": ", ".join(addr) or None, "pincode": _text(led, "PINCODE"),
                        "credit_days": int(_num(_text(led, "BILLCREDITPERIOD"))) or None,
                        "opening": -opening})  # Tally: debit is negative; ours: + they owe us
    for it in root.iter("STOCKITEM"):
        name = it.get("NAME") or _text(it, "NAME")
        if not name:
            continue
        unit = (_text(it, "BASEUNITS") or "NOS").upper().rstrip(".")
        rate_el = None
        for d in it.iter("RATEDETAILS.LIST"):
            if (_text(d, "GSTRATEDUTYHEAD") or "").lower() in ("igst", "integrated tax"):
                rate_el = d
        gst = _num(_text(rate_el, "GSTRATE")) if rate_el is not None else _num(_text(it, "GSTRATE"))
        items.append({"name": name.strip(), "unit": UNIT_ALIASES.get(unit, unit), "hsn": _text(it, "HSNCODE"),
                      "gst_rate": gst, "opening_qty": abs(_num(_text(it, "OPENINGBALANCE"))),
                      "opening_rate": abs(_num(_text(it, "OPENINGRATE"))), "mrp": _num(_text(it, "MRPRATE")) or None})
    if not parties and not items:
        raise ValidationFailed("No customers, suppliers or stock items found in this Tally file")
    return {"parties": parties, "items": items}


def import_tally(db: Session, access: BusinessAccess, data: bytes, *, opening_date: date | None,
                 warehouse_id: Any, dry_run: bool) -> dict[str, Any]:
    """Create the parties and products that do not exist yet (matched by name); optionally post
    their opening balances (on the first day of a financial year) and opening stock."""
    from app.modules.accounting.accounts import post_opening_balances
    from app.modules.accounting.chart import Accounts
    from app.modules.catalog.service import create_product
    from app.modules.inventory import service as inventory
    from app.modules.parties.service import create_party

    for perm in ("products.manage", "customers.manage", "suppliers.manage"):
        access.require(perm)
    parsed = parse_tally(data)
    bid = access.business.id
    units = {u.code: u for u in db.scalars(select(Unit).where(Unit.business_id == bid))}
    have_parties = {p.name.lower(): p for p in db.scalars(select(Party).where(Party.business_id == bid))}
    have_products = {p.name.lower(): p for p in db.scalars(select(Product).where(Product.business_id == bid))}
    skus = {p.sku for p in have_products.values()}
    out: dict[str, Any] = {"customers": 0, "suppliers": 0, "products": 0, "skipped": 0, "opening_balances": 0,
                           "opening_stock_lines": 0, "errors": []}
    opening_lines: list[dict[str, Any]] = []
    stock_lines: list[Any] = []
    with db.begin_nested() as sp:
        for p in parsed["parties"]:
            try:
                party = have_parties.get(p["name"].lower())
                if party is None:
                    from app.modules.gst.gstin import validate_gstin

                    gstin = validate_gstin(p["gstin"]) if p["gstin"] else None
                    role = PartyRole.CUSTOMER if p["role"] == "customer" else PartyRole.SUPPLIER
                    sc = p["state_code"] or (gstin[:2] if gstin else None)
                    pdata: dict[str, Any] = {"name": p["name"][:200], "gst_type": "regular" if gstin else "unregistered", "gstin": gstin,
                            "state_code": sc, "phone": (p["phone"] or None), "email": (p["email"] or "").lower() or None,
                            "credit_days": p["credit_days"], "attributes": {}}
                    addr = [{"kind": "billing", "address_line1": p["address"][:200], "pincode": p["pincode"],
                             "state_code": sc, "country": "IN"}] if p["address"] else []
                    with db.begin_nested():
                        party = create_party(db, access, role, pdata, addr)
                    have_parties[p["name"].lower()] = party
                    out["customers" if p["role"] == "customer" else "suppliers"] += 1
                else:
                    out["skipped"] += 1
                if opening_date and p["opening"]:
                    acc = Accounts(db, bid)["ar" if p["role"] == "customer" else "ap"]
                    amt = Decimal(p["opening"])
                    opening_lines.append({"account_id": acc.id, "party_id": party.id,
                                          "debit": amt if amt > 0 else Decimal(0), "credit": -amt if amt < 0 else Decimal(0)})
            except Exception as exc:  # noqa: BLE001 - report every bad row, then roll back
                out["errors"].append(f"{p['name']}: {getattr(exc, 'message', exc)}")
        for it in parsed["items"]:
            try:
                prod = have_products.get(it["name"].lower())
                if prod is None:
                    unit = units.get(it["unit"]) or units.get("NOS")
                    if unit is None:
                        raise ValidationFailed(f"Unit {it['unit']} not found — add it in Settings → Units")
                    sku = re.sub(r"[^A-Z0-9]+", "-", it["name"].upper()).strip("-")[:20] or "ITEM"
                    base, i = sku, 1
                    while sku in skus:
                        i += 1
                        sku = f"{base}-{i}"
                    skus.add(sku)
                    with db.begin_nested():
                        prod = create_product(db, access, {"name": it["name"][:200], "sku": sku, "unit_id": unit.id,
                                                           "hsn_sac": it["hsn"] if it["hsn"] and re.fullmatch(r"\d{4}|\d{6}|\d{8}", it["hsn"]) else None,
                                                           "gst_rate": it["gst_rate"], "mrp": it["mrp"], "purchase_price": it["opening_rate"] or None})
                    have_products[it["name"].lower()] = prod
                    out["products"] += 1
                else:
                    out["skipped"] += 1
                if opening_date and it["opening_qty"] > 0 and warehouse_id:
                    stock_lines.append(inventory.LineIn(product_id=prod.id, quantity=it["opening_qty"],
                                                        unit_cost=it["opening_rate"] or Decimal(0)))
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"{it['name']}: {getattr(exc, 'message', exc)}")
        if not out["errors"] and opening_lines and opening_date:
            post_opening_balances(db, access, as_of=opening_date, lines=opening_lines)
            out["opening_balances"] = len(opening_lines)
        if not out["errors"] and stock_lines and opening_date:
            inventory.create_opening(db, access, warehouse_id=warehouse_id, doc_date=opening_date, lines=stock_lines,
                                     reference="Tally import", notes="Opening stock from Tally")
            out["opening_stock_lines"] = len(stock_lines)
        if dry_run or out["errors"]:
            sp.rollback()
    out["dry_run"] = dry_run
    return out
