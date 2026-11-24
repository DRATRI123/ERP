"""Developer platform: API keys & scopes, OAuth tokens, rate limits, idempotency, request logs,
and signed outgoing webhooks with retries."""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.modules.integrations import base as ibase
from tests.conftest import TODAY, VALID_GSTIN, Shop
from tests.test_billing import customer, invoice, stocked
from tests.test_integrations import FakeProviders

D = Decimal


@pytest.fixture
def dshop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers, gstin=VALID_GSTIN, state_code="27")


@pytest.fixture(autouse=True)
def _reset_limits():
    from app.core.ratelimit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def public_dns(monkeypatch):
    """Resolve every webhook host to a public address unless the test says otherwise."""
    from app.modules.developer import webhooks

    table = {"hooks.example.com": "93.184.216.34", "internal.example.com": "10.0.0.5",
             "metadata.example.com": "169.254.169.254"}

    def fake(host, port, proto=0):  # noqa: ANN001
        ip = table.get(host, host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(webhooks, "resolver", fake)


@pytest.fixture
def fake():
    f = FakeProviders()
    ibase.set_transport(httpx.MockTransport(f))
    yield f
    ibase.set_transport(None)


def app_with_key(s: Shop, scopes: list[str], headers=None, **kw) -> tuple[dict, str]:
    r = s.api.c.post("/api/v1/developer/apps", headers=headers or s.h,
                     json={"name": kw.pop("name", "Clinic App"), "business_ids": [s.bid], "scopes": scopes, **kw})
    assert r.status_code == 201, r.text
    app = r.json()
    k = s.api.c.post(f"/api/v1/developer/apps/{app['id']}/keys", headers=headers or s.h, json={"label": "prod"})
    assert k.status_code == 201, k.text
    return app, k.json()["key"]


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# ---------------------------------------------------------------------------- keys & scopes


def test_api_key_scopes_are_enforced(dshop, db):
    from sqlalchemy import select

    from app.modules.developer.models import ApiKey

    app, key = app_with_key(dshop, ["customers:read", "customers:write", "products:read"])
    assert key.startswith("erp_") and app["keys"] == []  # secret only returned at creation
    stored = db.scalar(select(ApiKey.key_hash))
    db.rollback()
    assert key not in stored and len(stored) == 64
    h = bearer(key)
    c = dshop.post("/customers", {"name": "From Clinic App", "phone": "9876500000"}, headers=h)
    assert dshop.get("/customers", headers=h)["total"] == 1
    assert dshop.api.c.get(f"{dshop.base}/suppliers", headers=h).status_code == 403  # no scope
    assert dshop.api.c.post(f"{dshop.base}/documents", headers=h, json={
        "doc_type": "sales_invoice", "lines": [{"description": "x", "unit_price": "1"}]}).status_code == 403
    # API keys never reach account, user or developer endpoints, even when the owner created them.
    for path in ("/api/v1/auth/me", "/api/v1/users", "/api/v1/developer/apps", "/api/v1/audit-logs"):
        assert dshop.api.c.get(path, headers=h).status_code == 403, path
    assert dshop.api.c.post("/api/v1/businesses", headers=h, json={"name": "X"}).status_code == 403
    assert dshop.api.c.post(f"{dshop.base}/archive", headers=h).status_code == 403
    audit = dshop.get("/audit-logs", action="party.created")["items"]
    assert audit[0]["changes"]["via_api_key"] == key.split("_")[1]  # attributed to the key
    assert c["name"] == "From Clinic App"


def test_api_key_business_isolation_and_revocation(api, owner_headers, dshop):
    other = Shop(api, owner_headers, name="Other Biz")
    app, key = app_with_key(dshop, ["businesses:read", "customers:read"])
    h = bearer(key)
    assert [b["id"] for b in api.c.get("/api/v1/businesses", headers=h).json()] == [dshop.bid]
    assert api.c.get(f"{other.base}/customers", headers=h).status_code == 404
    assert api.c.get(f"{dshop.base}/customers", headers=h).status_code == 200
    keys = api.c.get("/api/v1/developer/apps", headers=owner_headers).json()[0]["keys"]
    api.c.post(f"/api/v1/developer/apps/{app['id']}/keys/{keys[0]['id']}/revoke", headers=owner_headers)
    r = api.c.get(f"{dshop.base}/customers", headers=h)
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"
    assert api.c.get(f"{dshop.base}/customers", headers=bearer("erp_nope_nope")).status_code == 401


def test_key_rotation_keeps_old_key_for_grace_period(api, owner_headers, dshop, db):
    from sqlalchemy import select

    from app.modules.developer.models import ApiKey

    app, old = app_with_key(dshop, ["customers:read"])
    kid = api.c.get("/api/v1/developer/apps", headers=owner_headers).json()[0]["keys"][0]["id"]
    r = api.c.post(f"/api/v1/developer/apps/{app['id']}/keys/{kid}/rotate", headers=owner_headers).json()
    new = r["key"]
    assert api.c.get(f"{dshop.base}/customers", headers=bearer(new)).status_code == 200
    assert api.c.get(f"{dshop.base}/customers", headers=bearer(old)).status_code == 200  # grace period
    k = db.get(ApiKey, uuid.UUID(kid))
    k.revoked_at = datetime.now(UTC) - timedelta(seconds=1)  # grace period over
    db.commit()
    assert api.c.get(f"{dshop.base}/customers", headers=bearer(old)).status_code == 401
    assert db.scalar(select(ApiKey).where(ApiKey.prefix == new.split("_")[1])) is not None


def test_key_capped_by_creators_current_permissions(api, owner_headers, dshop):
    admin_h, member = api.member_session(owner_headers, dshop.bid, "dev@example.com", "business_admin",
                                         deny_permissions=["suppliers.view"])
    r = api.c.post("/api/v1/developer/apps", headers=admin_h, json={
        "name": "Too broad", "business_ids": [dshop.bid], "scopes": ["suppliers:read"]})
    assert r.status_code == 403  # cannot hand out what they do not have
    other = Shop(api, owner_headers, name="Not Mine")
    r = api.c.post("/api/v1/developer/apps", headers=admin_h, json={
        "name": "Wrong biz", "business_ids": [other.bid], "scopes": ["customers:read"]})
    assert r.status_code == 403
    app, key = app_with_key(dshop, ["customers:read"], headers=admin_h)
    assert api.c.get(f"{dshop.base}/customers", headers=bearer(key)).status_code == 200
    # Suspending the creator's membership shuts the key out too.
    api.c.post(f"{dshop.base}/members/{member['id']}/suspend", headers=owner_headers)
    assert api.c.get(f"{dshop.base}/customers", headers=bearer(key)).status_code == 404
    op_h, _ = api.member_session(owner_headers, dshop.bid, "op@example.com", "billing_operator")
    assert api.c.get("/api/v1/developer/apps", headers=op_h).status_code == 403


def test_oauth_client_credentials_token(dshop, api):
    app, key = app_with_key(dshop, ["customers:read", "customers:write"])
    r = api.c.post("/api/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": app["id"],
                                                "client_secret": key, "scope": "customers:read"})
    assert r.status_code == 200, r.text
    tok = r.json()
    assert tok["token_type"] == "bearer" and tok["expires_in"] == 900 and tok["scope"] == "customers:read"
    h = bearer(tok["access_token"])
    assert api.c.get(f"{dshop.base}/customers", headers=h).status_code == 200
    assert api.c.post(f"{dshop.base}/customers", headers=h, json={"name": "x"}).status_code == 403  # narrowed
    bad = api.c.post("/api/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": app["id"],
                                                  "client_secret": key, "scope": "suppliers:read"})
    assert bad.status_code == 403
    wrong = api.c.post("/api/v1/oauth/token", data={"grant_type": "client_credentials",
                                                    "client_id": str(uuid.uuid4()), "client_secret": key})
    assert wrong.status_code == 401
    assert api.c.post("/api/v1/oauth/token", data={"grant_type": "password", "client_id": app["id"],
                                                   "client_secret": key}).status_code == 422


