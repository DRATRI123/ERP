# Security

This ERP holds the money, stock and customer data of the owner's businesses. This page lists
the threats considered, the controls in place, and how each control is verified.

## Threat model

| Threat | Main controls |
|---|---|
| Stolen or guessed passwords | Argon2id; per-account lockout; per-IP limits; TOTP two-factor sign-in with recovery codes; new-device alerts |
| Stolen tokens | 15-minute access tokens; rotating refresh tokens with reuse detection; server-side session check on every request; optional httpOnly SameSite=strict cookie + CSRF token |
| Staff seeing or changing what they shouldn't | 42 permissions, roles, branch scoping, enforced on the server for every route; API keys limited to their scopes ∩ their creator's current permissions |
| One business's data leaking into another | every business-scoped query filtered by the business; other businesses answer **404**, so their existence is not revealed |
| Injection (SQL, HTML, template) | SQLAlchemy bound parameters only; Jinja2 autoescape; print templates rendered with JavaScript disabled and a CSP that blocks network access |
| Forged webhooks | HMAC verification per provider, constant-time comparison, replay storage |
| SSRF via outgoing webhooks | URL and resolved-IP checks (no private, loopback or link-local addresses) |
| Host-header attacks, rogue domains | Host allow-list (421); certificates only for verified domains |
| Clickjacking, content sniffing, XS-leaks | strict CSP (`default-src 'none'`), `X-Frame-Options: DENY`, `nosniff`, COOP/CORP, Permissions-Policy, HSTS |
| Server compromise exposing secrets | integration credentials and TOTP secrets encrypted (Fernet, rotatable); API keys, refresh tokens and recovery codes stored only as hashes; backups AES-256-GCM encrypted under a separate key |
| Data loss | daily encrypted, verified backups; GFS retention; off-site copy; automated monthly restore drills |
| Tampering with history | append-only stock ledger and journals (corrections are reversals); audit log of every change |
| Denial of service | rate limits; bounded password hashing; load shedding with 503; request size limits at Caddy |

## Verification

| Check | Where |
|---|---|
| **Route inventory**: only 16 named routes are reachable without credentials. Any new public route fails the suite. | `tests/test_security_audit.py` |
| **Every protected route** (220+) answers 401 to: no token, `alg: none`, wrong key, expired, MFA-challenge token, revoked session, bad API key, garbage | same |
| **Cross-business**: an administrator of business A gets 404 on *every* route of business B, reads and writes alike | same |
| **Least privilege**: a view-only member gets 403/404 on *every* write route of their own business | same |
| **Owner-only** routes refuse business administrators | same |
| **Hostile input**: SQL, HTML/JS, path traversal, NUL, very long and template strings on every list endpoint and on sign-in never cause a 500 | same (SQLite and PostgreSQL) |
| **No leaks**: errors carry no internals; no response contains password hashes, TOTP secrets, refresh tokens or stored credentials | same |
| Static analysis (Bandit rules via ruff `S`) | CI `quality` job |
| Dependency vulnerabilities (`pip-audit`, `npm audit`) | CI `quality` job |
| Feature-level security tests: 2FA, lockout, CSRF, headers, domains, backups, webhooks, SSRF | `test_security_ops.py`, `test_auth.py`, `test_integrations.py`, `test_developer.py` |

## Findings from the Phase 10 review (all fixed)

1. **Permission check after body parsing.** 11 write routes resolved the exact permission
   inside the handler, for example sales vs purchase documents. A member without any relevant
   permission therefore got 422 for a malformed body instead of 403. No data was exposed: the
   service still refused valid requests. These routes now also have an up-front "any of these
   permissions" dependency (`require_any`). Reopening a closed year now uses an owner-only
   dependency.
2. **NUL characters caused a 500 on PostgreSQL.** PostgreSQL rejects NUL in text. NUL is now
   refused at the edge with 422 `invalid_characters` on every database.
3. **500 responses lost their request id.** Unhandled errors are now answered inside the request
   middleware, so the id matches the logs.
4. **Vulnerable dependency.** `ws` 8.18.0 in the real-time service had two high-severity
   advisories (GHSA-58qx-3vcg-4xpx, GHSA-96hv-2xvq-fx4p). Upgraded to 8.22.2.
5. **Calendar date taken from the server clock.** Docker servers run in UTC, so invoices raised
   in India between 00:00 and 05:30 defaulted to the previous day. Every "today" now uses the
   business time zone (`ERP_TIMEZONE`, default Asia/Kolkata).
6. **Memory exhaustion risk.** Parallel Argon2 hashes are now limited to one per CPU core.

### Reviewed and accepted

* `assert` statements: all 15 are type-narrowing invariants. If removed by `python -O`, the next
  line fails loudly and the transaction rolls back. None guards authorization or amounts. The
  images do not run with `-O`.
* `subprocess`: only `pg_dump`/`pg_restore` and Chromium, with argument lists, no shell, and
  inputs from configuration.
* One dynamic SQL string (`transfer._reset_sequences`): its identifiers come from the database
  catalogue and are quoted.

## Operating securely

* Turn on 2FA for the owner first. Ask staff who handle money to turn it on too.
* Keep `ERP_SECRET_KEY`, `ERP_ENCRYPTION_KEYS` and `ERP_BACKUP_KEY` only in `deploy/.env`
  (`chmod 600`). Keep the backup key off the server as well.
* Rotation: prepend a new Fernet key and run `rotate-encryption`. Changing `ERP_SECRET_KEY`
  signs everyone out.
* Offboarding: suspend the member. Their sessions and API keys stop working immediately.
* Watch `/system/health` and security alerts. Review the audit log for unusual changes.

## Reporting

This is a private system. Security issues go to the owner directly; do not open public issues.
