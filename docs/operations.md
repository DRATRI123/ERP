# Operations runbook

The daily running of the ERP after [deployment](deployment.md). Commands run from
`/opt/erp/deploy`, with `docker compose exec api python -m app.cli` abbreviated to **`erp`**
(`alias erp='docker compose exec api python -m app.cli'`).

## Routine

| When | Task | How |
|---|---|---|
| Daily (automatic) | Encrypted backup, verified, pruned (7 daily / 4 weekly / 12 monthly), copied off-site | worker; see `/system/backups` |
| Daily (automatic) | Custom domains re-verified | worker |
| Monthly (automatic) | **Restore drill**: latest backup restored into a scratch database, books and stock re-checked | worker; result on `/system/health` |
| Daily, 1 minute | Look at `/system/health` (or the alert from your uptime monitor) | `"status": "ok"`, otherwise read `warnings` |
| Weekly | Download one backup to another machine and run `erp verify-backup <file>` there | off-site copy you control |
| Monthly | Review the owner audit log and members list; remove people who left | `/audit-logs`, `/members` |
| Monthly | `sudo apt update && sudo apt upgrade`; `git pull && docker compose up -d --build` | §9 of deployment.md |
| Quarterly | Practise a full restore on a spare VPS following [§6](deployment.md#6-backups-and-restore) | proves the off-site copy and the key |
| Yearly | Year-end closing per business; consider rotating `ERP_ENCRYPTION_KEYS` | `POST /accounting/close-year`, `erp rotate-encryption` |

## When something is wrong

| Warning on /system/health | Meaning | Action |
|---|---|---|
| *The background worker is not running* | messages, webhooks and backups are paused | `docker compose ps worker`; `docker compose logs --tail 100 worker`; `docker compose up -d worker` |
| *No successful backup in the last 26 hours* / *Last backup failed: …* | backups are failing | read the error; usual causes: disk full (`df -h`), `ERP_BACKUP_KEY` missing, Google token expired (reconnect the integration). Then `erp backup` |
| *Last restore drill failed* | the latest backup could not be restored or its books did not verify | **treat as urgent**: `erp restore-drill` to see the problem; keep older backups (raise `keep_daily`) until it passes |
| *Queue … waiting for over 15 minutes* | a provider is down or credentials expired | `/integrations/{id}/logs`; fix and retry the message |
| *n message(s) failed in the last 24 hours* | e-mail/WhatsApp/SMS gave up after retries | `/messages?failed_only=true`, `POST /messages/{id}/retry` |
| *custom domain(s) disabled* | the TXT record disappeared | restore the DNS record, then `PATCH /system/domains/{id} {"enabled": true}` and verify |
| *disk check failing* | under `ERP_MIN_FREE_DISK_MB` free | delete old images (`docker image prune`), lower backup retention, or grow the disk |
| Users see "The server is busy" (503) | sustained load beyond capacity | check `ERP_API_WORKERS` matches the vCPUs; see [performance.md](performance.md); consider PostgreSQL |

## Incidents

**Suspected account compromise.** Sign in as the owner:
1. Suspend the member, which ends their sessions and API keys at once.
2. Check `/audit-logs?actor_user_id=…` and the member's login history.
3. Reverse wrong entries with cancellations or reversals. History is never deleted.
4. After an interview, re-invite the member and ask them to turn on 2FA.

**Owner lost the 2FA phone and the recovery codes.** On the server:
`erp reset-mfa --email you@example.com`, then sign in and set up 2FA again.

**Server lost.** Follow these steps:
1. Provision a new VPS following deployment.md §1–4.
2. Copy the latest backup from Drive or your own copy into the `erp-data` volume.
3. Run `erp restore <file> --force`, then `erp migrate`.
4. Point DNS at the new server.

The time this takes is measured by your quarterly practice.

**Leaked secret key.**
1. Change `ERP_SECRET_KEY`. Everyone is signed out.
2. Rotate `ERP_ENCRYPTION_KEYS`: prepend a new key and run `erp rotate-encryption`.
3. Re-issue integration credentials at the providers.
4. Rotate `ERP_TLS_ASK_TOKEN` and `ERP_METRICS_TOKEN`.

**Planned maintenance.** `erp maintenance on --message "…"` pauses all changes (viewing still
works). `erp maintenance off` resumes them.

## Command reference (`python -m app.cli …`)

| Command | Purpose |
|---|---|
| `migrate` | apply database migrations |
| `create-owner` | the one owner account (first run only) |
| `check-config` | production settings and PostgreSQL connection budget |
| `db-check` | database integrity check |
| `worker [--once]` | background jobs |
| `backup` / `verify-backup FILE` / `restore FILE [--target URL] [--force]` | encrypted backups |
| `restore-drill` | restore the latest backup into scratch and verify the books |
| `backup-keygen` | new `ERP_BACKUP_KEY` |
| `maintenance on\|off\|status` | pause or resume changes |
| `migrate-to-postgres --target URL [--dry-run]`, `compare-databases` | PostgreSQL move |
| `reset-mfa --email` | switch off 2FA for a locked-out user |
| `rotate-encryption` | re-encrypt stored secrets after adding a key |
| `accounting-backfill` | post journals for pre-Phase-4 data; re-verify books |
