# Roadmap

A phase is complete only when its features are covered by tests that pass on SQLite **and**
PostgreSQL.

| # | Phase | Status |
|---|---|---|
| 1 | Foundation: architecture, repo, SQLite + SQLAlchemy + Alembic, auth, owner, multi-business, users, roles & permissions, audit, outbox | **Backend done** (71 tests). Frontend shell (login, business switcher, member admin) still to do |
| 2 | Products, customers, suppliers, warehouses, inventory ledger | Next |
| 3 | Invoice engine, numbering series, GST, returns, printing | — |
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
* 40 permission codes, 7 built-in roles, custom roles, per-member grant/deny overrides,
  branch scoping, 30-user cap, anti-escalation rules, business-isolation via 404.
* Audit log (per business and owner-wide), transactional outbox, structured errors/logs,
  request ids, DB integrity check and verified online backup.

## Phase 1 — deliberately deferred

* **Frontend** (Next.js) — next step before Phase 2 so the foundation is usable in a browser.
* Mobile/e-mail OTP, Google OAuth, TOTP 2FA, passkeys — need the communication integrations
  (Phase 5) and land in Phase 8 security hardening; the session model already supports them.
* IP-level rate limiting — needs Redis (Phase 7).
* Refresh token in an `HttpOnly` cookie with CSRF protection — added together with the frontend.