def test_rate_limit_headers_and_429(dshop, api):
    app, key = app_with_key(dshop, ["customers:read"], rate_limit_per_minute=3)
    h = bearer(key)
    r = api.c.get(f"{dshop.base}/customers", headers=h)
    assert r.headers["X-RateLimit-Limit"] == "3" and r.headers["X-RateLimit-Remaining"] == "2"
    api.c.get(f"{dshop.base}/customers", headers=h)
    api.c.get(f"{dshop.base}/customers", headers=h)
    r = api.c.get(f"{dshop.base}/customers", headers=h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited" and int(r.headers["Retry-After"]) >= 1
    # Usage and error logs for the dashboard.
    logs = api.c.get(f"/api/v1/developer/apps/{app['id']}/logs", headers=dshop.h).json()
    assert logs["total"] == 4 and logs["items"][0]["status"] == 429 and logs["items"][0]["error_code"] == "rate_limited"
    errors = api.c.get(f"/api/v1/developer/apps/{app['id']}/logs", headers=dshop.h, params={"errors_only": True}).json()
    assert errors["total"] == 1
    use = api.c.get(f"/api/v1/developer/apps/{app['id']}/usage", headers=dshop.h).json()
    assert use["total_requests"] == 4 and use["total_errors"] == 1


def test_idempotency_keys(dshop, api):
    app, key = app_with_key(dshop, ["customers:write", "customers:read"])
    h = bearer(key) | {"Idempotency-Key": "create-cust-001"}
    body = {"name": "Idempotent Clinic"}
    a = api.c.post(f"{dshop.base}/customers", headers=h, json=body)
    b = api.c.post(f"{dshop.base}/customers", headers=h, json=body)
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert b.headers["Idempotent-Replayed"] == "true"
    assert dshop.get("/customers")["total"] == 1
    c = api.c.post(f"{dshop.base}/customers", headers=h, json={"name": "Different"})
    assert c.status_code == 422 and c.json()["error"]["code"] == "idempotency_key_reused"
    # Keys are per caller: the owner using the same key string is a separate request.
    o = api.c.post(f"{dshop.base}/customers", headers=dshop.h | {"Idempotency-Key": "create-cust-001"}, json=body)
    assert o.status_code == 201 and o.json()["id"] != a.json()["id"]
    # Validation failures are stored too: the same retry gets the same answer.
    bad = bearer(key) | {"Idempotency-Key": "bad-1"}
    assert api.c.post(f"{dshop.base}/customers", headers=bad, json={"name": ""}).status_code == 422
    assert api.c.post(f"{dshop.base}/customers", headers=bad, json={"name": ""}).headers.get("Idempotent-Replayed") == "true"


# ---------------------------------------------------------------------------- webhooks


def test_webhook_url_rules(dshop, public_dns):
    for url, ok in (("http://hooks.example.com/x", False), ("https://internal.example.com/x", False),
                    ("https://metadata.example.com/latest", False), ("https://127.0.0.1/x", False),
                    ("https://hooks.example.com/erp", True)):
        r = dshop.api.c.post(f"{dshop.base}/webhooks", headers=dshop.h, json={"url": url, "events": ["invoice.*"]})
        assert (r.status_code == 201) == ok, (url, r.text)
    r = dshop.api.c.post(f"{dshop.base}/webhooks", headers=dshop.h,
                         json={"url": "https://hooks.example.com/a", "events": ["nonsense.event"]})
    assert r.status_code == 422


def test_webhook_delivery_signed_filtered_and_retried(dshop, public_dns, fake, db):
    from app.modules.developer.webhooks import deliver_due, fan_out

    ep = dshop.post("/webhooks", {"url": "https://hooks.example.com/erp", "events": ["invoice.*", "payment.created"]})
    secret = ep["secret"]
    received: list[httpx.Request] = []
    responses = iter([httpx.Response(500), httpx.Response(200, json={"ok": True})])
    fake.on("POST", "https://hooks.example.com/erp", lambda r: (received.append(r), next(responses))[1])

    p = stocked(dshop, "wh", selling_price="100")
    c = customer(dshop)
    inv = invoice(dshop, [{"product_id": p}], party_id=c["id"])
    assert fan_out(db) == 1  # invoice.created matches; document.finalized/journal.created do not
    now = datetime.now(UTC)
    assert deliver_due(db, now=now) == {"succeeded": 0, "retrying": 1, "failed": 0}
    assert deliver_due(db, now=now + timedelta(seconds=61))["succeeded"] == 1

    req = received[-1]
    body = req.content
    event = json.loads(body)
    assert event["type"] == "invoice.created" and event["data"]["number"] == inv["number"]
    assert req.headers["erp-event-id"] == event["id"] and req.headers["erp-event-type"] == "invoice.created"
    parts = dict(p.split("=", 1) for p in req.headers["erp-signature"].split(","))
    expected = hmac.new(secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256).hexdigest()
    assert parts["v1"] == expected
    log = dshop.get(f"/webhooks/{ep['id']}/deliveries")["items"][0]
    assert (log["status"], log["attempts"], log["response_status"]) == ("succeeded", 2, 200)

    rotated = dshop.post(f"/webhooks/{ep['id']}/rotate-secret", {}, expect=200)["secret"]
    dshop.post(f"/webhooks/{ep['id']}/test", {}, expect=202)
    fake.on("POST", "https://hooks.example.com/erp", lambda r: (received.append(r), httpx.Response(204))[1])
    deliver_due(db)
    sig = received[-1].headers["erp-signature"]
    t = sig.split(",")[0][2:]
    v1s = [x[3:] for x in sig.split(",")[1:]]
    for s in (rotated, secret):  # both secrets sign during the 24 h rotation window
        assert hmac.new(s.encode(), f"{t}.".encode() + received[-1].content, hashlib.sha256).hexdigest() in v1s


def test_webhook_failures_disable_endpoint_and_never_touch_data(dshop, public_dns, fake, db, monkeypatch):
    from app.modules.developer import webhooks
    from app.modules.developer.webhooks import deliver_due, fan_out

    monkeypatch.setattr(webhooks, "DISABLE_AFTER", 3)
    ep = dshop.post("/webhooks", {"url": "https://hooks.example.com/down", "events": ["*"]})
    fake.on("POST", "https://hooks.example.com/down", lambda r: httpx.Response(503))
    p = stocked(dshop, "dn", selling_price="10")
    inv = invoice(dshop, [{"product_id": p}], payments=[{"mode": "cash", "amount": "10"}])
    fan_out(db)
    t = datetime.now(UTC)
    for _ in range(3):
        deliver_due(db, now=t)
        t += timedelta(days=1)
    status = dshop.get("/webhooks")[0]
    assert status["status"] == "disabled" and "consecutive" in status["disabled_reason"]
    assert dshop.get(f"/documents/{inv['id']}")["status"] == "final"  # the sale is untouched
    failed = dshop.get(f"/webhooks/{ep['id']}/deliveries")["items"]
    d = failed[0]
    dshop.api.c.patch(f"{dshop.base}/webhooks/{ep['id']}", headers=dshop.h, json={"status": "active"})
    deliver_due(db, now=t)  # remaining pending deliveries resume
    r = dshop.post(f"/webhooks/deliveries/{d['id']}/retry", {}, expect=200)
    assert r["status"] == "pending"


def test_overdue_event(dshop, db):
    from sqlalchemy import select

    from app.modules.developer.webhooks import emit_overdue
    from app.modules.events.models import OutboxEvent

    p = stocked(dshop, "od", selling_price="100")
    c = customer(dshop)
    inv = invoice(dshop, [{"product_id": p}], party_id=c["id"])
    assert emit_overdue(db) == 0  # due today: not overdue yet
    assert emit_overdue(db, today=datetime.now(UTC) + timedelta(days=3)) == 1
    assert emit_overdue(db, today=datetime.now(UTC) + timedelta(days=4)) == 0  # once only
    ev = db.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "invoice.overdue"))
    assert ev.payload["number"] == inv["number"] and ev.payload["days_overdue"] == 3


def test_catalogs_and_docs(api):
    scopes = {s["scope"] for s in api.c.get("/api/v1/developer/scopes").json()}
    assert {"customers:read", "invoices:create", "reports:read"} <= scopes
    events = {e["type"] for e in api.c.get("/api/v1/developer/events").json()}
    assert {"invoice.created", "invoice.overdue", "stock.low", "payment.created"} <= events
    assert api.c.get("/api/v1/openapi.json").status_code == 200
