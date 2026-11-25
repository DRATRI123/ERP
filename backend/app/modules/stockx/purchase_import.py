"""Import a supplier's bill from their CSV / Excel file into a draft purchase bill.

Distributors send bills as files with their own column names and item names. Columns are found
by their usual names; each row is matched to a product by what this supplier called it before
(remembered from earlier imports), then barcode, our SKU / their code, then exact name. The user
fixes the rest once; the mapping is remembered for that supplier.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.catalog.models import Product
from app.modules.parties.models import Party
from app.modules.stockx.models import SupplierItemMap

COLUMNS: dict[str, tuple[str, ...]] = {
    "name": ("item name", "item", "product name", "product", "description", "particulars", "item description", "name",
             "medicine", "goods"),
    "code": ("item code", "code", "sku", "product code", "prod code"),
    "barcode": ("barcode", "ean", "bar code"),
    "hsn": ("hsn", "hsn code", "hsn/sac", "hsn sac"),
    "qty": ("qty", "quantity", "billed qty", "bill qty", "pcs"),
    "free": ("free", "free qty", "scheme qty", "sch qty", "fqty", "bonus"),
    "rate": ("rate", "purchase rate", "ptr", "price", "unit price", "unit rate", "net rate"),
    "disc": ("disc %", "disc", "discount %", "discount", "cd %", "cd", "dis %"),
    "gst": ("gst %", "gst", "gst rate", "tax %", "igst %", "tax rate"),
    "batch": ("batch no", "batch no.", "batch", "lot", "lot no"),
    "expiry": ("expiry", "exp", "exp date", "expiry date", "exp. date", "exp dt"),
    "mrp": ("mrp", "m.r.p."),
}
MAX_ROWS = 500
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def detect_columns(headers: list[str]) -> dict[str, str]:
    """Our field -> the file's header, by the first synonym found (exact, then prefix)."""
    found: dict[str, str] = {}
    normed = {h: _norm(h) for h in headers}
    for field, names in COLUMNS.items():
        for n in names:
            hit = next((h for h, nh in normed.items() if nh == _norm(n) and h not in found.values()), None)
            if hit:
                found[field] = hit
                break
    return found


def _num(v: str | None, places: int = 2) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("%", "").strip()).quantize(Decimal(1).scaleb(-places))
    except InvalidOperation:
        return None


def parse_expiry(v: str | None) -> date | None:
    """MM/YY, MM/YYYY, MM-YY, DD/MM/YYYY, YYYY-MM-DD, Excel dates. Month-only means the month's last day."""
    if not v:
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}( 00:00:00)?", s):
        return date.fromisoformat(s[:10])
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{2}|\d{4})", s)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        year += 2000 if year < 100 else 0
        if 1 <= month <= 12:
            nxt = date(year + (month == 12), month % 12 + 1, 1)
            return nxt - timedelta(days=1)
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y += 2000 if y < 100 else 0
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.fullmatch(r"([A-Za-z]{3})[- ](\d{2}|\d{4})", s)  # Mar-27
    if m and m.group(1).lower() in MONTHS:
        year = int(m.group(2)) + (2000 if len(m.group(2)) == 2 else 0)
        month = MONTHS.index(m.group(1).lower()) + 1
        return date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return None


