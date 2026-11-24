"""Optional DEMO data, for trying the app or taking screenshots. Never run it on your real
database: it creates a business called "Demo — Ayurveda Wellness Store" with sample products,
customers, a supplier, purchases and a month of invoices, all through the normal API (so every
GST, stock and accounting rule applies).

    python scripts/demo_data.py --email owner@example.com --password '...'
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.core.clock import local_today
from app.main import create_app

PRODUCTS = [  # sku, name, hsn, gst, cost, price, reorder
    ("ASH-250", "Ashwagandha Churna 250 g", "30049011", "12", "160", "295", "20"),
    ("TRI-100", "Triphala Tablets (100)", "30049011", "12", "95", "180", "25"),
    ("CHY-1K", "Chyawanprash 1 kg", "21069099", "18", "310", "499", "15"),
    ("BRA-60", "Brahmi Capsules (60)", "30049011", "12", "140", "260", "20"),
    ("NEM-60", "Neem Tablets (60)", "30049011", "12", "70", "135", "30"),
    ("GIL-60", "Giloy Ghanvati (60)", "30049011", "12", "85", "165", "30"),
    ("TUL-30", "Tulsi Drops 30 ml", "30049011", "12", "55", "110", "40"),
    ("KUM-50", "Kumkumadi Tailam 50 ml", "33049990", "18", "380", "699", "10"),
    ("SHA-200", "Shatavari Granules 200 g", "30049011", "12", "150", "280", "15"),
    ("AML-1L", "Amla Juice 1 L", "20098990", "12", "120", "220", "25"),
    ("HAL-100", "Haldi (Turmeric) Capsules (100)", "30049011", "12", "110", "210", "20"),
    ("MAH-100", "Mahanarayan Oil 100 ml", "30049011", "12", "125", "235", "12"),
]
CUSTOMERS = ["Sanjivani Clinic", "Dhanvantari Health Centre", "Arogya Pharmacy", "Vaidya Wellness",
             "Prakriti Ayurveda Spa", "Rao Family", "Mehta Medical Stores", "Patanjali Wellness Point"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    args = ap.parse_args()
    random.seed(7)
    c = TestClient(create_app(), base_url="http://localhost")
    r = c.post("/api/v1/auth/login", json={"email": args.email, "password": args.password})
    if r.status_code != 200 or "access_token" not in r.json():
        print("Sign-in failed (2FA accounts: run this before enabling 2FA)", r.text, file=sys.stderr)
        return 1
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def call(method: str, path: str, body: dict | None = None, ok: tuple = (200, 201)) -> dict:
        resp = c.request(method, f"/api/v1{path}", json=body, headers=h)
        if resp.status_code not in ok:
            raise SystemExit(f"{method} {path} failed: {resp.status_code} {resp.text[:300]}")
        return resp.json() if resp.content else {}

    if any(b["name"].startswith("Demo") for b in call("GET", "/businesses")):
        print("Demo business already exists; nothing to do.")
        return 0
    biz = call("POST", "/businesses", {"name": "Demo — Ayurveda Wellness Store", "legal_name": "Demo Wellness LLP",
                                       "state_code": "27", "gstin": "27AAPFU0939F1ZV", "phone": "9876543210"})
    b = f"/businesses/{biz['id']}"
    branch = call("GET", f"{b}/branches")[0]["id"]
    c.patch(f"/api/v1{b}/branches/{branch}", headers=h, json={"address_line1": "12 FC Road", "city": "Pune",
                                                              "pincode": "411004", "state_code": "27"})
    units = {u["code"]: u["id"] for u in call("GET", f"{b}/units")}
    wh = next(w for w in call("GET", f"{b}/warehouses") if w["code"] == "MAIN")["id"]
    cat = call("POST", f"{b}/categories", {"name": "Ayurvedic medicines"})["id"]
    pids = []
    for sku, name, hsn, gst, cost, price, reorder in PRODUCTS:
        p = call("POST", f"{b}/products", {"sku": sku, "name": name, "hsn_sac": hsn, "gst_rate": gst,
                                            "unit_id": units["NOS"], "purchase_price": cost, "selling_price": price,
                                            "mrp": str(int(int(price) * 1.1)), "reorder_level": reorder,
                                            "category_id": cat, "barcode": f"890{random.randint(10**9, 10**10 - 1)}"})
        pids.append((p["id"], cost, sku))
    supplier = call("POST", f"{b}/suppliers", {"name": "Himalaya Herbal Distributors", "state_code": "27",
                                                "phone": "9822011223", "gst_type": "unregistered"})
    today = local_today()
    start = max(today - timedelta(days=29), today.replace(day=1) - timedelta(days=29))
    # Opening stock 35 days ago, a purchase bill 20 days ago.
    call("POST", f"{b}/inventory/opening-stock", {"warehouse_id": wh, "doc_date": (today - timedelta(days=35)).isoformat(),
         "lines": [{"product_id": p, "quantity": str(random.randint(25, 60)), "unit_cost": cost} for p, cost, _ in pids]})
    bill = call("POST", f"{b}/documents", {"doc_type": "purchase_invoice", "party_id": supplier["id"],
                                          "doc_date": (today - timedelta(days=20)).isoformat(),
                                          "supplier_invoice_no": "HHD/4471", "lines": [
                                              {"product_id": p, "quantity": "24", "unit_price": cost} for p, cost, _ in pids[:6]]})
    call("POST", f"{b}/documents/{bill['id']}/finalize", {"payments": []})
    cids = [call("POST", f"{b}/customers", {"name": n, "state_code": "27", "phone": f"98{random.randint(10**7, 10**8 - 1)}",
                                            "credit_days": 15})["id"] for n in CUSTOMERS]
    n = 0
    for day in range(29, -1, -1):
        d = today - timedelta(days=day)
        if d < start:
            continue
        for _ in range(random.randint(1, 4)):
            lines = [{"product_id": p, "quantity": str(random.randint(1, 3))} for p, _, _ in random.sample(pids, random.randint(1, 3))]
            walk_in = random.random() < 0.3
            doc = call("POST", f"{b}/documents", {"doc_type": "sales_invoice", "doc_date": d.isoformat(),
                                                  **({} if walk_in else {"party_id": random.choice(cids)}), "lines": lines})
            pay = walk_in or random.random() < 0.6
            call("POST", f"{b}/documents/{doc['id']}/finalize", {"payments": [
                {"mode": random.choice(["upi", "cash", "card"]), "amount": doc["grand_total"]}] if pay else []})
            n += 1
    accounts = call("GET", f"{b}/accounting/accounts")
    rent = next((a for a in accounts if "rent" in a["name"].lower()), None)
    bank = next((a for a in accounts if a.get("system_key") == "bank"), None)
    if rent:
        call("POST", f"{b}/accounting/expenses", {"expense_date": (today - timedelta(days=5)).isoformat(),
                                                  "account_id": rent["id"], "amount": "18000", "description": "Shop rent",
                                                  **({"paid_from_account_id": bank["id"]} if bank else {})})
    print(f"Demo business created: {biz['name']} — {len(pids)} products, {len(cids)} customers, {n} invoices")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
