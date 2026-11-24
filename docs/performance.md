# Performance and capacity

Measured on the development machine: 4 cores, with the load generator running on the same
machine, so the figures are conservative. Reproduce with `python backend/scripts/loadtest.py`.
CI runs a 30-user smoke version of the same test.

## The load test

`scripts/loadtest.py` starts the real server (uvicorn, production settings) on a fresh database.
It creates 3 businesses, each with 40 stocked products, 20 customers and its share of the
staff. Every user then signs in and repeats a mix of actions:

| Share | Action |
|---|---|
| 30 % | create an invoice (1–4 lines) and finalize it with a UPI payment |
| 25 % | search products |
| 15 % | stock balances |
| 12 % | list invoices, then open the print view of one |
| 8 % | trial balance |
| 5 % | day book |
| 5 % | GSTR-1 |

After the run it checks:

* no server errors;
* every business's books balance and its stock ledger matches stock balances;
* invoice numbers are unique and gapless in every series;
* for every product, the drop in stock on hand equals the quantity sold, so no sale was lost
  or counted twice under concurrency.

**All four runs below passed every check.**

## Results (90 users = 3 businesses × 30, the specification's maximum; 4 API processes)

**Realistic**: each user acts every ~5 s (a busy counter).

| | SQLite | PostgreSQL |
|---|---|---|
| Invoices per minute | 293 | 276 |
| Create invoice p50 / p95 | 26 / 373 ms | 44 / 217 ms |
| Finalize (stock, GST, journals, payment) p50 / p95 | 94 / 276 ms | 160 / 470 ms |
| Product search p50 / p95 | 15 / 88 ms | 24 / 148 ms |
| Trial balance p50 / p95 | 20 / 104 ms | 27 / 271 ms |
| GSTR-1 p50 / p95 | 49 / 178 ms | 60 / 327 ms |
| Errors | 0 | 0 |

**Stress**: each user acts every ~0.5 s, about 10 times the realistic load.

| | SQLite | PostgreSQL |
|---|---|---|
| Requests per second | 36 | 55 |
| Invoices per minute | 441 | 686 |
| Create invoice p50 / p95 | 2.6 / 18 s | 0.17 / 2.3 s |
| Product search p50 / p95 | 41 / 160 ms | 110 / 1,955 ms |
| Answered "busy, retry" (503) | 5 of 2,363 | 0 |
| Errors | 0 | 0 |

Sign-in takes several seconds in every run only because all 90 users sign in during the same
second. Argon2 password hashing (64 MiB each) is limited to one per CPU core so that a burst of
sign-ins cannot exhaust memory.

## What the numbers mean

* **SQLite comfortably serves the specified maximum** at realistic use. Reads stay fast even
  under stress, because WAL lets readers proceed without waiting. Writes queue, because SQLite
  has one writer at a time.
* **Move to PostgreSQL** (see [postgres-migration.md](postgres-migration.md)) when sustained
  invoicing approaches a few hundred invoices per minute, or when you need more than one server.
  Under stress it handles about 1.5 times the write volume, with far lower write latency.
* Use **one API process per CPU core** (`ERP_API_WORKERS`). A single process is limited to one
  core: in testing, 1 process at the realistic load timed out where 4 processes had p50 latencies
  of 15–94 ms.

## Problems found by load testing, and fixed

| Symptom under load | Cause | Fix |
|---|---|---|
| `database is locked` on sign-in | Argon2 verification ran while holding the SQLite write lock | verify in a read transaction; take the write lock only for bookkeeping |
| Writers starved, then timed out | SQLite's busy handler polls with back-off, so waiting writers are served unfairly | in-process writer queue before `BEGIN IMMEDIATE`; busy timeout 15 s |
| Connection timeouts at 90 users | pool of 15 connections for 40 request threads (SQLite) | SQLite pool = request threads (40) |
| PostgreSQL refused connections | 4 processes × 40 connections exceeded `max_connections = 100` | pool 8 + 7 per process, a 10 s wait, `check-config` verifies the total |
| Overload surfaced as 500 | lock/pool timeouts were unhandled | answered as **503 `database_busy`** with `Retry-After`; the transaction is rolled back, so retrying is safe |
| Memory spike risk on sign-in bursts | 40 parallel Argon2 hashes × 64 MiB | hashing limited to the number of CPU cores |

## Single-operation benchmarks

Single-operation benchmarks (`scripts/benchmark.py`) are in
[ADR 0001](adr/0001-rust-services.md). The PostgreSQL migration copies about 5,700 rows per
second including verification.

## Metrics with several processes

HTTP counters in `/metrics` are kept per process. With several API processes, each scrape sees
one process's counters. Queue, backup and worker gauges are read from the database and are
always complete.