def preview(db: Session, access: BusinessAccess, *, party_id: uuid.UUID, rows: list[dict[str, str]]) -> dict[str, Any]:
    access.require("purchases.create")
    party = db.get(Party, party_id)
    if party is None or party.business_id != access.business.id:
        raise ValidationFailed("Supplier not found")
    if not rows:
        raise ValidationFailed("The file has no rows")
    if len(rows) > MAX_ROWS:
        raise ValidationFailed(f"At most {MAX_ROWS} lines per bill")
    cols = detect_columns(list(rows[0].keys()))
    if "name" not in cols and "code" not in cols and "barcode" not in cols:
        raise ValidationFailed("Could not find the item name column. Name it “Item name” (or Product / Description).")
    if "qty" not in cols:
        raise ValidationFailed("Could not find the quantity column. Name it “Qty”.")
    bid = access.business.id
    remembered = {m.supplier_key: m.product_id for m in db.scalars(select(SupplierItemMap).where(
        SupplierItemMap.business_id == bid, SupplierItemMap.party_id == party_id))}
    products = list(db.scalars(select(Product).where(Product.business_id == bid, Product.is_active.is_(True))))
    by_barcode = {p.barcode: p for p in products if p.barcode}
    by_sku = {p.sku.lower(): p for p in products}
    by_name = {_norm(p.name): p for p in products}
    out = []
    for i, r in enumerate(rows, start=2):
        def get(f: str, r: dict[str, str] = r) -> str:
            return (r.get(cols[f]) or "").strip() if f in cols else ""

        name, code, barcode = get("name"), get("code"), get("barcode")
        if not (name or code or barcode):
            continue  # blank or total line
        qty = _num(get("qty"), 3)
        if not code and not barcode and _norm(name).split(" ")[0] in ("total", "grand", "subtotal", "sub"):
            continue  # the bill's total / sub-total line
        product, how = None, None
        for k in filter(None, (_norm(code) and f"code:{_norm(code)}", _norm(name) and f"name:{_norm(name)}")):
            if k in remembered:
                product, how = next((p for p in products if p.id == remembered[k]), None), "remembered"
                break
        if product is None and barcode and barcode in by_barcode:
            product, how = by_barcode[barcode], "barcode"
        if product is None and code and code.lower() in by_sku:
            product, how = by_sku[code.lower()], "code"
        if product is None and name and _norm(name) in by_name:
            product, how = by_name[_norm(name)], "name"
        suggestions = []
        if product is None and name:
            words = set(_norm(name).split())
            scored = sorted(((len(words & set(_norm(p.name).split())), p) for p in products), key=lambda x: -x[0])
            suggestions = [{"id": p.id, "name": p.name, "sku": p.sku} for s, p in scored[:3] if s > 0]
        problems = []
        if qty is None or qty <= 0:
            problems.append("quantity missing")
        rate = _num(get("rate"), 4)
        if rate is None:
            problems.append("rate missing")
        expiry_raw = get("expiry")
        expiry = parse_expiry(expiry_raw)
        if expiry_raw and expiry is None:
            problems.append(f"expiry {expiry_raw!r} not understood")
        out.append({
            "row": i, "supplier_text": name or code, "supplier_key": f"code:{_norm(code)}" if code else f"name:{_norm(name)}",
            "name": name, "code": code or None, "barcode": barcode or None, "hsn": get("hsn") or None,
            "quantity": qty, "free": _num(get("free"), 3) or Decimal(0), "rate": rate,
            "discount_percent": _num(get("disc"), 2) or Decimal(0), "gst_rate": _num(get("gst"), 2),
            "batch_no": get("batch") or None, "expiry_date": expiry, "mrp": _num(get("mrp"), 2),
            "product_id": product.id if product else None, "product_name": product.name if product else None,
            "batch_tracking": bool(product and product.batch_tracking), "match": how,
            "suggestions": suggestions, "problems": problems,
        })
    return {"columns": cols, "rows": out, "matched": sum(1 for r in out if r["product_id"]), "total": len(out)}


def create_bill(db: Session, access: BusinessAccess, *, party_id: uuid.UUID, rows: list[dict[str, Any]],
                header: dict[str, Any], remember: bool = True) -> Any:
    """Draft purchase bill from the checked rows; free quantity becomes a zero-rate line."""
    from app.modules.billing.doctypes import DocType
    from app.modules.billing.service import LineIn, create_draft

    access.require("purchases.create")
    if not rows:
        raise ValidationFailed("No lines to import")
    lines: list[LineIn] = []
    for r in rows:
        common = {"product_id": r["product_id"], "batch_no": r.get("batch_no"), "expiry_date": r.get("expiry_date"),
                  "mrp": r.get("mrp"), "gst_rate": r.get("gst_rate")}
        lines.append(LineIn(quantity=r["quantity"], unit_price=r["rate"], discount_percent=r.get("discount_percent") or Decimal(0),
                            **common))
        if r.get("free") and r["free"] > 0:
            lines.append(LineIn(quantity=r["free"], unit_price=Decimal(0), custom_fields={"free_from_supplier": True},
                                **common))
    doc = create_draft(db, access, DocType.PURCHASE_INVOICE, {"party_id": party_id, **header}, lines)
    if remember:
        bid = access.business.id
        for r in rows:
            key, text = r.get("supplier_key"), r.get("supplier_text")
            if not key or not text:
                continue
            m = db.scalar(select(SupplierItemMap).where(SupplierItemMap.business_id == bid,
                                                        SupplierItemMap.party_id == party_id, SupplierItemMap.supplier_key == key))
            if m is None:
                db.add(SupplierItemMap(business_id=bid, party_id=party_id, supplier_key=key[:200], supplier_text=text[:200],
                                       product_id=r["product_id"]))
            else:
                m.product_id = r["product_id"]
        db.flush()
    return doc


def forget(db: Session, access: BusinessAccess, *, party_id: uuid.UUID, map_id: uuid.UUID) -> None:
    access.require("purchases.create")
    m = db.get(SupplierItemMap, map_id)
    if m is None or m.business_id != access.business.id or m.party_id != party_id:
        raise ValidationFailed("Mapping not found")
    db.delete(m)


def mappings(db: Session, access: BusinessAccess, party_id: uuid.UUID) -> list[dict[str, Any]]:
    access.require("purchases.view")
    rows = db.execute(select(SupplierItemMap, Product.name).join(Product, Product.id == SupplierItemMap.product_id).where(
        SupplierItemMap.business_id == access.business.id, SupplierItemMap.party_id == party_id)
        .order_by(func.lower(SupplierItemMap.supplier_text))).all()
    return [{"id": m.id, "supplier_text": m.supplier_text, "product_id": m.product_id, "product_name": n} for m, n in rows]



