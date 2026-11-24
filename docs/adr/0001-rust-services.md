# ADR 0001 — No Rust service yet (measured)

**Status:** accepted (Phase 7) · **Revisit:** when a trigger below is hit

## Context

The plan allows Rust for CPU-heavy work — bulk imports, PDF/document processing, barcode work,
heavy reporting — *only where benchmarks show a measurable benefit*, and Rust may never keep
its own financial records. `backend/scripts/benchmark.py` measures those workloads on the real
code paths (SQLite, one process, this development machine):

| Workload | Result | Dominated by |
|---|---|---|
| Parse + read a 100,000-row CSV (13 MB) | 0.25 s | — |
| Import 5,000 products end to end | 8.8 s (1.8 ms/row) | DB writes, validation, audit, events |
| Create + finalize an invoice via the API | 54 ms | 2 HTTP calls, stock, GST, journal posting |
| Trial balance (~600 journals) / GSTR-1 (300 invoices) | 0.03 s / 0.31 s | SQL |
| One invoice PDF | 0.5–1.4 s | **Chromium start-up** |
| 50 invoices as one batch PDF | 1.1 s (23 ms each) | — |

## Decision

Do not introduce a Rust service.

* Parsing is 3% of import time; the rest is business rules and database writes, which must stay
  in Python (the single owner of financial records). A Rust parser would save ~0.25 s per 100k rows.
* The real bottleneck — PDF generation — was Chromium start-up, not computation. It was fixed in
  Python by rendering batches in one Chromium run (`POST /documents/print-batch`): 25–60× faster.
* Reports run in SQL; moving them to Rust would add a second data-access path for no gain.

A Rust service would add a toolchain, a deployment unit and a service contract without a
measured benefit — exactly what the architecture rules forbid.

## Triggers to revisit

* Imports regularly larger than ~500k rows where parsing (not DB writes) exceeds ~5 s.
* Label/barcode sheets of thousands of items per run where rendering, not I/O, dominates.
* Report endpoints over 2 s at real data volumes that SQL indexing/aggregation cannot fix.
* PDF throughput needs beyond batch rendering (e.g. hundreds of individual PDFs per minute).

Re-run `python scripts/benchmark.py` against production-sized data before deciding.
