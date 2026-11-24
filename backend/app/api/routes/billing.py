"""Billing documents, payments, numbering series, tax rates, templates and printing."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select

from app.api.deps import DB, Access, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.api.schemas_billing import (
    CancelIn,
    ConvertIn,
    DocumentIn,
    DocumentOut,
    DocumentSummaryOut,
    DocumentUpdateIn,
    FinalizeIn,
    IrnIn,
    OutstandingOut,
    PaymentCancelIn,
    PaymentIn,
    PaymentOut,
    SeriesIn,
    SeriesOut,
    SeriesUpdateIn,
    TaxRateIn,
    TaxRateOut,
    TaxRateUpdateIn,
    TemplateIn,
    TemplateOut,
    TemplateUpdateIn,
)
from app.core.errors import NotFound, PermissionDenied, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.audit import service as audit
from app.modules.billing import payments as pay_service
from app.modules.billing import series as series_service
from app.modules.billing import service, template_service
from app.modules.billing.doctypes import SPECS, DocType, Side
from app.modules.billing.models import (
    DocStatus,
    Document,
    HsnTaxRate,
    Payment,
    PaymentDirection,
    PaymentStatus,
)
from app.modules.billing.printing import Paper, TemplateConfig, html_to_pdf, render_html
from app.modules.gst import einvoice
from app.modules.parties.models import Party

router = APIRouter(prefix="/businesses/{business_id}", tags=["billing"])

ManageTemplates = Annotated[BusinessAccess, Depends(require("templates.manage"))]
# Never set to NULL from a PATCH: these columns are required.
_REQUIRED = {"doc_date", "branch_id", "tax_mode", "price_tier", "prices_include_tax",
             "reverse_charge", "restock", "landed_cost", "custom_fields"}


def _header(body: Any, exclude: set[str]) -> dict[str, Any]:
    data = body.model_dump(exclude=exclude | {"lines"}, exclude_unset=True)
    return {k: v for k, v in data.items() if not (k in _REQUIRED and v is None)}


def _lines(body: Any) -> list[service.LineIn] | None:
    if body.lines is None:
        return None
    return [service.LineIn(**ln.model_dump()) for ln in body.lines]


def _out(doc: Document) -> DocumentOut:
    return DocumentOut.model_validate(doc)


def _visible_types(access: BusinessAccess) -> list[DocType]:
    return [t for t, s in SPECS.items() if access.can(s.view_permission)]


# ---------------------------------------------------------------------------- documents


@router.get("/documents", response_model=Page[DocumentSummaryOut])
def list_documents(
    access: Access, db: DB, params: Annotated[PageParams, Depends(page_params)],
    doc_type: list[DocType] | None = Query(None), status_filter: DocStatus | None = None,
    party_id: uuid.UUID | None = None, payment_status: PaymentStatus | None = None,
    overdue: bool = False, date_from: date | None = None, date_to: date | None = None,
    branch_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=60, description="Number, party name or client_ref"),
) -> Page[DocumentSummaryOut]:
    types = _visible_types(access)
    if doc_type:
        types = [t for t in doc_type if t in types]
    if not types:
        raise PermissionDenied("You cannot view these documents")
    stmt = (select(Document).where(Document.business_id == access.business.id,
                                   Document.doc_type.in_(types))
            .order_by(Document.doc_date.desc(), Document.created_at.desc()))
    if not access.all_branches:
        stmt = stmt.where(Document.branch_id.in_(access.branch_ids))
    if branch_id:
        stmt = stmt.where(Document.branch_id == branch_id)
    if status_filter:
        stmt = stmt.where(Document.status == status_filter)
    if party_id:
        stmt = stmt.where(Document.party_id == party_id)
    if payment_status:
        stmt = stmt.where(Document.payment_status == payment_status)
    if overdue:
        stmt = stmt.where(Document.status == DocStatus.FINAL, Document.balance_due > 0,
                          Document.due_date < date.today())
    if date_from:
        stmt = stmt.where(Document.doc_date >= date_from)
    if date_to:
        stmt = stmt.where(Document.doc_date <= date_to)
    if q:
        stmt = stmt.where(or_(Document.number == q, Document.client_ref == q,
                              Document.party_name.ilike(f"%{q}%")))
    rows, total = paginate(db, stmt, params)
    return Page(items=[DocumentSummaryOut.model_validate(d) for d in rows], total=total,
                limit=params.limit, offset=params.offset)


@router.post("/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def create_document(body: DocumentIn, access: Access, db: DB) -> DocumentOut:
    doc = service.create_draft(db, access, body.doc_type, _header(body, {"doc_type"}) | {
        "doc_date": body.doc_date}, _lines(body))  # type: ignore[arg-type]
    db.commit()
    return _out(doc)


@router.post("/documents/preview", response_model=DocumentOut)
def preview_document(body: DocumentIn, access: Access, db: DB) -> DocumentOut:
    """Price and tax a document without saving anything (live totals while billing)."""
    with db.begin_nested() as sp:
        doc = service.create_draft(db, access, body.doc_type, _header(body, {"doc_type", "client_ref"})
                                   | {"doc_date": body.doc_date}, _lines(body))  # type: ignore[arg-type]
        out = _out(doc)
        sp.rollback()
    db.rollback()
    return out


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: uuid.UUID, access: Access, db: DB) -> DocumentOut:
    return _out(service.get_document(db, access, document_id))


@router.patch("/documents/{document_id}", response_model=DocumentOut)
def update_document(document_id: uuid.UUID, body: DocumentUpdateIn, access: Access, db: DB) -> DocumentOut:
    doc = service.update_draft(db, access, service.get_document(db, access, document_id),
                               _header(body, set()), _lines(body))
    db.commit()
    return _out(doc)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: uuid.UUID, access: Access, db: DB) -> None:
    service.delete_draft(db, access, service.get_document(db, access, document_id))
    db.commit()


@router.post("/documents/{document_id}/finalize", response_model=DocumentOut)
def finalize_document(document_id: uuid.UUID, access: Access, db: DB,
                      body: FinalizeIn | None = None) -> DocumentOut:
    """Assign the number, freeze party details, post stock and take counter payments."""
    doc = service.finalize(db, access, service.get_document(db, access, document_id),
                           payments=[p.model_dump() for p in (body.payments if body else [])])
    db.commit()
    return _out(doc)


@router.post("/documents/{document_id}/cancel", response_model=DocumentOut)
def cancel_document(document_id: uuid.UUID, body: CancelIn, access: Access, db: DB) -> DocumentOut:
    doc = service.cancel(db, access, service.get_document(db, access, document_id),
                         reason=body.reason, on=body.cancel_date or date.today())
    db.commit()
    return _out(doc)


@router.post("/documents/{document_id}/convert", response_model=DocumentOut,
             status_code=status.HTTP_201_CREATED)
def convert_document(document_id: uuid.UUID, body: ConvertIn, access: Access, db: DB) -> DocumentOut:
    """Quotation → order → invoice, challan → invoice, invoice → credit/debit note, etc."""
    source = service.get_document(db, access, document_id)
    doc = service.convert(db, access, source, body.target,
                          {"doc_date": body.doc_date} if body.doc_date else None)
    db.commit()
    return _out(doc)


@router.get("/documents/{document_id}/print", response_class=HTMLResponse,
            responses={200: {"content": {"text/html": {}, "application/pdf": {}}}})
def print_document(document_id: uuid.UUID, access: Access, db: DB,
                   format: str = Query("html", pattern="^(html|pdf)$"),
                   template_id: uuid.UUID | None = None, paper: Paper | None = None,
                   copies: int | None = Query(None, ge=1, le=5)) -> Response:
    doc = service.get_document(db, access, document_id)
    if template_id:
        cfg = TemplateConfig.model_validate(template_service.get_template(db, access, template_id).config)
    else:
        cfg = template_service.resolve(db, access.business.id, doc.branch_id, doc.doc_type)
    if paper:
        cfg = cfg.model_copy(update={"paper": paper})
    if copies:
        labels = ["Original for Recipient", "Duplicate for Transporter", "Triplicate for Supplier",
                  "Copy 4", "Copy 5"]
        cfg = cfg.model_copy(update={"copy_labels": labels[:copies]})
    html = render_html(doc, cfg, business_name=access.business.name)
    if format == "html":
        return HTMLResponse(html, headers={
            "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'"})
    pdf = html_to_pdf(html)
    audit.record(db, action="document.printed", entity_type="document", entity_id=doc.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={"format": "pdf", "paper": cfg.paper.value})
    db.commit()
    name = (doc.number or "draft").replace("/", "-")
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}.pdf"'})


@router.get("/documents/{document_id}/einvoice")
def einvoice_json(document_id: uuid.UUID, access: Annotated[BusinessAccess, Depends(require("gst.view"))],
                  db: DB) -> dict:
    """IRP e-invoice JSON (schema 1.1) for upload through the e-invoice portal or a GSP."""
    return einvoice.build(service.get_document(db, access, document_id))


@router.post("/documents/{document_id}/irn", response_model=DocumentOut)
def record_irn(document_id: uuid.UUID, body: IrnIn, access: Annotated[BusinessAccess, Depends(require("gst.file"))],
               db: DB) -> DocumentOut:
    """Record the IRN the Invoice Registration Portal issued for this document."""
    doc = service.record_irn(db, access, service.get_document(db, access, document_id),
                             irn=body.irn, ack_no=body.ack_no, ack_date=body.ack_date,
                             signed_qr=body.signed_qr)
    db.commit()
    return _out(doc)


@router.get("/parties/{party_id}/outstanding", response_model=OutstandingOut)
def party_outstanding(party_id: uuid.UUID, access: Access, db: DB,
                      side: Side = Side.SALES) -> OutstandingOut:
    access.require("customer_balances.view" if side == Side.SALES else "supplier_balances.view")
    party = db.get(Party, party_id)
    if party is None or party.business_id != access.business.id:
        raise NotFound("Party not found")
    types = [t for t, s in SPECS.items() if s.side == side and s.payable]
    docs = list(db.scalars(select(Document).where(
        Document.business_id == access.business.id, Document.party_id == party_id,
        Document.status == DocStatus.FINAL, Document.doc_type.in_(types), Document.balance_due > 0)
        .order_by(Document.due_date, Document.doc_date)))
    today = date.today()
    sign = {t: (-1 if t in (DocType.CREDIT_NOTE, DocType.PURCHASE_RETURN) else 1) for t in types}
    overdue = sum((sign[d.doc_type] * d.balance_due for d in docs
                   if d.due_date and d.due_date < today and sign[d.doc_type] > 0), Decimal(0))
    return OutstandingOut(party_id=party_id, side=side.value,
                          outstanding=service.party_outstanding(db, party_id, side),
                          overdue=overdue, documents=[DocumentSummaryOut.model_validate(d) for d in docs])


# ---------------------------------------------------------------------------- payments


def _payment_dirs(access: BusinessAccess) -> list[PaymentDirection]:
    dirs = []
    if access.can("payments.view") and access.can("invoices.view"):
        dirs.append(PaymentDirection.IN)
    if access.can("payments.view") and access.can("purchases.view"):
        dirs.append(PaymentDirection.OUT)
    return dirs


@router.get("/payments", response_model=Page[PaymentOut], tags=["payments"])
def list_payments(access: Access, db: DB, params: Annotated[PageParams, Depends(page_params)],
                  direction: PaymentDirection | None = None, party_id: uuid.UUID | None = None,
                  date_from: date | None = None, date_to: date | None = None) -> Page[PaymentOut]:
    dirs = _payment_dirs(access)
    if direction:
        dirs = [d for d in dirs if d == direction]
    if not dirs:
        raise PermissionDenied("You cannot view these payments")
    stmt = (select(Payment).where(Payment.business_id == access.business.id, Payment.direction.in_(dirs))
            .order_by(Payment.payment_date.desc(), Payment.number.desc()))
    if not access.all_branches:
        stmt = stmt.where(Payment.branch_id.in_(access.branch_ids))
    if party_id:
        stmt = stmt.where(Payment.party_id == party_id)
    if date_from:
        stmt = stmt.where(Payment.payment_date >= date_from)
    if date_to:
        stmt = stmt.where(Payment.payment_date <= date_to)
    rows, total = paginate(db, stmt, params)
    return Page(items=[PaymentOut.model_validate(p) for p in rows], total=total,
                limit=params.limit, offset=params.offset)


@router.post("/payments", response_model=PaymentOut, status_code=status.HTTP_201_CREATED, tags=["payments"])
def create_payment(body: PaymentIn, access: Access, db: DB) -> PaymentOut:
    p = pay_service.create_payment(
        db, access, direction=body.direction, party_id=body.party_id,
        payment_date=body.payment_date, mode=body.mode, amount=body.amount,
        reference=body.reference, notes=body.notes, branch_id=body.branch_id,
        allocations=[a.model_dump() for a in body.allocations])
    db.commit()
    return PaymentOut.model_validate(p)


@router.get("/payments/{payment_id}", response_model=PaymentOut, tags=["payments"])
def get_payment(payment_id: uuid.UUID, access: Access, db: DB) -> PaymentOut:
    return PaymentOut.model_validate(pay_service.get_payment(db, access, payment_id))


@router.post("/payments/{payment_id}/cancel", response_model=PaymentOut, tags=["payments"])
def cancel_payment(payment_id: uuid.UUID, body: PaymentCancelIn, access: Access, db: DB) -> PaymentOut:
    p = pay_service.cancel_payment(db, access, pay_service.get_payment(db, access, payment_id),
                                   reason=body.reason)
    db.commit()
    return PaymentOut.model_validate(p)


# ---------------------------------------------------------------------------- configuration


@router.get("/document-series", response_model=list[SeriesOut], tags=["billing settings"])
def list_series(access: Annotated[BusinessAccess, Depends(require("business.view"))], db: DB) -> list[SeriesOut]:
    return [SeriesOut.model_validate(s) for s in series_service.list_series(db, access)]


@router.post("/document-series", response_model=SeriesOut, status_code=status.HTTP_201_CREATED,
             tags=["billing settings"])
def create_series(body: SeriesIn, access: ManageTemplates, db: DB) -> SeriesOut:
    s = series_service.create_series(db, access, body.model_dump())
    db.commit()
    return SeriesOut.model_validate(s)


@router.patch("/document-series/{series_id}", response_model=SeriesOut, tags=["billing settings"])
def update_series(series_id: uuid.UUID, body: SeriesUpdateIn, access: ManageTemplates, db: DB) -> SeriesOut:
    s = series_service.update_series(db, access, series_service.get_series(db, access, series_id),
                                     body.model_dump(exclude_unset=True, exclude_none=True))
    db.commit()
    return SeriesOut.model_validate(s)


@router.get("/tax-rates", response_model=list[TaxRateOut], tags=["billing settings"])
def list_tax_rates(access: Annotated[BusinessAccess, Depends(require("business.view"))], db: DB) -> list[TaxRateOut]:
    rows = db.scalars(select(HsnTaxRate).where(HsnTaxRate.business_id == access.business.id)
                      .order_by(HsnTaxRate.hsn_prefix, HsnTaxRate.effective_from))
    return [TaxRateOut.model_validate(r) for r in rows]


@router.post("/tax-rates", response_model=TaxRateOut, status_code=status.HTTP_201_CREATED,
             tags=["billing settings"])
def create_tax_rate(body: TaxRateIn, access: Annotated[BusinessAccess, Depends(require("business.settings"))],
                    db: DB) -> TaxRateOut:
    if body.effective_to and body.effective_to < body.effective_from:
        raise ValidationFailed("effective_to must be on or after effective_from")
    overlap = db.scalar(select(HsnTaxRate.id).where(
        HsnTaxRate.business_id == access.business.id, HsnTaxRate.hsn_prefix == body.hsn_prefix,
        or_(HsnTaxRate.effective_to.is_(None), HsnTaxRate.effective_to >= body.effective_from),
        *([HsnTaxRate.effective_from <= body.effective_to] if body.effective_to else [])))
    if overlap:
        raise ValidationFailed("This HSN already has a rate in that period; end the old rate "
                               "first (set its effective_to)")
    r = HsnTaxRate(business_id=access.business.id, **body.model_dump())
    db.add(r)
    db.flush()
    audit.record(db, action="tax_rate.created", entity_type="hsn_tax_rate", entity_id=r.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes=body.model_dump(mode="json"))
    db.commit()
    return TaxRateOut.model_validate(r)


@router.patch("/tax-rates/{rate_id}", response_model=TaxRateOut, tags=["billing settings"])
def update_tax_rate(rate_id: uuid.UUID, body: TaxRateUpdateIn,
                    access: Annotated[BusinessAccess, Depends(require("business.settings"))], db: DB) -> TaxRateOut:
    """Only the end date and description can change: rates already used stay as they were."""
    r = db.get(HsnTaxRate, rate_id)
    if r is None or r.business_id != access.business.id:
        raise NotFound("Tax rate not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("effective_to") and data["effective_to"] < r.effective_from:
        raise ValidationFailed("effective_to must be on or after effective_from")
    for k, v in data.items():
        setattr(r, k, v)
    audit.record(db, action="tax_rate.updated", entity_type="hsn_tax_rate", entity_id=r.id,
                 business_id=access.business.id, actor_id=access.user.id,
                 changes={k: str(v) for k, v in data.items()})
    db.commit()
    return TaxRateOut.model_validate(r)


@router.get("/invoice-templates", response_model=list[TemplateOut], tags=["billing settings"])
def list_templates(access: Annotated[BusinessAccess, Depends(require("business.view"))], db: DB) -> list[TemplateOut]:
    return [TemplateOut.model_validate(t) for t in template_service.list_templates(db, access)]


@router.post("/invoice-templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED,
             tags=["billing settings"])
def create_template(body: TemplateIn, access: ManageTemplates, db: DB) -> TemplateOut:
    t = template_service.save_template(db, access, None, body.model_dump(mode="json"))
    db.commit()
    return TemplateOut.model_validate(t)


@router.patch("/invoice-templates/{template_id}", response_model=TemplateOut, tags=["billing settings"])
def update_template(template_id: uuid.UUID, body: TemplateUpdateIn, access: ManageTemplates,
                    db: DB) -> TemplateOut:
    t = template_service.get_template(db, access, template_id)
    t = template_service.save_template(db, access, t, body.model_dump(mode="json", exclude_unset=True,
                                                                       exclude_none=True))
    db.commit()
    return TemplateOut.model_validate(t)
