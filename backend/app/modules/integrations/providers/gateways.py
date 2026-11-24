"""Payment gateways: Razorpay and Cashfree payment links with verified webhooks.

An ERP receipt is created only from a webhook whose signature verifies (or from the gateway's
own API) — never from a browser redirect, which a customer could forge.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.modules.integrations.base import (
    Field,
    IntegrationError,
    ProviderSpec,
    check,
    hmac_b64,
    hmac_hex,
    http_client,
    register,
    same,
)

REPLAY_WINDOW_SECONDS = 300


@dataclass(frozen=True)
class LinkResult:
    link_id: str
    url: str
    status: str


@dataclass(frozen=True)
class PaidEvent:
    event_id: str
    link_id: str
    payment_id: str
    amount: Decimal  # rupees
    method: str | None


# ---------------------------------------------------------------------------- Razorpay

RAZORPAY = register(ProviderSpec(
    code="razorpay", label="Razorpay", category="payments",
    fields=(Field("settlement_account_id", "ERP bank account receiving settlements"),
            Field("key_id", "Key ID", secret=True), Field("key_secret", "Key secret", secret=True),
            Field("webhook_secret", "Webhook secret", secret=True,
                  help="Dashboard → Webhooks: event payment_link.paid, URL shown after connecting")),
    capabilities=("payment_links",), webhook=True,
    docs="Use test-mode keys first. Settlement account: the bank account in your chart of accounts.",
))
RZP = "https://api.razorpay.com/v1"


def _rzp(creds: dict[str, Any]):  # type: ignore[no-untyped-def]
    return http_client(RZP, auth=(creds["key_id"], creds["key_secret"]))


def razorpay_test(config: dict[str, Any], creds: dict[str, Any]) -> str:
    with _rzp(creds) as c:
        check(c.get("/payment_links", params={"count": 1}), what="Razorpay")
    return "Razorpay keys accepted"


def razorpay_create_link(creds: dict[str, Any], *, amount: Decimal, reference: str, description: str,
                         customer: dict[str, str]) -> LinkResult:
    body = {"amount": int((amount * 100).to_integral_value()), "currency": "INR",
            "accept_partial": False, "reference_id": reference[:40], "description": description[:2048],
            "customer": {k: v for k, v in customer.items() if v},
            "notify": {"sms": bool(customer.get("contact")), "email": bool(customer.get("email"))},
            "reminder_enable": True}
    with _rzp(creds) as c:
        data = check(c.post("/payment_links", json=body), what="Razorpay payment link")
    return LinkResult(data["id"], data["short_url"], data.get("status", "created"))


def razorpay_parse_webhook(creds: dict[str, Any], body: bytes, headers: dict[str, str]) -> PaidEvent | None:
    if not same(headers.get("x-razorpay-signature"), hmac_hex(creds["webhook_secret"], body)):
        raise IntegrationError("Invalid Razorpay webhook signature")
    data = json.loads(body, parse_float=Decimal)
    if data.get("event") != "payment_link.paid":
        return None
    payment = data["payload"]["payment"]["entity"]
    if payment.get("status") != "captured":
        return None
    return PaidEvent(event_id=headers.get("x-razorpay-event-id") or payment["id"],
                     link_id=data["payload"]["payment_link"]["entity"]["id"], payment_id=payment["id"],
                     amount=Decimal(payment["amount"]) / 100, method=payment.get("method"))


# ---------------------------------------------------------------------------- Cashfree

CASHFREE = register(ProviderSpec(
    code="cashfree", label="Cashfree Payments", category="payments",
    fields=(Field("settlement_account_id", "ERP bank account receiving settlements"),
            Field("environment", "Environment", help="sandbox | production"),
            Field("client_id", "App ID (x-client-id)", secret=True),
            Field("client_secret", "Secret key", secret=True)),
    capabilities=("payment_links",), webhook=True,
    docs="Webhook signatures use your secret key; configure the payment-link webhook URL shown after connecting.",
))
CF_VERSION = "2023-08-01"


def _cf(config: dict[str, Any], creds: dict[str, Any]):  # type: ignore[no-untyped-def]
    base = "https://api.cashfree.com/pg" if config.get("environment") == "production" \
        else "https://sandbox.cashfree.com/pg"
    return http_client(base, headers={"x-client-id": creds["client_id"], "x-client-secret": creds["client_secret"],
                                      "x-api-version": CF_VERSION})


def cashfree_test(config: dict[str, Any], creds: dict[str, Any]) -> str:
    with _cf(config, creds) as c:
        resp = c.get("/links/erp-connection-test")
    if resp.status_code == 404:  # authenticated; the probe link simply does not exist
        return f"Cashfree {config.get('environment', 'sandbox')} keys accepted"
    check(resp, what="Cashfree")
    return "Cashfree keys accepted"


def cashfree_create_link(config: dict[str, Any], creds: dict[str, Any], *, amount: Decimal, reference: str,
                         description: str, customer: dict[str, str]) -> LinkResult:
    link_id = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in reference)[:50]
    body = {"link_id": link_id, "link_amount": float(amount), "link_currency": "INR",
            "link_purpose": description[:500],
            "customer_details": {"customer_phone": customer.get("contact") or "",
                                 "customer_email": customer.get("email") or "",
                                 "customer_name": customer.get("name") or ""},
            "link_notify": {"send_sms": bool(customer.get("contact")), "send_email": bool(customer.get("email"))}}
    with _cf(config, creds) as c:
        data = check(c.post("/links", json=body), what="Cashfree payment link")
    return LinkResult(data.get("link_id", link_id), data["link_url"], str(data.get("link_status", "ACTIVE")).lower())


def cashfree_parse_webhook(creds: dict[str, Any], body: bytes, headers: dict[str, str],
                           now: float | None = None) -> PaidEvent | None:
    ts = headers.get("x-webhook-timestamp", "")
    expected = hmac_b64(creds["client_secret"], ts.encode() + body)
    if not same(headers.get("x-webhook-signature"), expected):
        raise IntegrationError("Invalid Cashfree webhook signature")
    try:
        age = (now or time.time()) - int(ts) / (1000 if len(ts) > 11 else 1)
    except ValueError as exc:
        raise IntegrationError("Invalid Cashfree webhook timestamp") from exc
    if abs(age) > REPLAY_WINDOW_SECONDS:
        raise IntegrationError("Stale Cashfree webhook (possible replay)")
    data = json.loads(body, parse_float=Decimal)
    d = data.get("data", {})
    if data.get("type") != "PAYMENT_LINK_EVENT" or d.get("link_status") not in ("PAID", "PARTIALLY_PAID"):
        return None
    order = d.get("order") or {}
    if order.get("transaction_status") not in (None, "SUCCESS"):
        return None
    payment_id = str(order.get("transaction_id") or order.get("order_id") or "")
    if not payment_id:
        return None
    return PaidEvent(event_id=f"{d.get('link_id')}:{payment_id}", link_id=d["link_id"], payment_id=payment_id,
                     amount=Decimal(str(order.get("order_amount") or d.get("link_amount_paid"))), method=None)
