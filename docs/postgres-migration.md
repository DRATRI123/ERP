# Moving from SQLite to PostgreSQL

SQLite is the right start for a few businesses on one server. Move to PostgreSQL when you
want a separate database server, point-in-time recovery, heavier concurrent use or more than one
API server. The move is a one-off copy in a short maintenance window. Nothing runs against both
databases at once. The SQLite file is never modified, so going back is just switching the URL.

## What the utility guarantees

`python -m app.cli migrate-to-postgres` does the following:

1. **Checks the source.** It must be at the latest schema revision and pass SQLite's
   `integrity_check` and `foreign_key_check`.
2. **Builds the PostgreSQL schema with the same Alembic migrations** as every other install. The
   target database must be empty.
3. **Copies every table, parents first, through the application's column types.**
   * Money stored as scaled integers on SQLite becomes exact `NUMERIC`.
   * Hex UUIDs become `uuid`.
   * Timestamps become `timestamptz`.
   * Self-references (reversal entries, sub-categories, the account tree, document references)
     are filled in after their rows exist.
4. **Verifies everything inside the still-open target transaction.**
   * Per-table row counts.
   * A SHA-256 checksum over every column of every row, compared with the source.
   * Double-entry integrity of each business's books.
   * The stock ledger against stock balances.
5. **Commits only if every check passes.** Otherwise nothing is written.

`--dry-run` performs steps 1–4 and then rolls back. This is a full rehearsal against the real
target that changes nothing. Run it a day before the cutover, while the ERP is in normal use.

Measured on the development machine: about **5,700 rows/s** including all checks (23,554 rows in
4.1 s). A database with a million rows takes about 3 minutes.

## Runbook (Docker Compose deployment)

```bash
cd /opt/erp/deploy

# 0. Day before: rehearse. Start PostgreSQL and check the copy works.
#    Put POSTGRES_PASSWORD in .env first.
docker compose --profile postgres up -d postgres
PG='postgresql+psycopg://erp:<POSTGRES_PASSWORD>@postgres:5432/erp'
docker compose run --rm api python -m app.cli migrate-to-postgres --target "$PG" --dry-run

# 1. Safety backup (encrypted, verified).
docker compose exec api python -m app.cli backup

# 2. Pause changes. Reads and sign-in keep working, the worker pauses.
docker compose exec api python -m app.cli maintenance on --message "Upgrading the database, back by 10:30"

# 3. Copy and verify. Keep the report.
docker compose run --rm -v "$PWD:/out" api python -m app.cli migrate-to-postgres \
    --target "$PG" --report /out/migration-report.json

# 4. Switch: in .env set ERP_DATABASE_URL=$PG, then restart on PostgreSQL.
docker compose --profile postgres up -d
docker compose exec api python -m app.cli db-check
docker compose exec api python -m app.cli maintenance off    # now runs against PostgreSQL

# 5. Check: /api/v1/system/health is "ok", the trial balance matches, a test invoice works.
```

If step 3 prints `Verification FAILED`, nothing was written. Run `maintenance off` and the ERP
continues on SQLite. Keep the report and investigate.

**Rollback after the switch.** Put the SQLite URL back in `.env` and restart. Only changes made
after the switch would be missing. Keep the SQLite file and the backup from step 1 for at least
a month.

Maintenance mode is copied along with the data, so PostgreSQL starts in maintenance mode too.
That is why `maintenance off` in step 4 is run *after* switching: changes resume only once you
have checked the new database.

## Other commands

* `python -m app.cli compare-databases --source <url> --target <url>` checks two databases hold
  identical data (row counts and checksums per table). It is useful any time after the move.
* `PUT /api/v1/system/maintenance {"enabled": true, "message": "…"}` lets the owner switch
  maintenance mode from the API. `GET` shows the current state.
* The same copy engine also works from PostgreSQL to SQLite. The test suite runs both directions.
  The CLI only offers SQLite → PostgreSQL.

## After the move

* Backups switch to `pg_dump` automatically. The worker and the `backup` command detect the
  dialect, and the image includes `postgresql-client`.
* Consider WAL archiving for point-in-time recovery, which is not set up by this project yet.
