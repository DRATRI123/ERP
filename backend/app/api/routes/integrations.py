"""Integration center, sending documents, payment links, sync, data import/export."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, Access, require
from app.api.pagination import Page, PageParams, page_params, paginate
from app.core.errors import PermissionDenied, ValidationFailed
from app.modules.access.service import BusinessAccess
from app.modules.billing import service as billing
from app.modules.integrations import data_io, outbox, sync
from app.modules.integrations import service as svc
from app.modules.integrations.base import REGISTRY, CallLog
from app.modules.integrations.models import (
    Integration,
    IntegrationLog,
    MessageChannel,
    OutboundMessage,
    PaymentLink,
)
from app.modules.integrations.providers import commerce, messaging
from app.modules.parties.models import PartyRole

router = APIRouter(prefix="/businesses/{business_id}", tags=["integrations"])
Manage = Annotated[BusinessAccess, Depends(require("integrations.manage"))]


class ConnectIn(BaseModel):
    provider: str
    name: str = Field(min_length=1, max_length=80)
    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)


class SendIn(BaseModel):
    channel: MessageChannel
    recipient: str | None = Field(None, max_length=254)
    integration_id: uuid.UUID | None = None
    message: str | None = Field(None, max_length=2000)
    include_payment_link: bool = False


def _out(i: Integration) -> dict[str, Any]:
    sp = REGISTRY[i.provider]
    return {"id": i.id, "provider": i.provider, "label": sp.label, "name": i.name, "status": i.status.value,
            "config": i.config, "has_credentials": bool(i.credentials_encrypted),
            "credentials_rotated_at": i.credentials_rotated_at, "last_tested_at": i.last_tested_at,
            "last_synced_at": i.last_synced_at, "last_error": i.last_error,
            "webhook_url": svc.webhook_url(i), "capabilities": list(sp.capabilities)}


@router.get("/integrations/providers")
def providers(_: Manage) -> list[dict[str, Any]]:
    """Available providers and the fields each needs (secret fields are marked)."""
    return [{"code": s.code, "label": s.label, "category": s.category, "capabilities": list(s.capabilities),
             "webhook": s.webhook, "oauth": s.oauth, "docs": s.docs,
             "fields": [{"name": f.name, "label": f.label, "secret": f.secret, "required": f.required, "help": f.help}
                        for f in s.fields]} for s in REGISTRY.values()]


@router.get("/integrations")
def list_integrations(access: Manage, db: DB) -> list[dict[str, Any]]:
    rows = db.scalars(select(Integration).where(Integration.business_id == access.business.id)
                      .order_by(Integration.name))
    return [_out(i) for i in rows]


@router.post("/integrations", status_code=status.HTTP_201_CREATED)
def connect(body: ConnectIn, access: Manage, db: DB) -> dict[str, Any]:
    i = svc.connect(db, access, provider=body.provider, name=body.name, config=body.config, creds=body.credentials)
    db.commit()
    out = _out(i)
    if i.provider == "google":
        out["oauth_redirect_uri"] = svc.oauth_redirect_uri()
        out["consent_url"] = svc.google_consent_url(db, access, i)
    return out


@router.patch("/integrations/{integration_id}")
def configure(integration_id: uuid.UUID, body: dict[str, Any], access: Manage, db: DB) -> dict[str, Any]:
    i = svc.update_config(db, access, svc.get_integration(db, access, integration_id), body)
    db.commit()
    return _out(i)


@router.post("/integrations/{integration_id}/credentials")
def rotate(integration_id: uuid.UUID, body: dict[str, str], access: Manage, db: DB) -> dict[str, Any]:
    """Replace one or more secrets (key rotation). Secrets are write-only: never returned."""
    i = svc.rotate_credentials(db, access, svc.get_integration(db, access, integration_id), body)
    db.commit()
    return _out(i)


@router.post("/integrations/{integration_id}/test")
def test(integration_id: uuid.UUID, access: Manage, db: DB) -> dict[str, Any]:
    ok, message = svc.test_connection(db, access, svc.get_integration(db, access, integration_id))
    db.commit()
    return {"ok": ok, "message": message}


@router.post("/integrations/{integration_id}/disconnect")
def disconnect(integration_id: uuid.UUID, access: Manage, db: DB) -> dict[str, Any]:
    i = svc.disconnect(db, access, svc.get_integration(db, access, integration_id))
    db.commit()
    return _out(i)


@router.get("/integrations/{integration_id}/consent-url")
def consent_url(integration_id: uuid.UUID, access: Manage, db: DB) -> dict[str, str]:
    i = svc.get_integration(db, access, integration_id)
    return {"consent_url": svc.google_consent_url(db, access, i), "redirect_uri": svc.oauth_redirect_uri()}


@router.get("/integrations/{integration_id}/logs")
def logs(integration_id: uuid.UUID, access: Manage, db: DB,
         params: Annotated[PageParams, Depends(page_params)], failed_only: bool = False) -> Page[dict]:
    i = svc.get_integration(db, access, integration_id)
    stmt = select(IntegrationLog).where(IntegrationLog.integration_id == i.id).order_by(IntegrationLog.created_at.desc())
    if failed_only:
        stmt = stmt.where(IntegrationLog.success.is_(False))
    rows, total = paginate(db, stmt, params)
    return Page(items=[{"id": r.id, "direction": r.direction, "operation": r.operation, "success": r.success,
                        "http_status": r.http_status, "detail": r.detail, "duration_ms": r.duration_ms,
                        "created_at": r.created_at} for r in rows], total=total, limit=params.limit,
                offset=params.offset)


@router.post("/integrations/{integration_id}/test-sms")
def test_sms(integration_id: uuid.UUID, access: Manage, db: DB, to: str = Query(min_length=10, max_length=15)) -> dict:
    """Sends one real SMS through the invoice flow to verify MSG91 end to end."""
    i = svc.get_integration(db, access, integration_id)
    if i.provider != "msg91":
        raise ValidationFailed("Only for MSG91 integrations")
    with CallLog(db, i, "test_sms") as log:
        ref = messaging.msg91_send(i.config, svc.credentials(i), to=to, flow_id=i.config["invoice_flow_id"],
                                   variables={"name": "Test", "number": "TEST-0001", "amount": "1.00", "link": ""})
        log.detail = f"request {ref}"
    db.commit()
    return {"ok": True, "request_id": ref}


@router.post("/integrations/{integration_id}/shopify/import-orders")
def shopify_orders(integration_id: uuid.UUID, access: Manage, db: DB) -> dict:
    i = svc.get_integration(db, access, integration_id)
    if i.provider != "shopify":
        raise ValidationFailed("Not a Shopify integration")
    result = sync.import_orders(db, access, i)
    db.commit()
    return result


@router.post("/integrations/{integration_id}/shopify/import-products")
def shopify_products(integration_id: uuid.UUID, access: Manage, db: DB) -> dict:
    i = svc.get_integration(db, access, integration_id)
    if i.provider != "shopify":
        raise ValidationFailed("Not a Shopify integration")
    result = sync.import_products(db, access, i)
    db.commit()
    return result


@router.post("/integrations/{integration_id}/drive-backup")
def drive_backup(integration_id: uuid.UUID, access: Manage, db: DB) -> dict:
    """Owner only: take an encrypted, verified database backup and upload it to Google Drive.
    (Scheduled off-site copies are configured under /system/backups/settings.)"""
    if not access.is_owner:
        raise PermissionDenied("Only the owner can export database backups")
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from app.modules.system.backups import backup_dir, create_backup

    i = svc.get_integration(db, access, integration_id)
    if "drive_backup" not in REGISTRY[i.provider].capabilities:
        raise ValidationFailed("This integration cannot store backups")
    with TemporaryDirectory(dir=backup_dir(), prefix=".drive-") as tmp, CallLog(db, i, "drive_backup") as log:
        path, _ = create_backup(db.get_bind(), dest_dir=Path(tmp))
        file_id = commerce.drive_upload_file(i.config, svc.credentials(i), path=path)
        log.detail = f"{path.name} → {file_id}"
    db.commit()
    return {"ok": True, "file_id": file_id, "filename": path.name, "encrypted": True}


# ---------------------------------------------------------------------------- documents


@router.post("/documents/{document_id}/send", status_code=status.HTTP_202_ACCEPTED, tags=["billing"])
def send_document(document_id: uuid.UUID, body: SendIn, access: Access, db: DB) -> dict:
    """Queue the document (as PDF) for e-mail / WhatsApp / SMS. Delivery happens in the worker."""
    doc = billing.get_document(db, access, document_id)
    link = sync.create_payment_link(db, access, doc, None).url if body.include_payment_link else None
    msg = outbox.queue_document(db, access, doc, channel=body.channel, recipient=body.recipient,
                                integration_id=body.integration_id, message=body.message, payment_link=link)
    db.commit()
    return outbox.message_summary(msg) | {"payment_link": link}


@router.post("/documents/{document_id}/payment-link", tags=["billing"])
def payment_link(document_id: uuid.UUID, access: Access, db: DB, integration_id: uuid.UUID | None = None) -> dict:
    link = sync.create_payment_link(db, access, billing.get_document(db, access, document_id), integration_id)
    db.commit()
    return {"id": link.id, "url": link.url, "amount": link.amount, "status": link.status.value,
            "provider_link_id": link.provider_link_id}


@router.get("/documents/{document_id}/payment-links", tags=["billing"])
def payment_links(document_id: uuid.UUID, access: Access, db: DB) -> list[dict]:
    doc = billing.get_document(db, access, document_id)
    return [{"id": x.id, "url": x.url, "amount": x.amount, "amount_paid": x.amount_paid, "status": x.status.value,
             "created_at": x.created_at}
            for x in db.scalars(select(PaymentLink).where(PaymentLink.document_id == doc.id)
                                .order_by(PaymentLink.created_at))]


@router.get("/messages")
def messages(access: Access, db: DB, params: Annotated[PageParams, Depends(page_params)],
             document_id: uuid.UUID | None = None, failed_only: bool = False) -> Page[dict]:
    access.require("invoices.view")
    stmt = select(OutboundMessage).where(OutboundMessage.business_id == access.business.id).order_by(
        OutboundMessage.created_at.desc())
    if document_id:
        stmt = stmt.where(OutboundMessage.document_id == document_id)
    if failed_only:
        stmt = stmt.where(OutboundMessage.status == "failed")
    rows, total = paginate(db, stmt, params)
    return Page(items=[outbox.message_summary(m) for m in rows], total=total, limit=params.limit, offset=params.offset)


@router.post("/messages/{message_id}/retry")
def retry_message(message_id: uuid.UUID, access: Access, db: DB) -> dict:
    access.require("invoices.create")
    m = db.get(OutboundMessage, message_id)
    if m is None or m.business_id != access.business.id:
        raise ValidationFailed("Message not found")
    outbox.retry(db, access, m)
    db.commit()
    return outbox.message_summary(m)


# ---------------------------------------------------------------------------- data import / export


@router.post("/data/import/{kind}", tags=["data"])
async def import_data(kind: str, access: Access, db: DB, file: UploadFile = File(...),
                      dry_run: bool = False) -> dict:
    """CSV or Excel. Nothing is written unless every row is valid; ``dry_run`` only validates."""
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise ValidationFailed("Files up to 10 MB")
    rows = data_io.read_rows(file.filename or "upload.csv", data)
    if kind == "products":
        result = data_io.import_products(db, access, rows, dry_run=dry_run)
    elif kind in ("customers", "suppliers"):
        result = data_io.import_parties(db, access, PartyRole.CUSTOMER if kind == "customers" else PartyRole.SUPPLIER,
                                        rows, dry_run=dry_run)
    else:
        raise ValidationFailed("kind must be products, customers or suppliers")
    db.commit()
    return result


@router.get("/data/export/{kind}", tags=["data"])
def export_data(kind: str, access: Access, db: DB, format: str = Query("xlsx", pattern="^(xlsx|csv)$"),
                date_from: date | None = None, date_to: date | None = None) -> Response:
    access.require("data.export")
    from app.modules.catalog.models import Product
    from app.modules.gst.reports import register
    from app.modules.parties.models import Party

    if kind == "products":
        access.require("products.view")
        rows = [{"name": p.name, "sku": p.sku, "barcode": p.barcode, "hsn": p.hsn_sac, "gst_rate": p.gst_rate,
                 "selling_price": p.selling_price, "mrp": p.mrp, "active": p.is_active}
                for p in db.scalars(select(Product).where(Product.business_id == access.business.id).order_by(Product.name))]
    elif kind in ("customers", "suppliers"):
        access.require(f"{kind}.view")
        flag = Party.is_customer if kind == "customers" else Party.is_supplier
        rows = [{"name": p.name, "gstin": p.gstin, "state_code": p.state_code, "phone": p.phone, "email": p.email,
                 "credit_limit": p.credit_limit, "credit_days": p.credit_days}
                for p in db.scalars(select(Party).where(Party.business_id == access.business.id, flag.is_(True))
                                    .order_by(Party.name))]
    elif kind in ("sales-register", "purchase-register"):
        access.require("gst.view")
        if not (date_from and date_to):
            raise ValidationFailed("date_from and date_to are required")
        rows = register(db, access.business.id, kind.split("-")[0], date_from, date_to, None)["rows"]
    else:
        raise ValidationFailed("Unknown export")
    content, mime = data_io.to_file(rows, format, sheet=kind)
    return Response(content, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{kind}.{format}"'})


@router.get("/data/export-tally", tags=["data"])
def export_tally(access: Annotated[BusinessAccess, Depends(require("accounting.view", "data.export"))], db: DB,
                 date_from: date, date_to: date) -> Response:
    xml = data_io.tally_xml(db, access.business.id, date_from=date_from, date_to=date_to,
                            company=access.business.legal_name or access.business.name)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return Response(xml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="tally-{stamp}.xml"'})
