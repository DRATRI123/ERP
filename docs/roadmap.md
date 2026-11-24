# Roadmap

A phase is complete only when its features are covered by tests that pass on SQLite **and**
PostgreSQL.

| # | Phase | Status |
|---|---|---|
| 1 | Foundation: architecture, repo, SQLite + SQLAlchemy + Alembic, auth, owner, multi-business, users, roles & permissions, audit, outbox | **Backend done**. Frontend shell still to do |
| 2 | Products, customers, suppliers, warehouses, inventory ledger | **Backend done** (105 tests in total). Frontend screens still to do |
| 3 | Invoice engine, numbering series, GST, returns, printing | Next |
| 4 | Double-entry accounting, payments, banking, financial reports | — |
| 5 | Integrations: WhatsApp, Gmail/SMTP, SMS, payment gateways, Google, Shopify | — |
| 6 | Developer platform: API keys & scopes, webhooks, idempotency, rate limits | — |
| 7 | Node.js realtime + webhook delivery, Redis, Rust workers (where benchmarks justify) | — |
| 8 | Custom domains, SSL, encryption at rest, monitoring, backups, deployment | — |
| 9 | SQLite → PostgreSQL migration utility and cutover | — |
| 10 | Load/security testing, restore drills, production docs | — |

## Phase 1 — what exists

* Owner account (CLI only), invitations, accept-invite, password reset tokens, change password.
* Login with Argon2id, account lockout, JWT access tokens + rotating refresh tokens with reuse
  detection, session list/revoke, login history.
* Businesses (create/edit/archive/restore), auto-created head office, financial year and roles.
* Branches, financial years, GSTIN/PAN/state-code validation.
* 42 permission codes (two added in Phase 2), 7 built-in roles, custom roles, per-member grant/deny overrides,
  branch scoping, 30-user cap, anti-escalation rules, business-isolation via 404.
* Audit log (per business and owner-wide), transactional outbox, structured errors/logs,
  request ids, DB integrity check and verified online backup.

## Phase 2 — what exists

* Units (12 GST-UQC defaults per business), nested categories, brands.
* Products: goods/services, SKU + barcode (mutually unique, scanner lookup), HSN/SAC
  validation, purchase/retail/wholesale price and MRP, tax-inclusive flag, GST and cess rates,
  reorder level/quantity, custom attributes, batch / serial tracking, archive/restore.
  Tracking fields lock once stock has moved.
* Customers and suppliers (one party can be both, needing both permissions), GSTIN-derived
  state and PAN, GST registration type, groups, credit limit/days, billing & shipping
  addresses with defaults, search by name/phone/GSTIN/code/e-mail.
* Warehouses linked to branches (a `MAIN` warehouse is created with every business); branch
  scoping applies to warehouses, stock and documents.
* Stock engine: append-only ledger, balances, weighted average **or** FIFO per business
  (locked once stock exists), exact Decimal values with no rounding residue, row locking so
  concurrent issues never oversell, gap-free document numbers per financial year.
* Documents: opening stock, adjustments (with reasons), transfers (FIFO layers travel with the
  stock), physical counts (post only the differences), cancellation by exact reversal.
* Batches with mfg/expiry dates, serial numbers per unit with history.
* Reports: balances, movement history, valuation by warehouse, low stock, expiring batches,
  serial lookup. Costs and values are hidden from users without `stock_value.view`.
* Events: `product.created/updated/deleted`, `customer.created/updated/deleted`,
  `stock.updated`, `stock.transferred`, `stock.low` (only when crossing the reorder level).

## Phase 2 — deliberately deferred

* **Price lists**, customer/supplier **outstanding balances** and **transaction history** —
  they need invoices and payments (Phases 3–4).
* **Attachments and product images** — come with secure file storage (Phase 8).
* Negative stock is never allowed. A per-business "allow negative billing" option can be added
  with the invoice engine if needed.
* Back-dated movements are valued at posting time (perpetual inventory); they do not
  retroactively revalue later issues.

## Phase 1 — deliberately deferred

* **Frontend** (Next.js) — next step before Phase 2 so the foundation is usable in a browser.
* Mobile/e-mail OTP, Google OAuth, TOTP 2FA, passkeys — need the communication integrations
  (Phase 5) and land in Phase 8 security hardening; the session model already supports them.
* IP-level rate limiting — needs Redis (Phase 7).
* Refresh token in an `HttpOnly` cookie with CSRF protection — added together with the frontend.
