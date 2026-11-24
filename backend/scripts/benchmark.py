"""Benchmarks for the workloads where a Rust service was proposed (bulk import, PDF,
stock posting, reporting). Run: python scripts/benchmark.py  (uses a throwaway SQLite DB)."""

from __future__ import annotations

import csv
import io
import os
import secrets
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="erp-bench-"))
os.environ.update({"ERP_ENV": "test", "ERP_SECRET_KEY": secrets.token_urlsafe(48),
                   "ERP_DATABASE_URL": f"sqlite:///{tmp / 'bench.db'}",
                   "ERP_PDF_CHROMIUM_PATH": os.environ.get("ERP_PDF_CHROMIUM_PATH", "/opt/pw-browsers/chromium"),
                   "ERP_PDF_NO_SANDBOX": "true"})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.cli import main as cli
from app.main import create_app

results: list[tuple[str, float, str]] = []


def timed(name: str, unit: str = ""):
    class T:
        def __enter__(self):
            self.t = time.perf_counter()

        def __exit__(self, *a):
            results.append((name, time.perf_counter() - self.t, unit))
    return T()


cli(["migrate"])
cli(["create-owner", "--email", "bench@example.com", "--name", "B", "--password", "Perf-Strong-Pass-99"])
c = TestClient(create_app())
h = {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={"email": "bench@example.com",
                                                                      "password": "Perf-Strong-Pass-99"}).json()["access_token"]}
biz = c.post("/api/v1/businesses", headers=h, json={"name": "Bench", "state_code": "27"}).json()
base = f"/api/v1/businesses/{biz['id']}"

# 1. CSV parsing + row validation only (the part a Rust parser would replace)
from app.modules.integrations import data_io
from app.modules.integrations.data_io import read_rows

data_io.MAX_ROWS = 200_000  # measure parsing beyond the normal per-import cap

buf = io.StringIO()
w = csv.writer(buf)
w.writerow(["name", "sku", "unit", "hsn", "selling_price", "gst_rate"])
for i in range(100_000):
    w.writerow([f"Product {i}", f"SKU-{i}", "NOS", "3004", f"{i % 999}.50", "12"])
data = buf.getvalue().encode()
with timed("Parse 100,000-row CSV (13 MB)", "100k rows"):
    rows = read_rows("p.csv", data)

# 2. Full import through the API (validation + DB writes + audit + events)
small = b"\n".join(data.split(b"\n")[:5001])
with timed("Import 5,000 products end to end", "5k rows"):
    r = c.post(f"{base}/data/import/products", headers=h, files={"file": ("p.csv", small, "text/csv")})
assert r.json()["imported"] == 5000, r.text

# 3. Stock posting: opening + 300 invoices finalized one by one
prods = c.get(f"{base}/products", headers=h, params={"limit": 20}).json()["items"]
wh = c.get(f"{base}/warehouses", headers=h).json()[0]["id"]
c.post(f"{base}/inventory/opening-stock", headers=h, json={"warehouse_id": wh, "doc_date": date.today().isoformat(),
       "lines": [{"product_id": p["id"], "quantity": "100000", "unit_cost": "10"} for p in prods]})
with timed("Create + finalize 300 invoices (stock, GST, journals)", "300 invoices"):
    for i in range(300):
        d = c.post(f"{base}/documents", headers=h, json={"doc_type": "sales_invoice", "lines": [
            {"product_id": prods[i % 20]["id"], "quantity": "2"}, {"product_id": prods[(i + 7) % 20]["id"]}]}).json()
        last = c.post(f"{base}/documents/{d['id']}/finalize", headers=h,
                      json={"payments": [{"mode": "cash", "amount": d["grand_total"]}]}).json()

# 4. Reporting over the resulting books
fy = c.get(f"{base}/financial-years", headers=h).json()[0]
q = {"date_from": fy["start_date"], "date_to": fy["end_date"]}
with timed("Trial balance over ~600 journals", "1 report"):
    c.get(f"{base}/accounting/reports/trial-balance", headers=h, params=q)
with timed("GSTR-1 over 300 invoices", "1 report"):
    c.get(f"{base}/gst/gstr1", headers=h, params=q)

# 5. PDF generation
with timed("Render 1 invoice PDF (headless Chromium)", "1 PDF"):
    r = c.get(f"{base}/documents/{last['id']}/print", headers=h, params={"format": "pdf"})
assert r.status_code == 200 and r.content[:4] == b"%PDF"
ids = [d["id"] for d in c.get(f"{base}/documents", headers=h, params={"limit": 50}).json()["items"]]
with timed("Render 50 invoices as ONE batch PDF", "50 PDFs"):
    r = c.post(f"{base}/documents/print-batch", headers=h, json={"document_ids": ids})
assert r.status_code == 200 and r.content[:4] == b"%PDF", r.text

# 6. SQLite → PostgreSQL migration (Phase 9), when a server is available:
#    ERP_BENCH_PG_URL=postgresql+psycopg://user:pw@localhost:5432/postgres python scripts/benchmark.py
if os.environ.get("ERP_BENCH_PG_URL"):
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from app.cli import _engine_for
    from app.db.session import get_engine
    from app.db.transfer import transfer, upgrade_target

    admin_url = make_url(os.environ["ERP_BENCH_PG_URL"])
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS erp_bench_transfer WITH (FORCE)"))
        conn.execute(text("CREATE DATABASE erp_bench_transfer"))
    target_url = admin_url.set(database="erp_bench_transfer").render_as_string(hide_password=False)
    upgrade_target(target_url)
    target = _engine_for(target_url)
    with timed("Copy + verify whole database SQLite → PostgreSQL", "1 run"):
        rep = transfer(get_engine(), target)
    assert rep.ok and rep.committed, rep.problems
    print(f"migration: {rep.total_rows:,} rows in {rep.seconds:.2f}s "
          f"({rep.total_rows / rep.seconds:,.0f} rows/s, copy + checksums + book checks)")
    target.dispose()
    with admin.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS erp_bench_transfer WITH (FORCE)"))

print(f"\n{'Workload':58} {'seconds':>8}  per unit")
for name, secs, unit in results:
    n = int("".join(ch for ch in unit.split()[0] if ch.isdigit()) or 1) * (1000 if "k" in unit else 1)
    print(f"{name:58} {secs:8.2f}  {secs / n * 1000:8.2f} ms")
