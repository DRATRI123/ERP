"""Load test against a real server, followed by data-integrity checks.

Starts uvicorn on a throwaway database (or uses ``--url`` for a running server you set up
yourself), creates businesses with staff, products, stock and customers, then lets many
concurrent users bill, browse and run reports for a fixed time. Afterwards it checks:

* no server errors;
* the books balance and the stock ledger matches stock balances in every business;
* invoice numbers are unique and gapless per series, and there is one number per invoice;
* stock sold equals the drop in stock on hand, so no sale was lost or counted twice.

Examples::

    python scripts/loadtest.py                                  # SQLite, 60 users, 60 s
    python scripts/loadtest.py --users 90 --duration 120
    python scripts/loadtest.py --database postgresql+psycopg://erp:pw@localhost/erp_load --workers 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "Load-Test-Pass-2026!"
TODAY = datetime.now(ZoneInfo(os.environ.get("ERP_TIMEZONE", "Asia/Kolkata"))).date().isoformat()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stats:
    def __init__(self) -> None:
        self.lat: dict[str, list[float]] = defaultdict(list)
        self.status: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.errors: list[str] = []
        self.sold: dict[str, Decimal] = defaultdict(Decimal)  # product id -> quantity sold
        self.invoices = 0
        self.shed = 0

    def record(self, action: str, r: httpx.Response, seconds: float) -> None:
        self.lat[action].append(seconds)
        self.status[action][r.status_code] += 1
        if r.status_code == 503:
            self.shed += 1  # "busy, retry": load shedding, not an error (nothing was written)
        elif r.status_code >= 500:
            self.errors.append(f"{action}: {r.status_code} {r.text[:200]}")


async def timed(stats: Stats, action: str, coro):
    t = time.perf_counter()
    r = await coro
    stats.record(action, r, time.perf_counter() - t)
    return r


# ---------------------------------------------------------------------------- setup


def setup(base: str, businesses: int, users_per_business: int, products: int) -> list[dict]:
    """Owner session → businesses, products with stock, customers, invited staff."""
    c = httpx.Client(base_url=base, timeout=60)
    r = c.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    r.raise_for_status()
    owner = {"Authorization": f"Bearer {r.json()['access_token']}"}
    out = []
    for b in range(businesses):
        biz = c.post("/businesses", headers=owner, json={
            "name": f"Load Business {b + 1}", "state_code": "27"}).json()
        bid = biz["id"]
        base_b = f"/businesses/{bid}"
        units = {u["code"]: u["id"] for u in c.get(f"{base_b}/units", headers=owner).json()}
        wh = next(w for w in c.get(f"{base_b}/warehouses", headers=owner).json() if w["code"] == "MAIN")["id"]
        pids = []
        for i in range(products):
            p = c.post(f"{base_b}/products", headers=owner, json={
                "name": f"Herbal Product {i}", "sku": f"HP{i:04d}", "unit_id": units["NOS"],
                "selling_price": str(50 + i), "hsn_sac": "30049011", "gst_rate": "12"})
            p.raise_for_status()
            pids.append(p.json()["id"])
        c.post(f"{base_b}/inventory/opening-stock", headers=owner, json={
            "warehouse_id": wh, "doc_date": TODAY,
            "lines": [{"product_id": p, "quantity": "1000000", "unit_cost": "20"} for p in pids]}).raise_for_status()
        cids = [c.post(f"{base_b}/customers", headers=owner, json={"name": f"Customer {i}"}).json()["id"]
                for i in range(20)]
        role = next(x["id"] for x in c.get(f"{base_b}/roles", headers=owner).json() if x["code"] == "branch_manager")
        staff = []
        for u in range(users_per_business):
            email = f"staff{b}-{u}@example.com"
            m = c.post(f"{base_b}/members", headers=owner, json={
                "email": email, "full_name": f"Staff {b}-{u}", "role_id": role}).json()
            c.post("/auth/accept-invite", json={"token": m["invite_token"], "password": PASSWORD}).raise_for_status()
            staff.append(email)
        out.append({"id": bid, "base": base_b, "products": pids, "customers": cids, "staff": staff,
                    "warehouse": wh})
    return out


def stock_on_hand(base: str, biz: dict, headers: dict) -> dict[str, Decimal]:
    c = httpx.Client(base_url=base, timeout=60, headers=headers)
    totals: dict[str, Decimal] = defaultdict(Decimal)
    offset = 0
    while True:
        page = c.get(f"{biz['base']}/inventory/balances", params={"limit": 200, "offset": offset}).json()
        for row in page["items"]:
            totals[row["product_id"]] += Decimal(row["quantity"])
        offset += 200
        if offset >= page["total"]:
            return totals


# ---------------------------------------------------------------------------- the users


async def user(base: str, biz: dict, email: str, until: float, stats: Stats, think: float) -> None:
    async with httpx.AsyncClient(base_url=base, timeout=60) as c:
        for _ in range(5):  # like a real client: retry when the server says it is busy
            r = await timed(stats, "login", c.post("/auth/login", json={"email": email, "password": PASSWORD}))
            if r.status_code != 503:
                break
            await asyncio.sleep(float(r.headers.get("retry-after", "2")))
        if r.status_code != 200:
            stats.errors.append(f"login {email}: {r.status_code} {r.text[:200]}")
            return
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        b = biz["base"]
        period = {"date_from": TODAY[:8] + "01", "date_to": TODAY}
        while time.monotonic() < until:
            roll = random.random()
            if roll < 0.30:  # sell: create + finalize an invoice with 1-4 lines, paid by UPI
                lines = [{"product_id": p, "quantity": str(random.randint(1, 5))}
                         for p in random.sample(biz["products"], random.randint(1, 4))]
                r = await timed(stats, "invoice.create", c.post(f"{b}/documents", json={
                    "doc_type": "sales_invoice", "doc_date": TODAY, "party_id": random.choice(biz["customers"]),
                    "lines": lines}))
                if r.status_code == 201:
                    total = r.json()["grand_total"]
                    f = await timed(stats, "invoice.finalize", c.post(
                        f"{b}/documents/{r.json()['id']}/finalize",
                        json={"payments": [{"mode": "upi", "amount": total, "reference": secrets.token_hex(4)}]}))
                    if f.status_code == 200:
                        stats.invoices += 1
                        for ln in lines:
                            stats.sold[ln["product_id"]] += Decimal(ln["quantity"])
                    elif f.status_code != 503:
                        stats.errors.append(f"finalize: {f.status_code} {f.text[:200]}")
            elif roll < 0.55:
                await timed(stats, "products.search", c.get(f"{b}/products", params={"q": "Herbal 1", "limit": 20}))
            elif roll < 0.70:
                await timed(stats, "stock.balances", c.get(f"{b}/inventory/balances", params={"limit": 50}))
            elif roll < 0.82:
                r = await timed(stats, "invoices.list", c.get(f"{b}/documents", params={"limit": 20}))
                items = r.json().get("items", []) if r.status_code == 200 else []
                if items:
                    await timed(stats, "invoice.print", c.get(f"{b}/documents/{random.choice(items)['id']}/print"))
            elif roll < 0.90:
                await timed(stats, "report.trial_balance", c.get(f"{b}/accounting/reports/trial-balance", params=period))
            elif roll < 0.95:
                await timed(stats, "report.day_book", c.get(f"{b}/accounting/reports/day-book", params=period))
            else:
                await timed(stats, "report.gstr1", c.get(f"{b}/gst/gstr1", params=period))
            if think:
                await asyncio.sleep(random.uniform(think / 2, think * 1.5))


# ---------------------------------------------------------------------------- integrity


def integrity(env: dict[str, str], bizs: list[dict]) -> list[str]:
    os.environ.update(env)
    sys.path.insert(0, str(BACKEND))
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.modules.accounting.reports import verify_books
    from app.modules.billing.models import Document
    from app.modules.inventory.engine import verify_balances

    problems: list[str] = []
    db = SessionLocal()
    try:
        for biz in bizs:
            import uuid

            bid = uuid.UUID(biz["id"])
            problems += [f"books: {p}" for p in verify_books(db, bid)]
            problems += [f"stock: {p}" for p in verify_balances(db, bid)]
            rows = db.execute(select(Document.series_id, Document.number).where(
                Document.business_id == bid, Document.doc_type == "sales_invoice",
                Document.number.is_not(None))).all()
            by_series: dict = defaultdict(list)
            for series, number in rows:
                by_series[series].append(int(re.search(r"(\d+)$", number).group(1)))
            for series, nums in by_series.items():
                if len(nums) != len(set(nums)):
                    problems.append(f"duplicate invoice numbers in series {series}")
                if sorted(nums) != list(range(1, len(nums) + 1)):
                    problems.append(f"gap in invoice numbers in series {series}")
    finally:
        db.close()
    return problems


# ---------------------------------------------------------------------------- main


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", help="Database URL (default: a fresh SQLite file)")
    ap.add_argument("--businesses", type=int, default=3)
    ap.add_argument("--users", type=int, default=60, help="Concurrent users in total")
    ap.add_argument("--duration", type=int, default=60, help="Seconds of load")
    ap.add_argument("--think", type=float, default=0.5, help="Mean pause between a user's actions (s)")
    ap.add_argument("--products", type=int, default=40)
    ap.add_argument("--workers", type=int, default=1, help="uvicorn worker processes")
    ap.add_argument("--report", help="Write results as JSON")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="erp-load-"))
    db_url = args.database or f"sqlite:///{tmp / 'load.db'}"
    port = free_port()
    env = {**os.environ, "ERP_ENV": "production", "ERP_SECRET_KEY": secrets.token_urlsafe(48),
           "ERP_DATABASE_URL": db_url, "ERP_LOGIN_IP_LIMIT": "100000",
           "ERP_MAX_USERS_PER_BUSINESS": str(max(30, args.users)),
           "ERP_BACKUP_DIR": str(tmp / "backups"), "ERP_REDIS_URL": ""}
    env.pop("ERP_REDIS_URL")
    def run(*a: str) -> None:
        subprocess.run([sys.executable, "-m", "app.cli", *a], cwd=BACKEND, env=env, check=True,
                       capture_output=True, text=True)
    run("migrate")
    run("create-owner", "--email", "owner@example.com", "--name", "Owner", "--password", PASSWORD)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1",
         "--port", str(port), "--workers", str(args.workers), "--log-level", "warning", "--no-access-log"],
        cwd=BACKEND, env=env, stdout=(tmp / "server.log").open("w"), stderr=subprocess.STDOUT)
    print(f"Server log: {tmp / 'server.log'}", flush=True)
    base = f"http://127.0.0.1:{port}/api/v1"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        per_biz = max(1, args.users // args.businesses)
        print(f"Setting up {args.businesses} businesses × {per_biz} users on {db_url.split('@')[-1]} …", flush=True)
        bizs = setup(base, args.businesses, per_biz, args.products)
        owner_h = {"Authorization": "Bearer " + httpx.post(f"{base}/auth/login", json={
            "email": "owner@example.com", "password": PASSWORD}).json()["access_token"]}
        before = [stock_on_hand(base, b, owner_h) for b in bizs]

        stats = Stats()

        async def run_load() -> float:
            until = time.monotonic() + args.duration
            t0 = time.perf_counter()
            await asyncio.gather(*[user(base, b, e, until, stats, args.think) for b in bizs for e in b["staff"]])
            return time.perf_counter() - t0

        print(f"Running {per_biz * args.businesses} concurrent users for {args.duration}s …", flush=True)
        elapsed = asyncio.run(run_load())
        after = [stock_on_hand(base, b, owner_h) for b in bizs]
    finally:
        server.terminate()
        server.wait(timeout=30)

    problems = list(stats.errors)
    for b, pre, post in zip(bizs, before, after, strict=True):
        for p in b["products"]:
            if pre[p] - post[p] != stats.sold.get(p, Decimal(0)):
                problems.append(f"stock drift on {p}: on hand fell by {pre[p] - post[p]}, sold {stats.sold.get(p, 0)}")
    problems += integrity(env, bizs)

    total = sum(len(v) for v in stats.lat.values())
    print(f"\n{'action':22} {'count':>7} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'max ms':>8}  statuses")
    rows = []
    for action in sorted(stats.lat):
        v = stats.lat[action]
        row = {"action": action, "count": len(v), "p50": pct(v, .5) * 1000, "p95": pct(v, .95) * 1000,
               "p99": pct(v, .99) * 1000, "max": max(v) * 1000, "statuses": dict(stats.status[action])}
        rows.append(row)
        print(f"{action:22} {len(v):7} {row['p50']:8.1f} {row['p95']:8.1f} {row['p99']:8.1f} {row['max']:8.1f}  "
              f"{dict(stats.status[action])}")
    summary = {"database": db_url.split("@")[-1].split("://")[0], "users": per_biz * args.businesses,
               "workers": args.workers, "duration_s": round(elapsed, 1), "requests": total,
               "throughput_rps": round(total / elapsed, 1), "invoices": stats.invoices,
               "invoices_per_min": round(stats.invoices / elapsed * 60, 1), "busy_503": stats.shed,
               "mean_ms": round(statistics.mean([x for v in stats.lat.values() for x in v]) * 1000, 1),
               "problems": problems[:50], "actions": rows}
    print(f"\n{total} requests in {elapsed:.0f}s = {summary['throughput_rps']} req/s; "
          f"{stats.invoices} invoices ({summary['invoices_per_min']}/min); {stats.shed} answered 503 busy")
    print("Integrity: OK — books balance, stock ledger consistent, invoice numbers gapless, "
          "stock sold = stock drop" if not problems else "PROBLEMS:\n  " + "\n  ".join(problems[:20]))
    if args.report:
        Path(args.report).write_text(json.dumps(summary, indent=2, default=str))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
