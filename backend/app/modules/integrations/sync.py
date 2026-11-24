"""Payment links (Razorpay/Cashfree) and Shopify import, plus webhook handling."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ValidationFailed
from app.modules.access.service import BusinessAccess, get_business_access
from app.modules.billing import payments as pay_service
from app.modules.billing import service as billing
from app.modules.billing.doctypes import DocType
from app.modules.billing.models import DocStatus, Document, PaymentDirection, PaymentMode
from app.modules.catalog.models import Product, ProductKind, Unit
from app.modules.integrations.base import CallLog, IntegrationError
from app.modules.integrations.models import (
    GatewayPayment,
    Integration,
    IntegrationLog,
    IntegrationStatus,
    PaymentLink,
    PaymentLinkStatus,
    WebhookReceipt,
)
from app.modules.integrations.providers import commerce, gateways
from app.modules.integrations.service import active_for, credentials
from app.modules.parties.models import Party, PartyGstType
from app.modules.users.service import get_owner


def system_access(db: Session, business_id: uuid.UUID) -> BusinessAccess:
    """Automatic actions from verified webhooks act as the owner (and are audited as such)."""
    owner = get_owner(db)
    assert owner is not None
    return get_business_access(db, owner, business_id)


def _log_inbound(db: Session, i: Integration, operation: str, ok: bool, detail: str) -> None:
    db.add(IntegrationLog(business_id=i.business_id, integration_id=i.id, direction="inbound",
                          operation=operation, success=ok, detail=detail[:1000]))


def _first_time(db: Session, i: Integration, event_id: str) -> bool:
    try:
        with db.begin_nested():
            db.add(WebhookReceipt(integration_id=i.id, event_id=event_id[:200]))
        return True
    except IntegrityError:
        return False


# ---------------------------------------------------------------------------- payment links


def create_payment_link(db: Session, access: BusinessAccess, doc: Document,
                        integration_id: uuid.UUID | None) -> PaymentLink:
    access.require("payments.create")
    if doc.doc_type not in (DocType.SALES_INVOICE, DocType.DEBIT_NOTE) or doc.status != DocStatus.FINAL:
        raise ValidationFailed("Payment links are for finalized sales invoices and debit notes")
    if doc.balance_due <= 0:
        raise ValidationFailed("Nothing is due on this invoice")
    existing = db.scalar(select(PaymentLink).where(PaymentLink.document_id == doc.id,
                                                   PaymentLink.status == PaymentLinkStatus.CREATED,
                                                   PaymentLink.amount == doc.balance_due))
    if existing:
        return existing  # same amount still open: reuse instead of sending two links
    i = active_for(db, access.business.id, "payment_links", integration_id)
    creds = credentials(i)
    snap = doc.party_snapshot or {}
    customer = {"name": snap.get("name") or doc.party_name or "", "email": snap.get("email") or "",
                "contact": snap.get("phone") or ""}
    n = db.scalar(select(PaymentLink.id).where(PaymentLink.document_id == doc.id).limit(1))
    reference = f"{doc.number}" + ("" if n is None else f"-{datetime.now(UTC):%H%M%S}")
    with CallLog(db, i, "create_payment_link") as log:
        if i.provider == "razorpay":
            r = gateways.razorpay_create_link(creds, amount=doc.balance_due, reference=reference,
                                              description=f"Invoice {doc.number}", customer=customer)
        else:
            r = gateways.cashfree_create_link(i.config, creds, amount=doc.balance_due, reference=reference,
                                              description=f"Invoice {doc.number}", customer=customer)
        log.detail = f"{doc.number}: {r.link_id}"
    link = PaymentLink(business_id=access.business.id, integration_id=i.id, document_id=doc.id,
                       provider_link_id=r.link_id, reference=reference, amount=doc.balance_due, url=r.url,
                       status=PaymentLinkStatus.CREATED)
    db.add(link)
    db.flush()
    return link


def handle_gateway_webhook(db: Session, i: Integration, body: bytes, headers: dict[str, str]) -> str:
    creds = credentials(i)
    event = (gateways.razorpay_parse_webhook(creds, body, headers) if i.provider == "razorpay"
             else gateways.cashfree_parse_webhook(creds, body, headers))
    if event is None:
        _log_inbound(db, i, "webhook", True, "ignored event")
        return "ignored"
    if not _first_time(db, i, event.event_id):
        return "duplicate"
    if db.scalar(select(GatewayPayment.id).where(GatewayPayment.integration_id == i.id,
                                                 GatewayPayment.provider_payment_id == event.payment_id)):
        return "duplicate"
    link = db.scalar(select(PaymentLink).where(PaymentLink.integration_id == i.id,
                                               PaymentLink.provider_link_id == event.link_id))
    gp = GatewayPayment(business_id=i.business_id, integration_id=i.id, link_id=link.id if link else None,
                        provider_payment_id=event.payment_id, amount=event.amount, method=event.method)
    db.add(gp)
    if link is None:  # paid, but not a link we created: keep the record, flag it for review
        _log_inbound(db, i, "payment_received", False,
                     f"Payment {event.payment_id} for unknown link {event.link_id}: recorded, not applied")
        return "unmatched"
    doc = db.get(Document, link.document_id)
    access = system_access(db, i.business_id)
    alloc = min(event.amount, doc.balance_due) if doc.status == DocStatus.FINAL else Decimal(0)
    pay = pay_service.create_payment(
        db, access, direction=PaymentDirection.IN, party_id=doc.party_id, payment_date=date.today(),
        mode=PaymentMode.UPI if event.method == "upi" else PaymentMode.OTHER, amount=event.amount,
        reference=f"{i.provider}:{event.payment_id}", notes=f"Online payment via {i.name}",
        allocations=[{"document_id": doc.id, "amount": alloc}] if alloc > 0 else [],
        branch_id=doc.branch_id, account_id=uuid.UUID(str(i.config["settlement_account_id"])))
    gp.erp_payment_id = pay.id
    link.amount_paid += event.amount
    link.status = PaymentLinkStatus.PAID if link.amount_paid >= link.amount else PaymentLinkStatus.PARTIALLY_PAID
    _log_inbound(db, i, "payment_received", True, f"{event.payment_id} → receipt {pay.number}")
    return "applied"


# ---------------------------------------------------------------------------- Shopify


def _sku_map(db: Session, business_id: uuid.UUID) -> dict[str, Product]:
    return {p.sku: p for p in db.scalars(select(Product).where(Product.business_id == business_id))}


def import_products(db: Session, access: BusinessAccess, i: Integration) -> dict[str, int]:
    """Create ERP products for Shopify variants whose SKU is new; existing SKUs are left as they
    are (the ERP is the master for prices, HSN and GST)."""
    access.require("products.manage")
    from app.modules.catalog.service import create_product

    with CallLog(db, i, "import_products") as log:
        products = commerce.shopify_products(i.config, credentials(i))
        known = _sku_map(db, access.business.id)
        unit = db.scalar(select(Unit).where(Unit.business_id == access.business.id, Unit.code == "NOS"))
        created = skipped = 0
        for prod in products:
            for v in prod.get("variants", []):
                sku = (v.get("sku") or "").strip()
                if not sku or sku in known:
                    skipped += 1
                    continue
                title = prod["title"] + ("" if v.get("title") in (None, "Default Title") else f" - {v['title']}")
                known[sku] = create_product(db, access, {
                    "name": title[:200], "sku": sku, "barcode": (v.get("barcode") or None) and v["barcode"][:64],
                    "unit_id": unit.id, "selling_price": Decimal(str(v.get("price") or "0")).quantize(Decimal("0.0001")),
                    "prices_include_tax": True, "kind": ProductKind.GOODS,
                    "attributes": {"shopify_variant_id": v.get("id")}})
                created += 1
        log.detail = f"{created} created, {skipped} skipped"
    # last_synced_at is the *order* cursor; product imports must not move it.
    return {"created": created, "skipped": skipped}


def _customer(db: Session, access: BusinessAccess, order: dict[str, Any]) -> Party | None:
    c = order.get("customer") or {}
    email = (c.get("email") or order.get("email") or "").strip().lower() or None
    phone = (c.get("phone") or order.get("phone") or "").strip() or None
    if not (email or phone):
        return None
    q = select(Party).where(Party.business_id == access.business.id, Party.is_customer.is_(True))
    found = db.scalar(q.where(Party.email == email)) if email else None
    found = found or (db.scalar(q.where(Party.phone == phone)) if phone else None)
    if found:
        return found
    from app.modules.parties.models import PartyRole
    from app.modules.parties.service import create_party

    name = " ".join(filter(None, [c.get("first_name"), c.get("last_name")])) or email or phone
    ship = order.get("shipping_address") or {}
    addresses = [{"kind": "shipping", "address_line1": (ship.get("address1") or "-")[:200],
                  "city": ship.get("city"), "pincode": (ship.get("zip") or "")[:6] or None, "country": "IN"}] \
        if ship.get("address1") else []
    return create_party(db, access, PartyRole.CUSTOMER, {"name": name[:200], "email": email, "phone": phone,
                                                         "gst_type": PartyGstType.CONSUMER}, addresses)


def import_order(db: Session, i: Integration, order: dict[str, Any], access: BusinessAccess) -> str:
    ref = f"shopify:{order['id']}"
    existing = db.scalar(select(Document).where(Document.business_id == i.business_id, Document.client_ref == ref))
    if existing is not None:
        if (order.get("financial_status") == "paid" and existing.status == DocStatus.FINAL
                and existing.balance_due > 0):  # imported unpaid earlier, paid now
            pay_service.pay_document(db, access, existing, mode=PaymentMode.OTHER, amount=existing.balance_due,
                                     reference=f"shopify:{order.get('name')}",
                                     account_id=uuid.UUID(str(i.config["settlement_account_id"])))
            return "paid"
        return "exists"
    if order.get("cancelled_at"):
        return "cancelled"
    skus = _sku_map(db, i.business_id)
    lines: list[billing.LineIn] = []
    for li in order.get("line_items", []):
        p = skus.get((li.get("sku") or "").strip())
        if p is None:
            raise ValidationFailed(f"Order {order.get('name')}: SKU {li.get('sku')!r} is not in the ERP; "
                                   "import products first")
        qty = Decimal(str(li["quantity"]))
        lines.append(billing.LineIn(product_id=p.id, quantity=qty, unit_price=Decimal(str(li["price"])),
                                    discount_amount=Decimal(str(li.get("total_discount") or 0)),
                                    price_includes_tax=bool(order.get("taxes_included"))))
    shipping = sum((Decimal(str(s.get("price") or 0)) for s in order.get("shipping_lines", [])), Decimal(0))
    if shipping:
        lines.append(billing.LineIn(description="Shipping charges", hsn_sac="996812", quantity=Decimal(1),
                                    unit_price=shipping, price_includes_tax=bool(order.get("taxes_included")),
                                    gst_rate=Decimal(str(i.config.get("shipping_gst_rate") or "18"))))
    party = _customer(db, access, order)
    created = order.get("created_at", "")[:10]
    doc = billing.create_draft(db, access, DocType.SALES_INVOICE, {
        "client_ref": ref, "doc_date": date.fromisoformat(created) if created else date.today(),
        "branch_id": uuid.UUID(str(i.config["branch_id"])), "warehouse_id": uuid.UUID(str(i.config["warehouse_id"])),
        "party_id": party.id if party else None,
        "party_name": None if party else (order.get("billing_address") or {}).get("name") or "Shopify customer",
        "notes": f"Shopify order {order.get('name')}"}, lines)
    paid = order.get("financial_status") == "paid"
    payments = [{"mode": PaymentMode.OTHER, "amount": doc.amount_payable, "reference": f"shopify:{order.get('name')}",
                 "account_id": uuid.UUID(str(i.config["settlement_account_id"]))}] if paid and doc.amount_payable else []
    billing.finalize(db, access, doc, payments=payments)
    return "imported"


def import_orders(db: Session, access: BusinessAccess, i: Integration) -> dict[str, Any]:
    access.require("invoices.create")
    since = i.last_synced_at.isoformat() if i.last_synced_at else None
    started = datetime.now(UTC)
    with CallLog(db, i, "import_orders") as log:
        orders = commerce.shopify_orders(i.config, credentials(i), updated_since=since)
        log.detail = f"{len(orders)} orders fetched"
    result: dict[str, Any] = {"imported": 0, "exists": 0, "paid": 0, "cancelled": 0, "errors": []}
    for order in sorted(orders, key=lambda o: o.get("created_at", "")):
        try:
            with db.begin_nested():  # one bad order never blocks the rest
                result[import_order(db, i, order, access)] += 1
        except AppError as exc:
            result["errors"].append(f"{order.get('name')}: {exc.message}")
    if not result["errors"]:
        i.last_synced_at = started  # only advance the cursor when everything imported
    return result


def handle_shopify_webhook(db: Session, i: Integration, body: bytes, headers: dict[str, str]) -> str:
    if not commerce.shopify_verify(credentials(i), body, headers.get("x-shopify-hmac-sha256")):
        raise IntegrationError("Invalid Shopify webhook signature")
    event_id = (headers.get("x-shopify-webhook-id") or headers.get("x-shopify-event-id")
                or "sha256:" + hashlib.sha256(body).hexdigest())
    if not _first_time(db, i, event_id):
        return "duplicate"
    topic = headers.get("x-shopify-topic", "")
    if topic not in ("orders/create", "orders/paid"):
        return "ignored"
    order = json.loads(body)
    try:
        with db.begin_nested():
            outcome = import_order(db, i, order, system_access(db, i.business_id))
    except AppError as exc:
        _log_inbound(db, i, topic, False, exc.message)
        return "error"
    _log_inbound(db, i, topic, True, f"{order.get('name')}: {outcome}")
    return outcome


def is_active(i: Integration) -> bool:
    return i.status in (IntegrationStatus.CONNECTED, IntegrationStatus.ERROR)
