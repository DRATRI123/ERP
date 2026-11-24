# Roadmap

A phase is complete only when its features are covered by tests that pass on SQLite **and**
PostgreSQL.

| # | Phase | Status |
|---|---|---|
| 1 | Foundation: architecture, repo, SQLite + SQLAlchemy + Alembic, auth, owner, multi-business, users, roles & permissions, audit, outbox | **Backend done**. Frontend shell still to do |
| 2 | Products, customers, suppliers, warehouses, inventory ledger | **Backend done**. Frontend screens still to do |
| 3 | Invoice engine, numbering series, GST, returns, printing | **Backend done** (150 tests in total). Visual designer UI still to do |
| 4 | Double-entry accounting, ledgers, banking, financial reports | Next |
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

## Phase 3 — what exists

* **11 document types** in one engine: quotation, estimate, proforma, sales order, delivery
  challan, sales invoice (tax invoice / bill of supply / export), credit note (sales return),
  debit note, purchase order, purchase invoice, purchase return.
* Draft → final → cancelled lifecycle. Drafts are freely editable; finalizing atomically
  assigns the number, freezes buyer/seller details, posts stock and takes counter payments.
  Finalized documents are immutable; cancellation reverses stock exactly.
* **Conversions** with line links: quotation → order → invoice, challan → invoice (stock moves
  only once), invoice → credit/debit note, PO → purchase invoice → purchase return. Returns and
  challan invoicing are capped by what remains on the source lines.
* **GST engine**: CGST+SGST / UTGST / IGST by place of supply, exports & SEZ, cess,
  tax-inclusive and exclusive pricing (inclusive totals stay exact), percent and flat
  discounts, exempt / nil-rated / non-GST / zero-rated lines, reverse charge (tax shown, not
  collected), composition and unregistered sellers (bill of supply), round-off to the rupee.
  **Versioned HSN rates** with effective dates override product rates.
* **Purchases**: supplier invoice numbers (duplicates blocked), ITC eligibility per line
  (non-claimable tax is added to stock cost), landed cost allocated by value.
* **Payments**: receipts and payments with allocation to many documents, partial payments,
  advances, refunds against credit notes / purchase returns, payment status, due dates from
  credit days, overdue filter, party outstanding, credit-limit enforcement (with an override
  permission), payment cancellation.
* **Numbering**: configurable series per document type and branch with `{FY}`, `{FYS}`,
  `{BRANCH}` placeholders, yearly reset, gap-free under concurrency, GST 16-character rule.
* **Printing**: templates per business / branch / document type; A4, A5, A6, 58 mm, 80 mm and
  custom paper; colours, fonts, columns, header/footer, terms, bank details, watermark, logo,
  signature and stamp images, UPI payment QR, e-invoice QR, amount in words (lakh/crore),
  multiple copies. HTML print view and PDF (headless Chromium), both locked down with a CSP.
* **GST outputs** (prepared, never claimed as filed): sales & purchase registers, HSN summary
  (B2B/B2C), GSTR-1 (B2B, B2CL, B2CS, exports, CDNR, CDNUR, nil, HSN, documents issued),
  GSTR-3B (3.1, 4, 5), e-invoice JSON (IRP schema 1.1), and recording of IRNs issued by the IRP.
* `client_ref` makes document creation idempotent (offline devices, external apps); a preview
  endpoint prices a bill without saving it.

## Phase 3 — deliberately deferred

* **Direct GSP/IRP and e-way bill API calls** need an authorised GSP account and credentials —
  they arrive as connectors in Phase 5. Until then the e-invoice JSON is uploaded on the portal
  and the returned IRN recorded here.
* **Drag-and-drop invoice designer** is a frontend feature; its backend (template config,
  rendering, preview) is done.
* **Price lists, salesperson commission reports, GSTR-2B reconciliation, purchase approvals,
  GRN** — next to the accounting work in Phase 4/5.
* Cancelling a GST invoice is allowed until an IRN exists. After filing, corrections should be
  made with credit/debit notes.

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
