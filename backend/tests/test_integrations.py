"""Integrations against simulated providers.

``FakeProviders`` answers HTTP calls the way each provider's documented API does and records
every request, so tests assert both our request shapes and our handling of responses. Webhooks
are signed exactly as the providers sign them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.modules.integrations import base as ibase
from app.modules.integrations.providers import messaging as msg_provider
from tests.conftest import TODAY, VALID_GSTIN, Shop
from tests.test_accounting import acct, bal, books_ok
from tests.test_billing import customer, doc, finalize, invoice, stocked

D = Decimal


class FakeProviders:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], Any] = {}

    def on(self, method: str, url_prefix: str, handler: Any) -> None:
        self.routes[(method, url_prefix)] = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for (method, prefix), handler in sorted(self.routes.items(), key=lambda kv: -len(kv[0][1])):
            if request.method == method and url.startswith(prefix):
                out = handler(request)
                return out if isinstance(out, httpx.Response) else httpx.Response(200, json=out)
        return httpx.Response(404, json={"error": f"no fake for {request.method} {url}"})

    def last(self, prefix: str) -> httpx.Request:
        return [r for r in self.requests if str(r.url).startswith(prefix)][-1]


@pytest.fixture
def fake():
    f = FakeProviders()
    ibase.set_transport(httpx.MockTransport(f))
    yield f
    ibase.set_transport(None)


@pytest.fixture
def ishop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers, gstin=VALID_GSTIN, state_code="27")


def connect(s: Shop, provider: str, config: dict, creds: dict, name: str | None = None, expect: int = 201) -> dict:
    return s.post("/integrations", {"provider": provider, "name": name or provider, "config": config,
                                    "credentials": creds}, expect=expect)


def hook(s: Shop, provider: str, iid: str, body: bytes, headers: dict) -> httpx.Response:
    return s.api.c.post(f"/api/v1/hooks/{provider}/{iid}", content=body,
                        headers={"content-type": "application/json", **headers})


# ---------------------------------------------------------------------------- framework


def test_credentials_are_encrypted_write_only_and_validated(ishop, db):
    from app.modules.integrations.models import Integration

    a = acct(ishop)
    r = connect(ishop, "razorpay", {"settlement_account_id": a["bank"]["id"]},
                {"key_id": "rzp_test_1", "key_secret": "SUPER-SECRET", "webhook_secret": "whsec"})
    assert r["has_credentials"] and "credentials" not in r and r["webhook_url"].startswith("https://erp.test/api/v1/hooks/razorpay/")
    raw = db.scalar(select(Integration.credentials_encrypted).where(Integration.id == uuid.UUID(r["id"])))
    db.rollback()  # release the SQLite read transaction before calling the API again
    assert "SUPER-SECRET" not in raw and raw.startswith("gAAAA")  # Fernet token
    listing = ishop.api.c.get(f"{ishop.base}/integrations", headers=ishop.h).text
    audit = ishop.api.c.get("/api/v1/audit-logs?limit=200", headers=ishop.h).text
    assert "SUPER-SECRET" not in listing and "SUPER-SECRET" not in audit

    connect(ishop, "razorpay", {"settlement_account_id": a["bank"]["id"]}, {"key_id": "x"}, name="r2", expect=422)
    connect(ishop, "razorpay", {"settlement_account_id": a["sales"]["id"]},
            {"key_id": "k", "key_secret": "s", "webhook_secret": "w"}, name="r3", expect=422)  # not a bank
    connect(ishop, "razorpay", {"settlement_account_id": a["bank"]["id"], "key_secret": "leak"},
            {"key_id": "k", "key_secret": "s", "webhook_secret": "w"}, name="r4", expect=422)  # secret in config
    connect(ishop, "paypal-ish", {}, {}, name="r5", expect=422)


def test_key_rotation_reencrypts(monkeypatch):
    from cryptography.fernet import Fernet

    from app.core import crypto
    from app.core.config import get_settings

    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(get_settings(), "encryption_keys", [old])
    token = crypto.encrypt_json({"k": "v"})
    monkeypatch.setattr(get_settings(), "encryption_keys", [new, old])
    rotated = crypto.reencrypt(token)
    monkeypatch.setattr(get_settings(), "encryption_keys", [new])
    assert crypto.decrypt_json(rotated) == {"k": "v"}
    with pytest.raises(crypto.EncryptionUnavailable):
        crypto.decrypt_json(token)  # old key removed: old ciphertext unreadable


def test_connection_test_success_failure_and_logs(ishop, fake):
    a = acct(ishop)
    i = connect(ishop, "razorpay", {"settlement_account_id": a["bank"]["id"]},
                {"key_id": "rzp_test_1", "key_secret": "secret", "webhook_secret": "w"})
    fake.on("GET", "https://api.razorpay.com/v1/payment_links", lambda r: {"items": []})
    assert ishop.post(f"/integrations/{i['id']}/test", {}, expect=200) == {"ok": True, "message": "Razorpay keys accepted"}
    req = fake.last("https://api.razorpay.com/v1/payment_links")
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"rzp_test_1:secret").decode()

    fake.on("GET", "https://api.razorpay.com/v1/payment_links",
            lambda r: httpx.Response(401, json={"error": {"description": "Authentication failed"}}))
    r = ishop.post(f"/integrations/{i['id']}/test", {}, expect=200)
    assert not r["ok"] and "401" in r["message"]
    listed = ishop.get("/integrations")[0]
    assert listed["status"] == "error" and "401" in listed["last_error"]
    logs = ishop.get(f"/integrations/{i['id']}/logs")["items"]
    assert [x["success"] for x in logs] == [False, True]


def test_billing_operator_cannot_manage_integrations(ishop, api):
    op_h, _ = api.member_session(ishop.h, ishop.bid, "op@example.com", "billing_operator")
    assert api.c.get(f"{ishop.base}/integrations", headers=op_h).status_code == 403


# ---------------------------------------------------------------------------- payment gateways


def _razorpay(ishop, fake) -> dict:
    a = acct(ishop)
    i = connect(ishop, "razorpay", {"settlement_account_id": a["bank"]["id"]},
                {"key_id": "rzp_test_1", "key_secret": "secret", "webhook_secret": "whsec_123"})

    def create(r: httpx.Request) -> dict:
        body = json.loads(r.content)
        return {"id": f"plink_{body['reference_id'].replace('/', '')}", "short_url": "https://rzp.io/i/abc",
                "status": "created"}

    fake.on("POST", "https://api.razorpay.com/v1/payment_links", create)
    return i


def _rzp_paid(link_id: str, payment_id: str, paise: int) -> bytes:
    return json.dumps({"event": "payment_link.paid", "payload": {
        "payment_link": {"entity": {"id": link_id, "status": "paid"}},
        "payment": {"entity": {"id": payment_id, "amount": paise, "status": "captured", "method": "upi"}}}}).encode()


def test_razorpay_payment_link_to_receipt_in_books(ishop, fake):
    i = _razorpay(ishop, fake)
    p = stocked(ishop, "rz", selling_price="1000", gst_rate="18")
    c = customer(ishop, "Online Buyer", email="buyer@example.com", phone="9876543210")
    inv = invoice(ishop, [{"product_id": p}], party_id=c["id"])
    link = ishop.post(f"/documents/{inv['id']}/payment-link", {}, expect=200)
    sent = json.loads(fake.last("https://api.razorpay.com/v1/payment_links").content)
    assert sent["amount"] == 118000 and sent["reference_id"] == inv["number"]
    assert sent["customer"] == {"name": "Online Buyer", "email": "buyer@example.com", "contact": "9876543210"}
    assert ishop.post(f"/documents/{inv['id']}/payment-link", {}, expect=200)["id"] == link["id"]  # reused

    body = _rzp_paid(link["provider_link_id"], "pay_001", 118000)
    sig = hmac.new(b"whsec_123", body, hashlib.sha256).hexdigest()
    bad = hook(ishop, "razorpay", i["id"], body, {"x-razorpay-signature": "0" * 64, "x-razorpay-event-id": "evt1"})
    assert bad.status_code == 401
    assert ishop.get(f"/documents/{inv['id']}")["payment_status"] == "unpaid"  # forged call did nothing

    ok = hook(ishop, "razorpay", i["id"], body, {"x-razorpay-signature": sig, "x-razorpay-event-id": "evt1"})
    assert ok.status_code == 200 and ok.json()["result"] == "applied"
    again = hook(ishop, "razorpay", i["id"], body, {"x-razorpay-signature": sig, "x-razorpay-event-id": "evt1"})
    assert again.json()["result"] == "duplicate"
    got = ishop.get(f"/documents/{inv['id']}")
    assert got["payment_status"] == "paid"
    pays = ishop.get("/payments")["items"]
    assert len(pays) == 1 and pays[0]["reference"] == "razorpay:pay_001" and pays[0]["mode"] == "upi"
    assert bal(ishop, "bank") == D("1180")
    assert ishop.get(f"/documents/{inv['id']}/payment-links")[0]["status"] == "paid"
    unknown = _rzp_paid("plink_other", "pay_002", 500)
    r = hook(ishop, "razorpay", i["id"], unknown, {"x-razorpay-signature": hmac.new(b"whsec_123", unknown, hashlib.sha256).hexdigest(),
                                                    "x-razorpay-event-id": "evt2"})
    assert r.json()["result"] == "unmatched" and len(ishop.get("/payments")["items"]) == 1
    books_ok(ishop)


def test_cashfree_webhook_signature_and_replay_window(ishop, fake):
    a = acct(ishop)
    i = connect(ishop, "cashfree", {"settlement_account_id": a["bank"]["id"], "environment": "sandbox"},
                {"client_id": "cf_app", "client_secret": "cf_secret"})
    fake.on("POST", "https://sandbox.cashfree.com/pg/links",
            lambda r: {"link_id": json.loads(r.content)["link_id"], "link_url": "https://payments-test.cashfree.com/links/x",
                       "link_status": "ACTIVE"})
    p = stocked(ishop, "cf", selling_price="500")
    c = customer(ishop, "CF Buyer", phone="9000000000")
    inv = invoice(ishop, [{"product_id": p}], party_id=c["id"])
    link = ishop.post(f"/documents/{inv['id']}/payment-link", {}, expect=200)
    req = fake.last("https://sandbox.cashfree.com/pg/links")
    assert req.headers["x-api-version"] == "2023-08-01" and json.loads(req.content)["link_amount"] == 500.0

    def signed(ts: int) -> tuple[bytes, dict]:
        body = json.dumps({"type": "PAYMENT_LINK_EVENT", "data": {
            "link_id": link["provider_link_id"], "link_status": "PAID", "link_amount_paid": 500.0,
            "order": {"order_id": "o1", "transaction_id": 99887766, "transaction_status": "SUCCESS",
                      "order_amount": 500.0}}}).encode()
        sig = base64.b64encode(hmac.new(b"cf_secret", str(ts).encode() + body, hashlib.sha256).digest()).decode()
        return body, {"x-webhook-timestamp": str(ts), "x-webhook-signature": sig}

    stale_body, stale_h = signed(int(time.time()) - 3600)
    assert hook(ishop, "cashfree", i["id"], stale_body, stale_h).status_code == 401
    body, h = signed(int(time.time() * 1000))  # Cashfree sends milliseconds
    r = hook(ishop, "cashfree", i["id"], body, h)
    assert r.status_code == 200 and r.json()["result"] == "applied"
    assert ishop.get(f"/documents/{inv['id']}")["payment_status"] == "paid"
    books_ok(ishop)


# ---------------------------------------------------------------------------- messaging


class FakeSMTP:
    sent: list[Any] = []
    fail_next = 0

    def __init__(self, host, port, timeout=None, context=None):  # noqa: ANN001
        self.host = host

    def starttls(self, context=None):  # noqa: ANN001
        pass

    def login(self, user, password):  # noqa: ANN001
        if password != "app-pass":
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def noop(self):
        pass

    def send_message(self, msg):  # noqa: ANN001
        if FakeSMTP.fail_next:
            FakeSMTP.fail_next -= 1
            import smtplib
            raise smtplib.SMTPServerDisconnected("connection dropped")
        FakeSMTP.sent.append(msg)
        return {}

    def quit(self):
        pass


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail_next = [], 0
    monkeypatch.setitem(msg_provider.smtp_classes, "starttls", FakeSMTP)
    return FakeSMTP


def test_email_invoice_via_worker_with_retries(ishop, smtp, db):
    from app.modules.integrations.models import OutboundMessage
    from app.modules.integrations.outbox import process_due

    connect(ishop, "smtp", {"host": "smtp.gmail.com", "port": "587", "security": "starttls",
                            "from_email": "billing@shop.in", "from_name": "Shop", "username": "billing@shop.in"},
            {"password": "app-pass"})
    p = stocked(ishop, "em", selling_price="250")
    c = customer(ishop, "Mail Buyer", email="buyer@example.com")
    inv = invoice(ishop, [{"product_id": p}], party_id=c["id"])
    queued = ishop.post(f"/documents/{inv['id']}/send", {"channel": "email", "message": "Thank you!"}, expect=202)
    assert queued["status"] == "queued" and queued["recipient"] == "buyer@example.com"
    assert smtp.sent == []  # nothing is sent inside the request

    smtp.fail_next = 1
    now = datetime.now(UTC)
    assert process_due(db, now=now) == {"sent": 0, "retrying": 1, "failed": 0}
    m = db.get(OutboundMessage, uuid.UUID(queued["id"]))
    db.refresh(m)
    assert m.attempts == 1 and "connection dropped" in m.last_error
    assert process_due(db, now=now) == {"sent": 0, "retrying": 0, "failed": 0}  # not due yet
    assert process_due(db, now=now + timedelta(minutes=2))["sent"] == 1
    email = smtp.sent[0]
    assert email["To"] == "buyer@example.com" and inv["number"] in email["Subject"]
    att = [part for part in email.iter_attachments()]
    assert att[0].get_filename() == inv["number"].replace("/", "-") + ".pdf"
    assert att[0].get_payload(decode=True)[:5] == b"%PDF-"
    html = email.get_body(("html",)).get_content()
    assert "Thank you!" in html and "Rupees Two Hundred Fifty Only" in html


def test_email_gives_up_after_retries(ishop, smtp, db):
    from app.modules.integrations.outbox import process_due

    connect(ishop, "smtp", {"host": "smtp.x.in", "port": "587", "security": "starttls", "from_email": "a@x.in",
                            "username": "a@x.in"}, {"password": "wrong"})
    p = stocked(ishop, "em2", selling_price="10")
    inv = invoice(ishop, [{"product_id": p}], payments=[{"mode": "cash", "amount": "10"}])
    m = ishop.post(f"/documents/{inv['id']}/send", {"channel": "email", "recipient": "x@example.com"}, expect=202)
    t = datetime.now(UTC)
    for _ in range(6):
        t += timedelta(hours=7)
        process_due(db, now=t)
    failed = ishop.get("/messages", failed_only=True)["items"]
    assert [x["id"] for x in failed] == [m["id"]] and "SMTP connection failed" in failed[0]["last_error"]
    assert failed[0]["attempts"] == 6
    assert ishop.post(f"/messages/{m['id']}/retry", {}, expect=200)["status"] == "queued"


def test_whatsapp_document_and_delivery_webhooks(ishop, fake, db):
    from app.modules.integrations.outbox import process_due

    i = connect(ishop, "whatsapp_cloud", {"phone_number_id": "1055", "template_name": "invoice_doc", "language": "en"},
                {"access_token": "EAAG", "app_secret": "appsec", "verify_token": "vt-123"})
    fake.on("POST", "https://graph.facebook.com/v21.0/1055/media", lambda r: {"id": "media-9"})
    fake.on("POST", "https://graph.facebook.com/v21.0/1055/messages",
            lambda r: {"messages": [{"id": "wamid.ABC"}]})
    p = stocked(ishop, "wa", selling_price="99")
    c = customer(ishop, "WA Buyer", phone="919812345678")
    inv = invoice(ishop, [{"product_id": p}], party_id=c["id"])
    ishop.post(f"/documents/{inv['id']}/send", {"channel": "whatsapp"}, expect=202)
    assert process_due(db)["sent"] == 1
    sent = json.loads(fake.last("https://graph.facebook.com/v21.0/1055/messages").content)
    assert sent["to"] == "919812345678" and sent["template"]["name"] == "invoice_doc"
    header, body = sent["template"]["components"]
    assert header["parameters"][0]["document"]["id"] == "media-9"
    assert [x["text"] for x in body["parameters"]] == ["WA Buyer", inv["number"], "99.00"]
    assert fake.last("https://graph.facebook.com/v21.0/1055/media").headers["authorization"] == "Bearer EAAG"

    v = ishop.api.c.get(f"/api/v1/hooks/whatsapp_cloud/{i['id']}",
                        params={"hub.mode": "subscribe", "hub.verify_token": "vt-123", "hub.challenge": "42"})
    assert v.status_code == 200 and v.text == "42"
    assert ishop.api.c.get(f"/api/v1/hooks/whatsapp_cloud/{i['id']}",
                           params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"}).status_code == 401

    def status(s: str) -> httpx.Response:
        b = json.dumps({"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.ABC", "status": s}]}}]}]}).encode()
        return hook(ishop, "whatsapp_cloud", i["id"], b,
                    {"x-hub-signature-256": "sha256=" + hmac.new(b"appsec", b, hashlib.sha256).hexdigest()})

    status("read")
    status("delivered")  # arrives late: must not downgrade "read"
    assert ishop.get("/messages")["items"][0]["status"] == "read"


def test_msg91_sms(ishop, fake, db):
    from app.modules.integrations.outbox import process_due

    connect(ishop, "msg91", {"invoice_flow_id": "flow-inv"}, {"auth_key": "msgkey"})
    fake.on("POST", "https://control.msg91.com/api/v5/flow", lambda r: {"type": "success", "message": "req-77"})
    p = stocked(ishop, "sms", selling_price="40")
    c = customer(ishop, "SMS Buyer", phone="98123 45678")
    inv = invoice(ishop, [{"product_id": p}], party_id=c["id"])
    ishop.post(f"/documents/{inv['id']}/send", {"channel": "sms"}, expect=202)
    assert process_due(db)["sent"] == 1
    req = fake.last("https://control.msg91.com/api/v5/flow")
    body = json.loads(req.content)
    assert req.headers["authkey"] == "msgkey" and body["template_id"] == "flow-inv"
    assert body["recipients"][0]["mobiles"] == "919812345678" and body["recipients"][0]["number"] == inv["number"]
    fake.on("POST", "https://control.msg91.com/api/v5/flow", lambda r: {"type": "error", "message": "DLT template mismatch"})
    ishop.post(f"/documents/{inv['id']}/send", {"channel": "sms"}, expect=202)
    assert process_due(db)["retrying"] == 1


# ---------------------------------------------------------------------------- Google


def test_google_oauth_gmail_and_drive(ishop, fake, db, smtp):
    from urllib.parse import parse_qs, urlparse

    from app.modules.integrations.outbox import process_due

    r = connect(ishop, "google", {}, {"client_id": "cid.apps.googleusercontent.com", "client_secret": "gsec"})
    assert r["status"] == "pending"
    q = parse_qs(urlparse(r["consent_url"]).query)
    assert q["client_id"] == ["cid.apps.googleusercontent.com"] and q["access_type"] == ["offline"]
    assert q["redirect_uri"] == ["https://erp.test/api/v1/hooks/google/oauth/callback"]
    assert "gmail.send" in q["scope"][0] and "drive.file" in q["scope"][0]

    def token(req: httpx.Request) -> dict:
        form = parse_qs(req.content.decode())
        if form["grant_type"] == ["authorization_code"]:
            assert form["code"] == ["auth-code"]
            return {"access_token": "at1", "refresh_token": "rt-xyz", "expires_in": 3599}
        assert form["refresh_token"] == ["rt-xyz"]
        return {"access_token": "at2", "expires_in": 3599}

    fake.on("POST", "https://oauth2.googleapis.com/token", token)
    bad = ishop.api.c.get("/api/v1/hooks/google/oauth/callback", params={"code": "auth-code", "state": "forged"})
    assert bad.status_code == 422
    ok = ishop.api.c.get("/api/v1/hooks/google/oauth/callback", params={"code": "auth-code", "state": q["state"][0]})
    assert ok.status_code == 200
    assert ishop.get("/integrations")[0]["status"] == "connected"

    fake.on("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", lambda r: {"id": "gm-1"})
    p = stocked(ishop, "gm", selling_price="75")
    c = customer(ishop, "Gmail Buyer", email="g@example.com")
    inv = invoice(ishop, [{"product_id": p}], party_id=c["id"])
    ishop.post(f"/documents/{inv['id']}/send", {"channel": "email"}, expect=202)
    assert process_due(db)["sent"] == 1
    sent = fake.last("https://gmail.googleapis.com")
    assert sent.headers["authorization"] == "Bearer at2"
    raw = base64.urlsafe_b64decode(json.loads(sent.content)["raw"]).decode(errors="ignore")
    assert "To: g@example.com" in raw and "application/pdf" in raw

    from app.core.config import get_settings

    if get_settings().is_sqlite:
        session = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=u1"
        fake.on("POST", "https://www.googleapis.com/upload/drive/v3/files",
                lambda r: httpx.Response(200, headers={"Location": session}))
        fake.on("PUT", session, lambda r: {"id": "drive-file-1"})
        out = ishop.post(f"/integrations/{r['id']}/drive-backup", {}, expect=200)
        assert out["file_id"] == "drive-file-1" and out["encrypted"] is True
        start = [q for q in fake.requests if q.method == "POST" and "/upload/drive/" in str(q.url)][-1]
        assert json.loads(start.content)["name"] == out["filename"]
        up = fake.last(session)
        # Only the encrypted file ever leaves the server.
        assert up.content.startswith(b"ERPBK1\n") and b"SQLite format 3" not in up.content
        assert up.headers["content-range"] == f"bytes 0-{len(up.content) - 1}/{len(up.content)}"


# ---------------------------------------------------------------------------- Shopify


def _shopify(ishop, fake) -> dict:
    a = acct(ishop)
    return connect(ishop, "shopify", {"shop_domain": "ayur-store.myshopify.com", "warehouse_id": ishop.main,
                                      "branch_id": ishop.ho, "settlement_account_id": a["bank"]["id"]},
                   {"admin_token": "shpat_1", "api_secret": "shpsec"})


def _order(oid: int, sku: str, paid: bool, qty: int = 2) -> dict:
    return {"id": oid, "name": f"#{oid}", "created_at": f"{TODAY}T10:00:00+05:30", "taxes_included": True,
            "financial_status": "paid" if paid else "pending", "cancelled_at": None,
            "email": "web@example.com", "customer": {"first_name": "Web", "last_name": "Buyer", "email": "web@example.com"},
            "line_items": [{"sku": sku, "quantity": qty, "price": "590.00", "total_discount": "0.00"}],
            "shipping_lines": [{"price": "59.00"}]}


def test_shopify_products_and_orders(ishop, fake):
    i = _shopify(ishop, fake)
    base_url = "https://ayur-store.myshopify.com/admin/api/2025-01"
    page2 = f'<{base_url}/products.json?page_info=abc&limit=250>; rel="next"'

    def products(r: httpx.Request) -> httpx.Response:
        assert r.headers["x-shopify-access-token"] == "shpat_1"
        if r.url.params.get("page_info") == "abc":
            return httpx.Response(200, json={"products": [{"title": "Brahmi Oil", "variants": [
                {"id": 2, "sku": "BRAHMI-100", "price": "350.00", "title": "Default Title"}]}]})
        return httpx.Response(200, headers={"link": page2}, json={"products": [{"title": "Kesar Cream", "variants": [
            {"id": 1, "sku": "KESAR-50", "price": "590.00", "title": "50 g", "barcode": "8909999000011"}]}]})

    fake.on("GET", f"{base_url}/products.json", products)
    assert ishop.post(f"/integrations/{i['id']}/shopify/import-products", {}, expect=200) == {"created": 2, "skipped": 0}
    assert ishop.post(f"/integrations/{i['id']}/shopify/import-products", {}, expect=200) == {"created": 0, "skipped": 2}
    kesar = ishop.get("/products", q="KESAR-50")["items"][0]
    assert kesar["name"] == "Kesar Cream - 50 g" and kesar["barcode"] == "8909999000011"
    ishop.opening([{"product_id": kesar["id"], "quantity": "10", "unit_cost": "200"}])

    fake.on("GET", f"{base_url}/orders.json", lambda r: {"orders": [_order(1001, "KESAR-50", paid=True),
                                                                    _order(1002, "UNKNOWN", paid=False)]})
    out = ishop.post(f"/integrations/{i['id']}/shopify/import-orders", {}, expect=200)
    assert out["imported"] == 1 and len(out["errors"]) == 1 and "UNKNOWN" in out["errors"][0]
    assert ishop.get("/integrations")[0]["last_synced_at"] is None  # cursor kept: the failed order retries
    inv = ishop.get("/documents", q="shopify:1001")["items"][0]
    full = ishop.get(f"/documents/{inv['id']}")
    assert full["payment_status"] == "paid" and full["grand_total"] == "1239.0000"  # 2 × 590 + 59 shipping
    assert ishop.stock(kesar["id"])[0] == D("8")
    again = ishop.post(f"/integrations/{i['id']}/shopify/import-orders", {}, expect=200)
    assert again["exists"] == 1 and again["imported"] == 0
    books_ok(ishop)


def test_shopify_webhooks(ishop, fake):
    i = _shopify(ishop, fake)
    p = ishop.product("SHOP-1", selling_price="590")["id"]
    ishop.opening([{"product_id": p, "quantity": "5", "unit_cost": "100"}])

    def send(order: dict, topic: str, wid: str, secret: bytes = b"shpsec") -> httpx.Response:
        body = json.dumps(order).encode()
        sig = base64.b64encode(hmac.new(secret, body, hashlib.sha256).digest()).decode()
        return hook(ishop, "shopify", i["id"], body, {"x-shopify-hmac-sha256": sig, "x-shopify-topic": topic,
                                                       "x-shopify-webhook-id": wid})

    assert send(_order(2001, "SHOP-1", paid=False, qty=1), "orders/create", "w1", secret=b"wrong").status_code == 401
    r = send(_order(2001, "SHOP-1", paid=False, qty=1), "orders/create", "w1")
    assert r.json()["result"] == "imported"
    assert send(_order(2001, "SHOP-1", paid=False, qty=1), "orders/create", "w1").json()["result"] == "duplicate"
    inv = ishop.get("/documents", q="shopify:2001")["items"][0]
    assert inv["payment_status"] == "unpaid"
    assert send(_order(2001, "SHOP-1", paid=True, qty=1), "orders/paid", "w2").json()["result"] == "paid"
    assert ishop.get(f"/documents/{inv['id']}")["payment_status"] == "paid"
    books_ok(ishop)


# ---------------------------------------------------------------------------- data


def _upload(s: Shop, kind: str, filename: str, data: bytes, **params) -> dict:
    r = s.api.c.post(f"{s.base}/data/import/{kind}", headers=s.h, params=params,
                     files={"file": (filename, data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def test_product_import_all_or_nothing_and_excel_roundtrip(ishop):
    good = b"name,sku,unit,hsn,selling_price,gst_rate,prices_include_tax\nTulsi Drops,TD-1,BTL,3004,120,12,yes\nNeem Soap,NS-1,PCS,3401,45,18,no\n"
    assert _upload(ishop, "products", "p.csv", good, dry_run=True) == {"valid_rows": 2, "errors": [], "imported": 0}
    assert ishop.get("/products")["total"] == 0
    bad = good + b"Broken,TD-1,BTL,12,x,5,no\n"
    out = _upload(ishop, "products", "p.csv", bad)
    assert out["imported"] == 0 and [e["row"] for e in out["errors"]] == [4]
    assert ishop.get("/products")["total"] == 0  # nothing written when any row fails
    assert _upload(ishop, "products", "p.csv", good)["imported"] == 2

    xlsx = ishop.api.c.get(f"{ishop.base}/data/export/products", headers=ishop.h)
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    from openpyxl import load_workbook

    ws = load_workbook(io.BytesIO(xlsx.content)).active
    assert [c.value for c in ws[1]][:2] == ["name", "sku"] and ws.max_row == 3
    csv_out = ishop.api.c.get(f"{ishop.base}/data/export/products", headers=ishop.h, params={"format": "csv"})
    assert "Neem Soap,NS-1" in csv_out.content.decode("utf-8-sig")


def test_customer_import_and_tally_export(ishop):
    rows = (f"name,gstin,phone,email,address,city,pincode\nKA Clinic,29AAGCB7383J1Z4,9876500000,ka@example.com,"
            f"1 MG Road,Bengaluru,560001\nLocal Buyer,,9000000001,,,,\n").encode()
    assert _upload(ishop, "customers", "c.csv", rows)["imported"] == 2
    ka = ishop.get("/customers", q="KA Clinic")["items"][0]
    assert ka["state_code"] == "29" and ka["addresses"][0]["city"] == "Bengaluru"

    p = stocked(ishop, "tal", selling_price="1000", gst_rate="18")
    invoice(ishop, [{"product_id": p}], party_id=ka["id"])
    r = ishop.api.c.get(f"{ishop.base}/data/export-tally", headers=ishop.h, params={"date_from": TODAY, "date_to": TODAY})
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    ledgers = {x.get("NAME"): x.findtext("PARENT") for x in root.iter("LEDGER")}
    assert ledgers["KA Clinic"] == "Sundry Debtors" and ledgers["Output IGST"] == "Duties & Taxes"
    for v in root.iter("VOUCHER"):
        assert sum(D(x.findtext("AMOUNT")) for x in v.iter("ALLLEDGERENTRIES.LIST")) == 0
    sale = [v for v in root.iter("VOUCHER") if "Sales Invoice" in (v.findtext("NARRATION") or "")][0]
    entries = {x.findtext("LEDGERNAME"): (x.findtext("ISDEEMEDPOSITIVE"), x.findtext("AMOUNT"))
               for x in sale.iter("ALLLEDGERENTRIES.LIST")}
    assert entries["KA Clinic"] == ("Yes", "-1180.00") and entries["Sales"] == ("No", "1000.00")


def test_disconnect_wipes_secrets_and_blocks_webhooks(ishop, fake):
    i = _razorpay(ishop, fake)
    ishop.post(f"/integrations/{i['id']}/disconnect", {}, expect=200)
    assert ishop.get("/integrations")[0]["has_credentials"] is False
    body = _rzp_paid("x", "y", 100)
    r = hook(ishop, "razorpay", i["id"], body, {"x-razorpay-signature": "a"})
    assert r.status_code == 404
    ishop.post(f"/integrations/{i['id']}/credentials", {"key_id": "k2", "key_secret": "s2", "webhook_secret": "w2"},
               expect=200)
    assert ishop.get("/integrations")[0]["status"] == "connected"
