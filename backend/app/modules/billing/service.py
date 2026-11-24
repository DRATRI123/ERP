"""The billing document engine: drafts, pricing & GST, finalization, cancellation, conversion.

Server-side totals are the only truth: whatever totals a client sends are ignored and every
draft save recomputes lines and totals from quantities, prices and tax rules.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.clock import local_today
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing import series as series_service
from app.modules.billing.doctypes import SPECS, DocType, DocTypeSpec, Side
from app.modules.billing.models import (
    DocStatus,
    Document,
    DocumentLine,
    HsnTaxRate,
    PaymentStatus,
    PriceTier,
    TaxMode,
)
from app.modules.businesses.models import Branch, FinancialYear, GstRegistrationType
from app.modules.catalog.models import Product, ProductKind, Unit
from app.modules.events import service as events
from app.modules.gst.tax import (
    UTGST_STATES,
    ZERO,
    LineInput,
    TaxCategory,
    TaxError,
    compute_line,
    is_interstate,
    round_off,
)
from app.modules.inventory.engine import Movement, StockEngine, q4
from app.modules.inventory.models import Batch, StockLedgerEntry, Warehouse
from app.modules.parties.models import Party, PartyAddress, PartyGstType

SOURCE = "billing_document"
_REGISTERED = (PartyGstType.REGULAR, PartyGstType.COMPOSITION, PartyGstType.SEZ)
_EXPORT = (PartyGstType.OVERSEAS, PartyGstType.SEZ)
HEADER_FIELDS = [
    "branch_id", "series_id", "doc_date", "due_date", "client_ref", "party_id", "party_name",
    "place_of_supply", "billing_address_id", "shipping_address_id", "tax_mode",
    "reverse_charge", "price_tier", "prices_include_tax", "warehouse_id", "restock",
    "reference_document_id", "supplier_invoice_no", "supplier_invoice_date", "landed_cost",
    "salesperson_id", "notes", "terms", "custom_fields",
]


@dataclass
class LineIn:
    product_id: uuid.UUID | None = None
    description: str | None = None
    hsn_sac: str | None = None
    quantity: Decimal = Decimal(1)
    unit_price: Decimal | None = None
    discount_percent: Decimal = ZERO
    discount_amount: Decimal = ZERO
    price_includes_tax: bool | None = None
    tax_category: TaxCategory | None = None
    gst_rate: Decimal | None = None
    cess_rate: Decimal | None = None
    itc_eligible: bool = True
    batch_no: str | None = None
    expiry_date: date | None = None
    mrp: Decimal | None = None  # purchase lines: the MRP printed on this batch
    serials: list[str] | None = None
    source_line_id: uuid.UUID | None = None
    custom_fields: dict[str, Any] | None = None


def spec_of(doc: Document) -> DocTypeSpec:
    return SPECS[doc.doc_type]


# ---------------------------------------------------------------------------- access helpers


def require_for(access: BusinessAccess, spec: DocTypeSpec, action: str) -> None:
    access.require(getattr(spec, f"{action}_permission"))


def get_document(db: Session, access: BusinessAccess, doc_id: uuid.UUID) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None or doc.business_id != access.business.id or not access.can_access_branch(doc.branch_id):
        raise NotFound("Document not found")
    require_for(access, spec_of(doc), "view")
    return doc


def _branch(db: Session, access: BusinessAccess, branch_id: uuid.UUID | None) -> Branch:
    if branch_id is None:
        if not access.all_branches and len(access.branch_ids) == 1:
            branch_id = next(iter(access.branch_ids))
        else:
            branch_id = db.scalar(select(Branch.id).where(
                Branch.business_id == access.business.id, Branch.kind == "head_office"))
    b = db.get(Branch, branch_id) if branch_id else None
    if b is None or b.business_id != access.business.id or not access.can_access_branch(b.id):
        raise ValidationFailed("Branch not found or not accessible")
    if not b.is_active:
        raise ValidationFailed(f"Branch {b.code} is inactive")
    return b


def _party(db: Session, access: BusinessAccess, spec: DocTypeSpec, party_id: uuid.UUID | None) -> Party | None:
    if party_id is None:
        return None
    p = db.get(Party, party_id)
    if p is None or p.business_id != access.business.id:
        raise ValidationFailed("Party not found in this business")
    if spec.side == Side.SALES and not p.is_customer:
        raise ValidationFailed(f"{p.name} is not a customer")
    if spec.side == Side.PURCHASE and not p.is_supplier:
        raise ValidationFailed(f"{p.name} is not a supplier")
    if not p.is_active:
        raise ValidationFailed(f"{p.name} is archived")
    return p


def _address(db: Session, party: Party | None, address_id: uuid.UUID | None) -> PartyAddress | None:
    if address_id is None:
        return None
    a = db.get(PartyAddress, address_id)
    if a is None or party is None or a.party_id != party.id:
        raise ValidationFailed("Address does not belong to this party")
    return a


# ---------------------------------------------------------------------------- pricing & tax


def hsn_rate(db: Session, business_id: uuid.UUID, hsn: str | None, on: date) -> HsnTaxRate | None:
    """Most specific HSN rule effective on the date."""
    if not hsn:
        return None
    prefixes = [hsn[:n] for n in range(len(hsn), 1, -1)]
    rows = db.scalars(select(HsnTaxRate).where(
        HsnTaxRate.business_id == business_id, HsnTaxRate.hsn_prefix.in_(prefixes),
        HsnTaxRate.effective_from <= on,
        or_(HsnTaxRate.effective_to.is_(None), HsnTaxRate.effective_to >= on)))
    return max(rows, key=lambda r: (len(r.hsn_prefix), r.effective_from), default=None)


def _seller_charges_tax(access: BusinessAccess, doc: Document, spec: DocTypeSpec,
                        party: Party | None) -> bool:
    if spec.side == Side.SALES:
        if access.business.gst_registration_type in (GstRegistrationType.COMPOSITION,
                                                     GstRegistrationType.UNREGISTERED):
            return False
        return doc.tax_mode == TaxMode.GST
    # Purchases: the supplier charges GST only if registered as a regular/SEZ taxpayer; under
    # reverse charge we compute the tax we owe ourselves.
    if doc.reverse_charge:
        return True
    if doc.tax_mode == TaxMode.BILL_OF_SUPPLY:
        return False
    return party is not None and party.gst_type in (PartyGstType.REGULAR, PartyGstType.SEZ)


def _price_line(db: Session, access: BusinessAccess, doc: Document, spec: DocTypeSpec, i: int,
                li: LineIn) -> DocumentLine:
    """Fill a line from the product (price by tier, HSN, versioned GST rate) and the input."""
    product: Product | None = None
    if li.product_id is not None:
        product = db.get(Product, li.product_id)
        if product is None or product.business_id != access.business.id:
            raise ValidationFailed(f"Line {i}: product not found")
        if not product.is_active:
            raise ValidationFailed(f"Line {i}: {product.name} is archived")
    description = li.description or (product.name if product else None)
    if not description:
        raise ValidationFailed(f"Line {i}: description is required for lines without a product")
    unit = db.get(Unit, product.unit_id) if product else None
    if product and unit is not None and li.quantity != li.quantity.quantize(Decimal(1).scaleb(-unit.decimals)):
        raise ValidationFailed(f"Line {i}: {unit.code} allows {unit.decimals} decimal places")

    price, inclusive = li.unit_price, li.price_includes_tax
    if price is None and product is not None:
        listed = _party_list_price(db, doc, spec, product, li.quantity)
        if listed is not None:
            price, list_inclusive = listed
            inclusive = list_inclusive if inclusive is None else inclusive
        elif spec.side == Side.PURCHASE:
            price = product.purchase_price
        elif doc.price_tier == PriceTier.WHOLESALE:
            price = product.wholesale_price or product.selling_price
        elif doc.price_tier == PriceTier.MRP:
            price, inclusive = product.mrp, True if inclusive is None else inclusive
        else:
            price = product.selling_price
        if inclusive is None and spec.side == Side.SALES:
            inclusive = product.prices_include_tax
    if price is None:
        raise ValidationFailed(f"Line {i}: unit price is required")
    if inclusive is None:
        inclusive = doc.prices_include_tax

    hsn = li.hsn_sac or (product.hsn_sac if product else None)
    rule = hsn_rate(db, access.business.id, hsn, doc.doc_date)
    gst = li.gst_rate if li.gst_rate is not None else (
        rule.gst_rate if rule else (product.gst_rate if product else ZERO))
    cess = li.cess_rate if li.cess_rate is not None else (
        rule.cess_rate if rule else (product.cess_rate if product else ZERO))
    category = li.tax_category or (TaxCategory.NIL_RATED if gst == 0 and cess == 0
                                   else TaxCategory.TAXABLE)
    return DocumentLine(
        line_no=i, product_id=product.id if product else None, description=description[:500],
        hsn_sac=hsn, unit_code=unit.code if unit else None, uqc=unit.uqc if unit else None,
        is_service=bool(product and product.kind == ProductKind.SERVICE),
        quantity=li.quantity, unit_price=price,
        mrp=li.mrp if li.mrp is not None else (product.mrp if product else None),
        discount_percent=li.discount_percent, discount_amount=li.discount_amount,
        price_includes_tax=bool(inclusive), tax_category=category, gst_rate=gst, cess_rate=cess,
        itc_eligible=li.itc_eligible if spec.side == Side.PURCHASE else True,
        batch_no=li.batch_no, expiry_date=li.expiry_date, serials=li.serials or None,
        source_line_id=li.source_line_id, custom_fields=li.custom_fields or {},
    )


def _party_list_price(db: Session, doc: Document, spec: DocTypeSpec, product: Product,
                      qty: Decimal) -> tuple[Decimal, bool | None] | None:
    if spec.side != Side.SALES or doc.party_id is None:
        return None
    party = db.get(Party, doc.party_id)
    if party is None or party.price_list_id is None:
        return None
    from app.modules.pricing.service import list_price

    return list_price(db, party.price_list_id, product, qty)


def recompute(db: Session, access: BusinessAccess, doc: Document) -> None:
    """Apply place-of-supply and GST rules to every line and total the document."""
    spec = spec_of(doc)
    party = db.get(Party, doc.party_id) if doc.party_id else None
    branch = db.get(Branch, doc.branch_id)
    assert branch is not None
    business = access.business

    if spec.side == Side.SALES and business.gst_registration_type in (
            GstRegistrationType.COMPOSITION, GstRegistrationType.UNREGISTERED):
        doc.tax_mode = TaxMode.BILL_OF_SUPPLY
    our_state = branch.state_code or business.state_code
    if spec.side == Side.SALES:
        doc.seller_state = our_state
        doc.seller_gstin = branch.gstin or business.gstin
        if doc.place_of_supply is None:
            ship = db.get(PartyAddress, doc.shipping_address_id) if doc.shipping_address_id else None
            doc.place_of_supply = (ship.state_code if ship and ship.state_code else None) or (
                party.state_code if party else None) or our_state
        supplier_state = our_state
    else:  # purchase: the supplier is the seller; we are the place of supply
        doc.seller_state = party.state_code if party else None
        doc.seller_gstin = party.gstin if party else None
        doc.place_of_supply = doc.place_of_supply or our_state
        supplier_state = doc.seller_state
    export = party is not None and party.gst_type in _EXPORT and spec.side == Side.SALES
    doc.is_interstate = is_interstate(supplier_state, doc.place_of_supply, export_or_sez=export)
    doc.is_utgst = not doc.is_interstate and doc.place_of_supply in UTGST_STATES
    charge = _seller_charges_tax(access, doc, spec, party)

    totals = dict.fromkeys(("subtotal", "discount_total", "taxable_total", "cgst_total",
                            "sgst_total", "igst_total", "cess_total"), ZERO)
    for line in doc.lines:
        try:
            t = compute_line(LineInput(
                quantity=line.quantity, unit_price=line.unit_price, gst_rate=line.gst_rate,
                cess_rate=line.cess_rate, discount_percent=line.discount_percent,
                discount_amount=line.discount_amount, price_includes_tax=line.price_includes_tax,
                category=line.tax_category), interstate=doc.is_interstate, charge_tax=charge)
        except TaxError as exc:
            raise ValidationFailed(f"Line {line.line_no}: {exc}") from exc
        line.gross_amount, line.discount_total, line.taxable_value = t.gross, t.discount, t.taxable_value
        line.cgst_amount, line.sgst_amount, line.igst_amount = t.cgst, t.sgst, t.igst
        line.cess_amount, line.line_total = t.cess, t.total
        totals["subtotal"] += t.gross
        totals["discount_total"] += t.discount
        totals["taxable_total"] += t.taxable_value
        totals["cgst_total"] += t.cgst
        totals["sgst_total"] += t.sgst
        totals["igst_total"] += t.igst
        totals["cess_total"] += t.cess
    for k, v in totals.items():
        setattr(doc, k, v)
    doc.tax_total = doc.cgst_total + doc.sgst_total + doc.igst_total + doc.cess_total
    value = doc.taxable_total + doc.tax_total
    doc.round_off = round_off(value) if business.settings.get("round_off", True) else ZERO
    doc.grand_total = value + doc.round_off
    doc.amount_payable = doc.grand_total - (doc.tax_total if doc.reverse_charge else ZERO)
    doc.balance_due = doc.amount_payable - doc.amount_paid


# ---------------------------------------------------------------------------- drafts


def _validate_reference(db: Session, access: BusinessAccess, doc: Document, spec: DocTypeSpec) -> Document | None:
    if doc.reference_document_id is None:
        if spec.requires_reference:
            raise ValidationFailed(f"A {spec.label} must reference the original document")
        return None
    ref = db.get(Document, doc.reference_document_id)
    if ref is None or ref.business_id != access.business.id:
        raise ValidationFailed("Referenced document not found")
    if ref.doc_type not in spec.converts_from:
        raise ValidationFailed(f"A {spec.label} cannot be based on a {SPECS[ref.doc_type].label}")
    if ref.status != DocStatus.FINAL:
        raise ValidationFailed("Only finalized documents can be referenced")
    if ref.party_id and doc.party_id != ref.party_id:
        raise ValidationFailed("The party must match the referenced document")
    source_ids = {ln.id for ln in ref.lines}
    for ln in doc.lines:
        if ln.source_line_id is not None and ln.source_line_id not in source_ids:
            raise ValidationFailed(f"Line {ln.line_no}: source line is not on the referenced document")
        if ref.doc_type in spec.consumes_source and spec.requires_reference and ln.source_line_id is None:
            raise ValidationFailed(f"Line {ln.line_no}: must refer to a line of {ref.number}")
    return ref


def _apply(db: Session, access: BusinessAccess, doc: Document, spec: DocTypeSpec,
           header: dict[str, Any], lines: list[LineIn] | None) -> None:
    if ({"party_id", "shipping_address_id"} & header.keys()) and "place_of_supply" not in header:
        doc.place_of_supply = None  # re-derive from the new customer / address
    for k, v in header.items():
        setattr(doc, k, v)
    doc.branch_id = _branch(db, access, doc.branch_id).id
    party = _party(db, access, spec, doc.party_id)
    _address(db, party, doc.billing_address_id)
    _address(db, party, doc.shipping_address_id)
    if party is not None:
        doc.party_name = party.name
    if doc.warehouse_id is not None:
        w = db.get(Warehouse, doc.warehouse_id)
        if w is None or w.business_id != access.business.id or not access.can_access_branch(w.branch_id):
            raise ValidationFailed("Warehouse not found or not accessible")
    if doc.landed_cost and spec.code != DocType.PURCHASE_INVOICE:
        raise ValidationFailed("Landed cost applies to purchase invoices only")
    if lines is not None:
        if not lines:
            raise ValidationFailed("A document needs at least one line")
        if doc.lines:
            doc.lines.clear()
            db.flush()  # delete old lines before inserting new ones with the same line numbers
        doc.lines.extend(_price_line(db, access, doc, spec, i, li) for i, li in enumerate(lines, 1))
        if spec.side == Side.SALES and spec.code not in (DocType.CREDIT_NOTE, DocType.DEBIT_NOTE):
            from app.modules.pricing.service import apply_schemes

            apply_schemes(db, access, doc)
    _validate_reference(db, access, doc, spec)
    recompute(db, access, doc)


def create_draft(db: Session, access: BusinessAccess, doc_type: DocType, header: dict[str, Any],
                 lines: list[LineIn]) -> Document:
    spec = SPECS[doc_type]
    require_for(access, spec, "create")
    if header.get("client_ref"):
        existing = db.scalar(select(Document).where(
            Document.business_id == access.business.id, Document.client_ref == header["client_ref"]))
        if existing is not None:  # idempotent retry (offline sync, flaky network)
            if existing.doc_type != doc_type:
                raise Conflict("client_ref is already used by another document")
            return existing
    # Column defaults only apply at flush, but pricing runs before it: set them explicitly.
    doc = Document(business_id=access.business.id, doc_type=doc_type, status=DocStatus.DRAFT,
                   created_by_id=access.user.id, branch_id=header.get("branch_id"),
                   tax_mode=TaxMode.GST, reverse_charge=False, price_tier=PriceTier.RETAIL,
                   prices_include_tax=False, restock=True, landed_cost=ZERO, amount_paid=ZERO,
                   payment_status=PaymentStatus.NOT_APPLICABLE, custom_fields={})
    # Build it completely before it joins the session, so no lookup query autoflushes a
    # half-built row.
    with db.no_autoflush:
        _apply(db, access, doc, spec, header, lines)
    db.add(doc)
    db.flush()
    audit.record(db, action="document.draft_created", entity_type="document", entity_id=doc.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"doc_type": doc_type.value, "total": doc.grand_total})
    return doc


def update_draft(db: Session, access: BusinessAccess, doc: Document, header: dict[str, Any],
                 lines: list[LineIn] | None) -> Document:
    spec = spec_of(doc)
    require_for(access, spec, "edit")
    if doc.status != DocStatus.DRAFT:
        raise Conflict("Finalized documents cannot be edited; cancel it or issue a credit/debit note")
    with db.no_autoflush:
        _apply(db, access, doc, spec, header, lines)
    audit.record(db, action="document.draft_updated", entity_type="document", entity_id=doc.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"total": doc.grand_total})
    return doc


def delete_draft(db: Session, access: BusinessAccess, doc: Document) -> None:
    require_for(access, spec_of(doc), "edit")
    if doc.status != DocStatus.DRAFT:
        raise Conflict("Only drafts can be deleted")
    if db.scalar(select(Document.id).where(Document.reference_document_id == doc.id).limit(1)):
        raise Conflict("Another document refers to this draft")
    audit.record(db, action="document.draft_deleted", entity_type="document", entity_id=doc.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"doc_type": doc.doc_type.value})
    db.delete(doc)


def convert(db: Session, access: BusinessAccess, source: Document, target: DocType,
            header: dict[str, Any] | None = None) -> Document:
    """New draft of ``target`` pre-filled from a finalized ``source`` (lines linked)."""
    spec = SPECS[target]
    if source.doc_type not in spec.converts_from:
        raise ValidationFailed(f"A {SPECS[source.doc_type].label} cannot become a {spec.label}")
    if source.status != DocStatus.FINAL:
        raise ValidationFailed("Only finalized documents can be converted")
    remaining = _remaining_quantities(db, source, target) if source.doc_type in spec.consumes_source else {}
    lines = []
    for ln in source.lines:
        qty = remaining.get(ln.id, ln.quantity)
        if qty <= 0:
            continue
        lines.append(LineIn(
            product_id=ln.product_id, description=ln.description, hsn_sac=ln.hsn_sac,
            quantity=qty, unit_price=ln.unit_price, discount_percent=ln.discount_percent,
            discount_amount=ln.discount_amount if qty == ln.quantity else ZERO,
            price_includes_tax=ln.price_includes_tax, tax_category=ln.tax_category, gst_rate=ln.gst_rate,
            cess_rate=ln.cess_rate, itc_eligible=ln.itc_eligible, batch_no=ln.batch_no,
            expiry_date=ln.expiry_date, serials=ln.serials, source_line_id=ln.id))
    if not lines:
        raise Conflict(f"Nothing left on {source.number} to convert")
    base = {
        "branch_id": source.branch_id, "doc_date": local_today(), "party_id": source.party_id,
        "party_name": source.party_name, "place_of_supply": source.place_of_supply,
        "billing_address_id": source.billing_address_id,
        "shipping_address_id": source.shipping_address_id, "tax_mode": source.tax_mode,
        "reverse_charge": source.reverse_charge, "price_tier": source.price_tier,
        "warehouse_id": source.warehouse_id, "reference_document_id": source.id,
        "salesperson_id": source.salesperson_id, "terms": source.terms,
    }
    return create_draft(db, access, target, {**base, **(header or {})}, lines)


def _remaining_quantities(db: Session, source: Document, target: DocType,
                          exclude: uuid.UUID | None = None) -> dict[uuid.UUID, Decimal]:
    used: dict[uuid.UUID, Decimal] = dict(db.execute(
        select(DocumentLine.source_line_id, func.sum(DocumentLine.quantity))
        .join(Document, Document.id == DocumentLine.document_id)
        .where(Document.reference_document_id == source.id, Document.doc_type == target,
               Document.status == DocStatus.FINAL,
               *([Document.id != exclude] if exclude else []))
        .group_by(DocumentLine.source_line_id)).all())
    return {ln.id: ln.quantity - (used.get(ln.id) or ZERO) for ln in source.lines}


# ---------------------------------------------------------------------------- finalize


def _open_fy(db: Session, business_id: uuid.UUID, d: date) -> FinancialYear:
    fy = db.scalar(select(FinancialYear).where(
        FinancialYear.business_id == business_id, FinancialYear.start_date <= d,
        FinancialYear.end_date >= d))
    if fy is None:
        raise ValidationFailed(f"No financial year covers {d.isoformat()}; create it first")
    if fy.is_closed:
        raise ValidationFailed(f"Financial year {fy.code} is closed")
    return fy


def _snapshot_party(db: Session, doc: Document, party: Party | None) -> dict[str, Any] | None:
    if party is None:
        return {"name": doc.party_name or "Cash Sale"}

    def addr(a: PartyAddress | None) -> dict[str, Any] | None:
        if a is None:
            return None
        return {k: getattr(a, k) for k in ("address_line1", "address_line2", "city",
                                           "state_code", "pincode", "country")}

    def default(kind: str) -> PartyAddress | None:
        return next((a for a in party.addresses if a.kind.value == kind and a.is_default), None)

    bill = db.get(PartyAddress, doc.billing_address_id) if doc.billing_address_id else default("billing")
    ship = db.get(PartyAddress, doc.shipping_address_id) if doc.shipping_address_id else default("shipping")
    return {"id": str(party.id), "name": party.name, "gstin": party.gstin, "pan": party.pan,
            "gst_type": party.gst_type.value, "state_code": party.state_code,
            "phone": party.phone, "email": party.email,
            "billing_address": addr(bill), "shipping_address": addr(ship)}


def _snapshot_seller(access: BusinessAccess, branch: Branch) -> dict[str, Any]:
    b = access.business
    return {"name": b.name, "legal_name": b.legal_name or b.name, "gstin": branch.gstin or b.gstin,
            "pan": b.pan, "gst_registration_type": b.gst_registration_type.value,
            "phone": branch.phone or b.phone, "email": b.email, "branch": branch.name,
            "address_line1": branch.address_line1 or b.address_line1,
            "address_line2": branch.address_line2 or b.address_line2,
            "city": branch.city or b.city, "state_code": branch.state_code or b.state_code,
            "pincode": branch.pincode or b.pincode, "licences": _printable_licences(access, branch)}


def _printable_licences(access: BusinessAccess, branch: Branch) -> list[dict[str, str]]:
    from sqlalchemy.orm import object_session

    from app.modules.compliance.service import printable

    db = object_session(branch)
    return printable(db, access.business.id, branch.id, local_today()) if db is not None else []


def party_outstanding(db: Session, party_id: uuid.UUID, side: Side) -> Decimal:
    """Amount the party owes us (sales) or we owe them (purchases), from final documents."""
    plus = (DocType.SALES_INVOICE, DocType.DEBIT_NOTE) if side == Side.SALES else (DocType.PURCHASE_INVOICE,)
    minus = (DocType.CREDIT_NOTE,) if side == Side.SALES else (DocType.PURCHASE_RETURN,)

    def total(types: tuple[DocType, ...]) -> Decimal:
        v = db.scalar(select(func.coalesce(func.sum(Document.balance_due), 0)).where(
            Document.party_id == party_id, Document.status == DocStatus.FINAL,
            Document.doc_type.in_(types)))
        return Decimal(v or 0)

    return total(plus) - total(minus)


def _default_warehouse(db: Session, access: BusinessAccess, branch_id: uuid.UUID) -> uuid.UUID:
    w = db.scalar(select(Warehouse.id).where(
        Warehouse.business_id == access.business.id, Warehouse.branch_id == branch_id,
        Warehouse.is_active.is_(True)).order_by(Warehouse.code))
    if w is None:
        raise ValidationFailed("This branch has no active warehouse; choose a warehouse")
    return w


def _batch_id(db: Session, access: BusinessAccess, product: Product, line: DocumentLine,
              *, create: bool) -> uuid.UUID | None:
    if not product.batch_tracking:
        return None
    if not line.batch_no:
        raise ValidationFailed(f"Line {line.line_no}: {product.name} needs a batch number")
    b = db.scalar(select(Batch).where(Batch.product_id == product.id, Batch.batch_no == line.batch_no))
    if b is None:
        if not create:
            raise ValidationFailed(f"Line {line.line_no}: batch {line.batch_no} does not exist")
        b = Batch(business_id=access.business.id, product_id=product.id, batch_no=line.batch_no,
                  expiry_date=line.expiry_date, mrp=line.mrp)
        db.add(b)
        db.flush()
    return b.id


def _source_issue_entry(db: Session, line: DocumentLine) -> StockLedgerEntry | None:
    """The stock issue that a returned line originally came from (follows invoice→challan)."""
    seen = 0
    src_id = line.source_line_id
    while src_id is not None and seen < 5:
        e = db.scalar(select(StockLedgerEntry).where(
            StockLedgerEntry.source_type == SOURCE, StockLedgerEntry.source_line_id == src_id,
            StockLedgerEntry.quantity < 0, StockLedgerEntry.reverses_entry_id.is_(None)))
        if e is not None:
            return e
        src = db.get(DocumentLine, src_id)
        src_id = src.source_line_id if src else None
        seen += 1
    return None


def _stock_movements(db: Session, access: BusinessAccess, doc: Document, spec: DocTypeSpec,
                     ref: Document | None) -> list[Movement]:
    if spec.stock == 0 or (doc.doc_type == DocType.CREDIT_NOTE and not doc.restock):
        return []
    movements: list[Movement] = []
    landed_base = sum((ln.taxable_value for ln in doc.lines if ln.product_id), ZERO)
    for ln in doc.lines:
        product = db.get(Product, ln.product_id) if ln.product_id else None
        if product is None or not product.track_inventory:
            continue
        if (doc.doc_type == DocType.SALES_INVOICE and ref is not None
                and ref.doc_type == DocType.DELIVERY_CHALLAN and ln.source_line_id):
            continue  # stock already left with the delivery challan
        if doc.warehouse_id is None:
            doc.warehouse_id = _default_warehouse(db, access, doc.branch_id)
        serials = list(ln.serials or [])
        if spec.stock < 0:
            movements.append(Movement(product, doc.warehouse_id, -ln.quantity,
                                      _batch_id(db, access, product, ln, create=False),
                                      serials=serials, line_id=ln.id))
            continue
        if doc.doc_type == DocType.PURCHASE_INVOICE:
            cost = ln.taxable_value
            if not ln.itc_eligible:  # tax we cannot claim back becomes part of the cost
                cost += ln.cgst_amount + ln.sgst_amount + ln.igst_amount + ln.cess_amount
            if doc.landed_cost and landed_base:
                cost += q4(doc.landed_cost * ln.taxable_value / landed_base)
            movements.append(Movement(product, doc.warehouse_id, ln.quantity,
                                      _batch_id(db, access, product, ln, create=True),
                                      value=q4(cost), serials=serials, line_id=ln.id))
        else:  # credit note: back into stock at the cost it left with
            issue = _source_issue_entry(db, ln)
            if issue is None:
                raise ValidationFailed(f"Line {ln.line_no}: the original stock issue was not found")
            # Returning everything that left: exactly its value; part of it: at the issue cost.
            value = -issue.value if issue.quantity == -ln.quantity else q4(issue.unit_cost * ln.quantity)
            src = db.get(DocumentLine, ln.source_line_id)
            if serials and not set(serials) <= set(src.serials or []):  # type: ignore[union-attr]
                raise ValidationFailed(f"Line {ln.line_no}: serials were not on the original invoice")
            movements.append(Movement(product, issue.warehouse_id, ln.quantity, issue.batch_id,
                                      value=value, serials=serials, line_id=ln.id))
    return movements


def finalize(db: Session, access: BusinessAccess, doc: Document, *,
             payments: list[dict[str, Any]] | None = None) -> Document:
    spec = spec_of(doc)
    require_for(access, spec, "create")
    if doc.status != DocStatus.DRAFT:
        raise Conflict("This document is already finalized")
    # Batches first-expiry-first-out, no expired batches on sales, batch MRP on the line.
    if spec.stock < 0:
        from app.modules.inventory.expiry import prepare_issue_lines

        if doc.warehouse_id is None and any(ln.product_id for ln in doc.lines):
            doc.warehouse_id = _default_warehouse(db, access, doc.branch_id)
        prepare_issue_lines(db, access, doc, spec, doc.warehouse_id)
    # Re-price from current rules: the draft may have been saved before a rate change.
    recompute(db, access, doc)
    party = _party(db, access, spec, doc.party_id)
    if spec.requires_party and party is None:
        raise ValidationFailed(f"A {spec.label} needs a party")
    if doc.doc_type == DocType.PURCHASE_INVOICE:
        if not doc.supplier_invoice_no:
            raise ValidationFailed("Enter the supplier's invoice number")
        dup = db.scalar(select(Document.id).where(
            Document.business_id == access.business.id, Document.party_id == doc.party_id,
            Document.doc_type == DocType.PURCHASE_INVOICE, Document.status == DocStatus.FINAL,
            Document.supplier_invoice_no == doc.supplier_invoice_no))
        if dup:
            raise Conflict(f"Supplier invoice {doc.supplier_invoice_no} is already recorded")
    ref = _validate_reference(db, access, doc, spec)
    if ref is not None and ref.doc_type in spec.consumes_source:
        remaining = _remaining_quantities(db, ref, doc.doc_type, exclude=doc.id)
        for ln in doc.lines:
            if ln.source_line_id is not None and ln.quantity > remaining.get(ln.source_line_id, ZERO):
                raise ValidationFailed(
                    f"Line {ln.line_no}: only {remaining.get(ln.source_line_id, ZERO)} left on {ref.number}")

    branch = db.get(Branch, doc.branch_id)
    assert branch is not None
    fy = _open_fy(db, access.business.id, doc.doc_date)
    s = series_service.resolve(db, access.business.id, doc.doc_type, branch.id, doc.series_id)
    doc.series_id = s.id
    doc.financial_year_id = fy.id
    doc.number = series_service.allocate(db, s, fy, branch, gst_rules=spec.gst_number_rules)
    doc.party_snapshot = _snapshot_party(db, doc, party)
    doc.seller_snapshot = _snapshot_seller(access, branch) if spec.side == Side.SALES else doc.party_snapshot
    if spec.payable and doc.due_date is None:
        days = party.credit_days if party and party.credit_days else 0
        doc.due_date = doc.doc_date + timedelta(days=days)
    doc.status = DocStatus.FINAL
    doc.finalized_at = datetime.now(UTC)
    doc.finalized_by_id = access.user.id
    doc.amount_paid = ZERO
    doc.balance_due = doc.amount_payable if spec.payable else ZERO
    doc.payment_status = PaymentStatus.UNPAID if spec.payable else PaymentStatus.NOT_APPLICABLE
    if spec.payable and doc.amount_payable == 0:
        doc.payment_status = PaymentStatus.PAID

    movements = _stock_movements(db, access, doc, spec, ref)
    if movements:
        StockEngine(db, access.business).post(movements, entry_date=doc.doc_date,
                                              source_type=SOURCE, source_id=doc.id)
    db.flush()
    from app.modules.accounting.posting import post_document

    post_document(db, access.business, doc, user_id=access.user.id)

    if payments:
        if not spec.payable:
            raise ValidationFailed(f"A {spec.label} does not take payments")
        from app.modules.billing.payments import pay_document

        for p in payments:
            pay_document(db, access, doc, **p)
    if doc.doc_type == DocType.SALES_INVOICE and party is None and doc.balance_due != 0:
        raise ValidationFailed("A cash sale without a customer must be paid in full")
    if doc.doc_type in (DocType.SALES_INVOICE, DocType.DEBIT_NOTE) and party is not None \
            and party.credit_hold and doc.balance_due > 0 and not access.can("credit_limit.override"):
        raise Conflict(f"{party.name} is on credit hold: collect payment in full, or ask someone who may "
                       "override credit limits", code="credit_hold")
    if doc.doc_type in (DocType.SALES_INVOICE, DocType.DEBIT_NOTE) and party is not None \
            and party.credit_limit is not None and doc.balance_due > 0:
        exposure = party_outstanding(db, party.id, Side.SALES)
        if exposure > party.credit_limit and not access.can("credit_limit.override"):
            raise Conflict(
                f"Credit limit exceeded: {party.name} would owe {exposure} (limit {party.credit_limit})",
                code="credit_limit_exceeded",
                details={"outstanding": str(exposure), "credit_limit": str(party.credit_limit)})

    audit.record(db, action="document.finalized", entity_type="document", entity_id=doc.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"doc_type": doc.doc_type.value, "number": doc.number,
                          "grand_total": doc.grand_total})
    _emit(db, doc, "created")
    return doc


def _emit(db: Session, doc: Document, what: str) -> None:
    payload = {"id": doc.id, "doc_type": doc.doc_type.value, "number": doc.number,
               "branch_id": doc.branch_id, "status": doc.status.value, "party_id": doc.party_id, "doc_date": doc.doc_date,
               "grand_total": doc.grand_total, "balance_due": doc.balance_due}
    kind = {"created": "finalized", "cancelled": "cancelled", "updated": "updated"}[what]
    events.emit(db, f"document.{kind}", aggregate_type="document", aggregate_id=doc.id,
                business_id=doc.business_id, payload=payload)
    if doc.doc_type == DocType.SALES_INVOICE:
        events.emit(db, f"invoice.{what}", aggregate_type="document", aggregate_id=doc.id,
                    business_id=doc.business_id, payload=payload)
    elif doc.doc_type == DocType.PURCHASE_INVOICE and what in ("created", "updated"):
        events.emit(db, f"purchase.{what}", aggregate_type="document", aggregate_id=doc.id,
                    business_id=doc.business_id, payload=payload)


# ---------------------------------------------------------------------------- cancel


def cancel(db: Session, access: BusinessAccess, doc: Document, *, reason: str, on: date) -> Document:
    spec = spec_of(doc)
    require_for(access, spec, "cancel")
    if doc.status != DocStatus.FINAL:
        raise Conflict("Only finalized documents can be cancelled; delete drafts instead")
    if doc.irn:
        raise Conflict("This invoice has an IRN. Cancel the e-invoice on the IRP first, then "
                       "issue a credit note.")
    if doc.amount_paid != 0:
        raise Conflict("Cancel the payments recorded against this document first")
    dependants = db.scalar(select(Document.number).where(
        Document.reference_document_id == doc.id, Document.status == DocStatus.FINAL).limit(1))
    if dependants:
        raise Conflict(f"Cancel {dependants}, which is based on this document, first")
    _open_fy(db, access.business.id, on)
    entries = list(db.scalars(select(StockLedgerEntry).where(
        StockLedgerEntry.source_type == SOURCE, StockLedgerEntry.source_id == doc.id,
        StockLedgerEntry.reverses_entry_id.is_(None)).order_by(StockLedgerEntry.posted_at)))
    if entries:
        moves = [Movement(db.get(Product, e.product_id), e.warehouse_id, reverses=e,  # type: ignore[arg-type]
                          line_id=e.source_line_id) for e in reversed(entries)]
        StockEngine(db, access.business).post(moves, entry_date=on, source_type=SOURCE,
                                              source_id=doc.id)
    from app.modules.accounting.models import JournalSource
    from app.modules.accounting.posting import reverse as reverse_posting

    reverse_posting(db, access.business, JournalSource.DOCUMENT, doc.id, on=on, user_id=access.user.id)
    doc.status = DocStatus.CANCELLED
    doc.cancelled_at = datetime.now(UTC)
    doc.cancelled_by_id = access.user.id
    doc.cancel_reason = reason
    doc.balance_due = ZERO
    if spec.payable:
        doc.payment_status = PaymentStatus.NOT_APPLICABLE
    audit.record(db, action="document.cancelled", entity_type="document", entity_id=doc.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"number": doc.number, "reason": reason})
    _emit(db, doc, "cancelled")
    return doc


def record_irn(db: Session, access: BusinessAccess, doc: Document, *, irn: str, ack_no: str,
               ack_date: datetime, signed_qr: str | None) -> Document:
    """Store IRN details generated by the Invoice Registration Portal (manually or via a GSP
    connector). The ERP never fabricates an IRN."""
    require_for(access, spec_of(doc), "create")
    if doc.status != DocStatus.FINAL or doc.doc_type not in (
            DocType.SALES_INVOICE, DocType.CREDIT_NOTE, DocType.DEBIT_NOTE):
        raise ValidationFailed("IRNs apply to finalized invoices, credit notes and debit notes")
    if doc.irn:
        raise Conflict("An IRN is already recorded for this document")
    doc.irn, doc.irn_ack_no, doc.irn_ack_date, doc.irn_signed_qr = irn, ack_no, ack_date, signed_qr
    audit.record(db, action="document.irn_recorded", entity_type="document", entity_id=doc.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"irn": irn, "ack_no": ack_no})
    _emit(db, doc, "updated")
    return doc

