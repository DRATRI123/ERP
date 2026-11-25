# Integrations

Integrations are configured per business under `/api/v1/businesses/{id}/integrations`
(permission `integrations.manage`). Secrets are encrypted at rest (Fernet, `ERP_ENCRYPTION_KEYS`),
write-only through the API, and never logged or audited. Every external call and inbound
webhook is recorded in the integration's log.

## Before connecting anything (server)

1. Set `ERP_ENCRYPTION_KEYS` (see `.env.example`) and `ERP_PUBLIC_BASE_URL` (your HTTPS URL —
   webhook and OAuth callback URLs are built from it).
2. Run the message worker next to the API: `python -m app.cli worker` (systemd/Docker service).
   E-mails, WhatsApp and SMS are queued by the API and sent by the worker with retries
   (1 min, 5 min, 30 min, 2 h, 6 h), so a slow provider never blocks billing.
3. Key rotation: prepend a new key to `ERP_ENCRYPTION_KEYS`, run
   `python -m app.cli rotate-encryption`, then remove the old key.

## Verification status

These connectors are written against each provider's documented API and tested against
simulated responses (request shapes, auth headers, real HMAC signatures, pagination, retries).
They have **not yet been exercised against the live services** — this development environment
cannot reach them and holds no credentials. Use each provider's **test/sandbox mode first** and
the `/test` endpoint after connecting.

## Providers

| Provider | What it does | You need |
|---|---|---|
| `smtp` | E-mail invoices (PDF attached) | SMTP host/port, username, password. Gmail/Workspace: an **App Password**. Microsoft 365: `smtp.office365.com:587` with SMTP AUTH enabled for the mailbox |
| `google` | Gmail sending; database backups to Google Drive | Google Cloud project with Gmail API + Drive API enabled, OAuth client (Web), Authorized JavaScript origin = the address the ERP is opened at (e.g. `http://localhost:3000` or `https://erp.example.com`); Authorized redirect URI = that address + `/api/v1/hooks/google/oauth/callback`. Both are shown (copyable) in the connect dialog and via `GET /integrations/google-setup?origin=`. The origin is used only if this server answers to it (localhost, `ERP_PUBLIC_BASE_URL`, `ERP_ALLOWED_HOSTS`, `ERP_CORS_ORIGINS`); otherwise `ERP_PUBLIC_BASE_URL`. The redirect URI travels in the signed state, so the token exchange uses the same one |
| `whatsapp_cloud` | WhatsApp invoices (PDF) + delivery/read receipts | Meta Business account, WhatsApp phone number ID, permanent access token, app secret, an **approved Utility template** with a DOCUMENT header and body variables {{1}} name, {{2}} number, {{3}} amount. Webhook URL + verify token from the integration |
| `msg91` | SMS invoice notifications | MSG91 auth key and a **DLT-approved** flow with variables `name`, `number`, `amount`, `link`. `POST …/test-sms?to=` sends one real SMS |
| `razorpay` | Payment links; paid links become receipts automatically | Key ID/secret, webhook secret; webhook event `payment_link.paid` to the integration's webhook URL; settlement bank account from your chart |
| `cashfree` | Payment links; paid links become receipts automatically | App ID, secret key, environment (`sandbox`/`production`); payment-link webhook to the integration's URL |
| `shopify` | Import products (new SKUs) and orders → sales invoices (paid orders get receipts) | Custom app with `read_orders`, `read_products`; Admin API token and API secret; webhooks `orders/create`, `orders/paid` |

Payment receipts are created **only** from signature-verified webhooks — never from a browser
redirect — and each gateway payment is applied exactly once. A payment for an unknown link is
recorded for review, not applied.

## Data

* `POST /data/import/{products|customers|suppliers}` — CSV or Excel; all rows are validated and
  nothing is written unless every row is valid (`dry_run=true` only validates).
* `GET /data/export/{products|customers|suppliers|sales-register|purchase-register}` — Excel or CSV.
* `GET /data/export-tally` — ledger masters and journal vouchers in Tally's XML import format
  (Gateway of Tally → Import → Vouchers). Customers/suppliers become ledgers under Sundry
  Debtors/Creditors.

## Not yet built

PhonePe, PayU, WooCommerce, Shiprocket, Outlook via Microsoft Graph, Google Sheets/Contacts/
Calendar, Tally *import*, pushing stock levels to Shopify, and the no-code automation builder
(e.g. "send every invoice on WhatsApp automatically") — the queue and connectors it needs exist.

## Disconnect and remove

* `POST /integrations/{id}/disconnect` works from any state (pending, error, connected, already
  disconnected), deletes the stored secrets and, for Google, revokes the refresh token at Google
  (best effort — an offline Google never blocks it).
* A disconnected integration can be reconnected with `POST /integrations/{id}/credentials`.
* `DELETE /integrations/{id}` disconnects if needed and deletes it. When messages, payment links
  or gateway payments refer to it, it is hidden instead (`removed_at`, migration 0027) and its
  name freed so the same name can be connected again.
* The Google callback always answers with a readable page (cancelled, not a test user, expired
  link, redirect URI mismatch) with a link back to Integrations, never raw JSON.
