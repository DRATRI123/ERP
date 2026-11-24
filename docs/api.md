# ERP API — developer guide

Base URL: `https://<your-erp-domain>/api/v1` · Interactive reference: `/api/v1/docs` ·
OpenAPI 3: `/api/v1/openapi.json`

## 1. Create an application and key

Signed in as the owner (or a user with `developer.manage`):

```http
POST /api/v1/developer/apps
{"name": "Clinic app", "business_ids": ["<business-id>"], "scopes": ["customers:read", "invoices:create"],
 "rate_limit_per_minute": 120}

POST /api/v1/developer/apps/{app_id}/keys          → {"key": "erp_3fa1…_…", "prefix": "3fa1…"}
```

The key is shown **once**; only its hash is stored. Keys can expire, be revoked, or be rotated
(`…/keys/{id}/rotate` returns a new key and keeps the old one valid for 24 hours).

**What a key can do** = its scopes ∩ what the user who created it can do in that business
*right now*. Keys never get owner powers and cannot use account, user, developer or owner-only
endpoints. Suspending the creator shuts their keys out. Scopes: `GET /api/v1/developer/scopes`.

## 2. Authenticate

Either send the key directly:

```
Authorization: Bearer erp_3fa1…_…
```

or exchange it for a 15-minute token (OAuth 2.0 client credentials), optionally narrowed:

```bash
curl -X POST https://erp.example.com/api/v1/oauth/token \
  -d grant_type=client_credentials -d client_id=$APP_ID -d client_secret=$ERP_KEY -d "scope=customers:read"
```

API keys are not affected by two-factor authentication: they are separate credentials with
their own scopes, revocable at any time. Interactive sign-in for people (`/auth/login`) may
answer `{"mfa_required": true, "mfa_token": …}`; send it with the authenticator code to
`/auth/login/mfa`.

## 3. Conventions

* **Errors** — `{"error": {"code", "message", "details", "request_id"}}`. 401 bad credentials,
  403 missing scope/permission, 404 not found *or not yours*, 409 conflict (e.g. insufficient
  stock, credit limit), 422 validation, 429 rate limited (`Retry-After` header).
* **Money** — decimal strings (`"1180.0000"`), never floats. Send amounts as strings.
* **Pagination** — `?limit=50&offset=0` → `{"items", "total", "limit", "offset"}`.
* **Idempotency** — send `Idempotency-Key: <unique id>` on any POST. Retrying with the same key
  and body returns the original response (`Idempotent-Replayed: true`); the same key with a
  different body is refused (422); keys are kept 24 hours and scoped to your application.
  Sales documents also accept a `client_ref` field for permanent de-duplication.
* **Rate limits** — `X-RateLimit-Limit` / `X-RateLimit-Remaining` on every response.
* **Versioning** — breaking changes only ever ship under a new prefix (`/api/v2`); `/api/v1`
  keeps working. Additive changes (new fields, endpoints, events) can appear at any time, so
  ignore unknown fields.

## 4. Examples

### cURL — create a walk-in invoice paid by UPI

```bash
API=https://erp.example.com/api/v1/businesses/$BUSINESS_ID
DOC=$(curl -s -X POST $API/documents -H "Authorization: Bearer $ERP_KEY" -H "Idempotency-Key: pos-1-000123" \
  -H "Content-Type: application/json" \
  -d '{"doc_type":"sales_invoice","client_ref":"pos-1-000123","lines":[{"product_id":"'$PRODUCT_ID'","quantity":"2"}]}')
curl -s -X POST $API/documents/$(echo $DOC | jq -r .id)/finalize -H "Authorization: Bearer $ERP_KEY" \
  -H "Content-Type: application/json" -d '{"payments":[{"mode":"upi","amount":"'$(echo $DOC | jq -r .grand_total)'"}]}'
```

### Python

```python
import uuid, httpx

class ERP:
    def __init__(self, base_url: str, key: str, business_id: str):
        self.c = httpx.Client(base_url=f"{base_url}/api/v1/businesses/{business_id}",
                              headers={"Authorization": f"Bearer {key}"}, timeout=30)

    def post(self, path: str, body: dict) -> dict:
        r = self.c.post(path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
        if r.status_code >= 400:
            err = r.json()["error"]
            raise RuntimeError(f"{err['code']}: {err['message']} (request {err['request_id']})")
        return r.json()

erp = ERP("https://erp.example.com", "erp_…", "<business-id>")
patient = erp.post("/customers", {"name": "Asha Rao", "phone": "9876543210", "gst_type": "consumer"})
bill = erp.post("/documents", {"doc_type": "sales_invoice", "party_id": patient["id"],
                               "lines": [{"description": "Consultation", "hsn_sac": "999312",
                                          "unit_price": "500", "gst_rate": "0"}]})
erp.post(f"/documents/{bill['id']}/finalize", {"payments": [{"mode": "cash", "amount": bill["grand_total"]}]})
```

### TypeScript / JavaScript

```ts
const API = `https://erp.example.com/api/v1/businesses/${process.env.ERP_BUSINESS_ID}`;

async function erp<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(API + path, {
    ...init,
    headers: { Authorization: `Bearer ${process.env.ERP_KEY}`, "Content-Type": "application/json",
               ...(init.method === "POST" ? { "Idempotency-Key": crypto.randomUUID() } : {}), ...init.headers },
  });
  const body = await res.json();
  if (!res.ok) throw new Error(`${body.error.code}: ${body.error.message} (request ${body.error.request_id})`);
  return body as T;
}

const lowStock = await erp<{ sku: string; on_hand: string }[]>("/inventory/low-stock");
```

## 5. Webhooks

Register `POST /api/v1/businesses/{id}/webhooks {"url": "https://…", "events": ["invoice.*", "payment.created"]}`
(HTTPS, public address only). The response includes the signing **secret** — shown once.
Event catalog: `GET /api/v1/developer/events`. `"*"` subscribes to everything, `"invoice.*"` to a family.

Each delivery is `POST` JSON `{"id", "type", "created_at", "business_id", "data"}` with headers
`ERP-Event-Id`, `ERP-Event-Type` and `ERP-Signature: t=<unix seconds>,v1=<hex>[,v1=<hex>]`.

Verify before trusting it (Python):

```python
import hashlib, hmac, time

def verify(raw_body: bytes, header: str, secret: str, tolerance: int = 300) -> bool:
    parts = [p.split("=", 1) for p in header.split(",")]
    t = int(next(v for k, v in parts if k == "t"))
    if abs(time.time() - t) > tolerance:
        return False  # too old: possible replay
    expected = hmac.new(secret.encode(), f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, v) for k, v in parts if k == "v1")
```

Node.js: `crypto.createHmac("sha256", secret).update(`${t}.`).update(rawBody).digest("hex")`,
compared with `crypto.timingSafeEqual`.

Rules for receivers: respond 2xx quickly (do work asynchronously); de-duplicate on the event
`id` (deliveries are at-least-once); events may arrive out of order. Failed deliveries are
retried after 1 min, 5 min, 30 min, 2 h, 6 h and 12 h; an endpoint failing 20 times in a row is
disabled (re-enable it with `PATCH … {"status": "active"}`). Delivery history, manual retry,
test events and secret rotation (both secrets sign for 24 hours) are under
`/businesses/{id}/webhooks/…`. Webhooks are sent after the change is committed — a failing
receiver can never undo an invoice or payment.
