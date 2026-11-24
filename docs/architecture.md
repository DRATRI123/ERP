# Architecture

A private, single-owner ERP for running several independent businesses from one application.
It is **not** multi-tenant SaaS: there is no sign-up, no subscriptions, no super-admin. One owner
account controls everything; up to 30 internal users per business get scoped access.

## 1. Shape of the system

A **modular monolith** in Python holds all financial truth. Two satellite services are added
only where their runtime is a genuine fit, and neither ever writes financial records.

```
                        Cloudflare (TLS, WAF, CDN)
                                   │
                                 Nginx  ── host-header allow-list, per-domain routing
              ┌────────────────────┼─────────────────────────────┐
              │                    │                             │
     Next.js frontend      Python core API (FastAPI)      Node.js realtime (Phase 7)
     (Phase 1b+)           /api/v1/*  ◄── source of truth   WebSockets, webhooks,
                                   │                          integration adapters
                                   │                             ▲
                          SQLite → PostgreSQL                    │ reads outbox / Redis
                          (one DB per installation)              │
                                   │      outbox_events ─────────┘
                                   │
                           Rust workers (Phase 7, only if benchmarks justify)
                           CSV/Excel import, PDF render, barcodes — return
                           results to the Python API; never write ledgers directly
```

| Component | Owns | Never does |
|---|---|---|
| **Python core** (`backend/`) | Auth, permissions, businesses, billing, GST, accounting, inventory, every DB write | — |
| **Node.js realtime** (Phase 7) | WebSocket fan-out, presence, webhook delivery with retries, third-party adapters | Writes business data except via the Python API |
| **Rust workers** (Phase 7) | CPU-heavy transforms: bulk import parsing, PDF/label rendering | Keeps its own financial records |
| **Redis** (Phase 7) | Pub/sub, job queues, rate-limit counters, cache | Acts as a source of truth |

Phase 1 ships only the Python core. Node, Rust, Redis and the frontend are added in their phases
rather than as empty scaffolding.

## 2. Python core layout

```
backend/
  app/
    core/        config, security (Argon2id, JWT), errors, logging, request context
    db/          declarative base, portable types, engine/session, maintenance (backup, checks)
    api/         FastAPI dependencies, schemas, pagination, routes/
    modules/
      access/    permission catalog, roles, memberships, overrides, authorization service
      auth/      sessions, refresh-token rotation, one-time tokens, login history
      businesses/ businesses, branches, financial years
      users/     accounts and lifecycle
      audit/     append-only audit trail
      events/    transactional outbox
      gst/       GSTIN/PAN/state-code validation (grows into the GST engine in Phase 3)
      catalog/   units, categories, brands, products
      parties/   customers & suppliers, groups, addresses
      inventory/ warehouses, stock engine, documents, reports
      numbering/ gap-free document number series
    cli.py       migrate, create-owner, db-check, db-backup
  alembic/       versioned migrations (the only way schema changes)
  tests/         one suite, runs on SQLite and PostgreSQL
```

Each module has `models.py` (tables), `service.py` (business rules, transactions) and is exposed by
a thin router in `api/routes/`. **Rules live in services; routers only translate HTTP.** Routers
call `db.commit()` once at the end of a successful request; anything uncommitted is rolled back.

## 3. Isolation and authorization

* Every business-owned row carries `business_id`. Business-scoped endpoints live under
  `/api/v1/businesses/{business_id}/…` and resolve a `BusinessAccess` object first.
* `BusinessAccess` is built by `access.service.get_business_access`:
  * the **owner** gets every permission in every business (including archived ones);
  * anyone else needs an **active membership** in an **active business** — otherwise the
    response is `404 Business not found`, so the existence of other businesses is not leaked.
* **Effective permissions** = role permissions ∪ granted overrides − denied overrides.
* **Branch scope**: a membership is either `all_branches` or limited to a list of branches.
* **Anti-escalation** (enforced in the service, not the UI): a non-owner can only grant
  permissions and branches they hold, can only manage members whose access is a subset of their
  own, and cannot change their own access. Changing a custom role requires holding both its old
  and new permission sets.
* Permission codes are a closed catalog in code (`access/catalog.py`); the DB stores only codes
  that exist in the catalog. Seven built-in roles are seeded into each business and are
  read-only; custom roles are per business.

## 4. Authentication

* No public registration. The owner is created with `python -m app.cli create-owner`; everyone
  else is invited (one-time token, 72 h) by the owner or a member holding `users.manage`.
* Passwords: Argon2id (64 MiB, t=3, p=2), automatic rehash on login when parameters change,
  policy on length/repetition/email-name.
* **Access token**: 15-minute HS256 JWT carrying `sub` (user) and `sid` (session).
* **Refresh token**: opaque 256-bit random value, stored only as SHA-256, rotated on every use.
  Replaying an already-rotated token revokes the whole session (theft detection).
* The session row is checked on every request, so logout, suspension, password change and
  "sign out other devices" take effect immediately, not when the JWT expires.
* Brute force: per-account lockout after 5 failures for 15 minutes; failed attempts are
  committed even though the request fails. Unknown e-mail and wrong password return the same
  error and comparable timing. IP-level rate limiting is added with Redis in Phase 7/8.
* Login, logout and refresh-reuse events go to `login_events`; administrative actions go to
  `audit_logs`.

## 5. Inventory engine

All stock changes go through `inventory/engine.py` (`StockEngine.post`). Phase 3 sales and
purchase documents will post through the same engine with their own `source_type`.

* **Ledger + balances**: `stock_ledger` is append-only (signed quantity, value at cost, running
  balance). `stock_balances` holds the current quantity/value per product × warehouse × batch
  and always equals the ledger sum (`verify_balances` checks this; every inventory test runs it).
* **Valuation**: weighted average or FIFO, chosen per business and locked once stock exists.
  FIFO keeps `cost_layers` with remaining quantity *and* remaining value, so issuing the last
  unit of a layer takes exactly what is left. Rounding therefore never leaves residue.
  `layer_consumptions` record which layers each issue used.
* **Reversal, not deletion**: cancelling a document posts mirror entries. A reversed issue
  restores the exact layers (with original receipt dates) it consumed; a reversed receipt
  removes exactly its own layers and is refused if they have already been issued.
* **Transfers** issue from the source and receive at the destination with the same value; under
  FIFO the individual layers travel with the stock.
* **Concurrency**: balance rows, cost layers, serials and number-series rows are locked
  (`SELECT … FOR UPDATE` with `populate_existing`, so a session never acts on a stale cached
  copy) in a fixed order to avoid deadlocks. A test fires concurrent issues at the same stock
  on both databases and asserts no overselling and no duplicate document numbers.
* **Batches & serials**: batch-tracked products require a batch on every movement; serial-tracked
  products require exactly one serial per unit, and each serial's movements are recorded.
* **Cost visibility**: purchase prices, unit costs and values are returned only to users with
  `stock_value.view`; everyone else sees quantities.

## 6. Reliability patterns

* **Transactional outbox** – domain events (`business.created`, later `invoice.created`, …) are
  inserted into `outbox_events` in the same transaction as the change. A relay (Phase 7) publishes
  them to Redis/WebSockets/webhooks and marks them published. A failed delivery can never roll
  back a committed invoice or payment, and every event has a stable id for idempotent consumers.
* **Audit trail** – written in the same transaction as the change, with actor, before/after
  diff, IP and request id. Secrets (password hashes, tokens) are never included.
* **Structured errors** – `{"error": {"code", "message", "details", "request_id"}}` for every
  failure; every response carries `X-Request-ID`.
* **Structured logs** – one JSON line per request with method, path, status, duration and
  request id.

## 7. Database strategy (SQLite first, PostgreSQL ready)

See [database-schema.md](database-schema.md) for the schema itself.

* SQLAlchemy 2.x ORM + Alembic for all schema changes; the test suite builds its schema from
  the migrations and asserts the models and migrations have no drift.
* Portable types only:
  * `Uuid` primary keys (native `uuid` on PostgreSQL, 32-char text on SQLite);
  * `UTCDateTime` — always timezone-aware UTC in Python;
  * `ScaledDecimal` / `Money` (18,4) / `Quantity` (18,3) — exact decimals. SQLite would
    silently store `NUMERIC` as binary floating point, so there the value is stored as a scaled
    64-bit integer and `SUM()` stays exact; PostgreSQL uses native `NUMERIC(p,s)`. Floats are
    rejected outright;
  * `str_enum` — enums stored as VARCHAR + CHECK constraint (no PostgreSQL native ENUM types,
    which are hard to migrate).
* Deterministic constraint names via a naming convention (needed for SQLite batch migrations).
* SQLite runtime settings: WAL, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`.
  Requests that may write (POST/PATCH/PUT/DELETE, CLI) open `BEGIN IMMEDIATE`, so concurrent
  writers queue instead of failing on a lock upgrade; GET requests use a deferred `BEGIN`, so
  readers never block. SQLite still serializes writes — fine for a few dozen users, and the
  reason for the PostgreSQL phase.
* Migrations on SQLite pause foreign-key enforcement while batch operations rebuild tables,
  then run `PRAGMA foreign_key_check` and refuse to commit if anything is broken. A test
  upgrades a database holding Phase 1 data to prove upgrades work on real data, not just on
  empty schemas.
* Backups: `python -m app.cli db-backup` uses SQLite's online backup API (safe while running)
  and integrity-checks the copy before reporting success.
* The same test suite runs against PostgreSQL by setting `ERP_TEST_DATABASE_URL`; CI runs both.

### Migration to PostgreSQL (Phase 9)

A dedicated utility will: back up SQLite → create the PostgreSQL schema from the same Alembic
migrations → copy table by table in foreign-key order through the ORM types (which converts the
scaled-integer money columns back to `NUMERIC`) → verify row counts, primary keys, foreign keys,
decimal sums per column, trial balance, stock balances, customer outstanding and invoice
totals → run the test suite against the new database → cut over in a maintenance window.
No dual writes.

## 8. API conventions

* REST + JSON under `/api/v1`, OpenAPI 3 at `/api/v1/openapi.json`, docs at `/api/v1/docs`.
* `limit`/`offset` pagination returning `{items, total, limit, offset}`.
* Validation errors are `422` with field locations; permission problems are `403`;
  inaccessible businesses and cross-business ids are `404`.
* Planned for Phase 6: scoped API keys for external apps, idempotency keys on create
  endpoints, per-key rate limiting, HMAC-signed webhooks.
