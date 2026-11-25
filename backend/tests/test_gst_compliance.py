"""GSTR-2B reconciliation, GSTR-9, CMP-08, RCM self-invoices, job work (ITC-04), 194Q and 26Q."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.modules.gst.compliance import norm_invoice_no
from app.modules.gst.gstin import gstin_check_digit
from tests.conftest import TODAY, Shop
from tests.test_billing import doc, finalize

T = date.fromisoformat(TODAY)


def gstin(pan: str, state: str = "27") -> str:
    first = f"{state}{pan}1Z"
    return first + gstin_check_digit(first)


@pytest.fixture
def gs(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def bill(s: Shop, sup: dict, no: str, amount: str, **kw) -> dict:
    return finalize(s, doc(s, "purchase_invoice", [{"description": "Herbs", "quantity": "1", "unit_price": amount, "gst_rate": "12"}],
                           party_id=sup["id"], supplier_invoice_no=no, **kw)["id"])


def test_norm_invoice_no():
    assert norm_invoice_no("INV/2026-27/0012") == norm_invoice_no("inv-2026-27-12")
    assert norm_invoice_no("00045") == "45"


def test_gstr2b_reconciliation(gs):
    g1, g2 = gstin("AAPFU0939F"), gstin("BBBCD1234E")
    s1 = gs.post("/suppliers", {"name": "Herbal Co", "gstin": g1})
    s2 = gs.post("/suppliers", {"name": "Pack Co", "gstin": g2})
    b1 = bill(gs, s1, "HC/001", "10000")
    bill(gs, s1, "HC/002", "5000")  # supplier has not filed it
    b3 = bill(gs, s2, "P-77", "2000")  # filed with a different amount
    tax1 = D(b1["igst_total"]) + D(b1["cgst_total"]) + D(b1["sgst_total"])
    portal = {"data": {"docdata": {"b2b": [
        {"ctin": g1, "trdnm": "HERBAL CO", "inv": [
            {"inum": "hc-1", "dt": T.strftime("%d-%m-%Y"), "val": "11200", "itcavl": "Y",
             "items": [{"txval": "10000", "cgst": str(tax1 / 2), "sgst": str(tax1 / 2), "igst": "0", "cess": "0"}]},
            {"inum": "HC/009", "dt": T.strftime("%d-%m-%Y"), "val": "1120", "items": [{"txval": "1000", "igst": "120"}]}]},
        {"ctin": g2, "trdnm": "PACK CO", "inv": [
            {"inum": "P-77", "dt": T.strftime("%d-%m-%Y"), "val": "2800", "items": [{"txval": "2500", "igst": "300"}]}]},
    ]}}}
    r = gs.api.c.post(f"{gs.base}/gst/2b-reconcile", headers=gs.h, data={"date_from": TODAY, "date_to": TODAY},
                      files={"file": ("2b.json", json.dumps(portal).encode(), "application/json")})
    assert r.status_code == 200, r.text
    out = r.json()
    status = {(x["supplier_gstin"], x["number"]): x["status"] for x in out["rows"]}
    assert status[(g1, "hc-1")] == "matched"
    assert status[(g1, "HC/002")] == "only_in_books"
    assert status[(g1, "HC/009")] == "only_in_2b"
    assert status[(g2, "P-77")] == "mismatch"
    assert out["rows"][0]["status"] == "only_in_books" and D(out["itc_at_risk"]) == D(600)
    assert next(x for x in out["rows"] if x["number"] == "P-77")["document_id"] == b3["id"]
    bad = gs.api.c.post(f"{gs.base}/gst/2b-reconcile", headers=gs.h, data={"date_from": TODAY, "date_to": TODAY},
                        files={"file": ("x.json", b"not json", "application/json")})
    assert bad.status_code == 422


def test_annual_and_composition_summaries(gs):
    finalize(gs, doc(gs, "sales_invoice", [{"description": "Kit", "quantity": "1", "unit_price": "1000", "gst_rate": "18"}])["id"],
             [{"mode": "cash", "amount": "1180"}])
    g9 = gs.get("/gst/gstr9")
    assert D(g9["table_4_outward_taxable"]["taxable_value"]) == D(1000) and "table_17_hsn_outward" in g9
    c = gs.get("/gst/cmp08", rate="1")
    assert len(c["quarters"]) == 4 and D(c["gstr4"]["outward_turnover"]) == D(1000) and D(c["gstr4"]["composition_tax"]) == D(10)


def test_rcm_self_invoice(gs):
    farmer = gs.post("/suppliers", {"name": "Local Farmer"})
    rcm = bill(gs, farmer, "F-1", "3000", reverse_charge=True)
    pending = gs.get("/gst/rcm-self-invoices")
    row = next(x for x in pending if x["document_id"] == rcm["id"])
    assert row["self_invoice"] is None
    si = gs.post("/gst/rcm-self-invoices", {"document_id": rcm["id"]})
    assert si["number"].startswith("RCM/")
    assert gs.api.c.post(f"{gs.base}/gst/rcm-self-invoices", headers=gs.h, json={"document_id": rcm["id"]}).status_code == 409
    html = gs.api.c.get(f"{gs.base}/gst/rcm-self-invoices/{si['id']}/print", headers=gs.h).text
    assert "Self-invoice" in html and si["number"] in html and "Local Farmer" in html


def test_job_work_and_itc04(gs):
    p = gs.product("RAW-1", name="Ashwagandha root", purchase_price="200")["id"]
    gs.opening([{"product_id": p, "quantity": "10", "unit_cost": "200"}])
    jw = gs.post("/suppliers", {"name": "Grinding Works"})
    out = gs.post("/job-work", {"direction": "out", "party_id": jw["id"], "warehouse_id": gs.main, "process": "powdering",
                                "lines": [{"product_id": p, "quantity": "4"}]})
    assert out["number"].startswith("JWO/") and out["lines"][0]["value"] == "800.00"
    assert gs.stock(p, gs.main)[0] == D(6)
    back = gs.post("/job-work", {"direction": "in", "party_id": jw["id"], "warehouse_id": gs.main, "sent_challan_id": out["id"],
                                 "lines": [{"product_id": p, "quantity": "3"}]})
    assert back["number"].startswith("JWI/") and gs.stock(p, gs.main)[0] == D(9)
    r = gs.get("/gst/itc04", date_from=TODAY, date_to=TODAY)
    assert len(r["sent"]) == 1 and len(r["received"]) == 1
    assert r["pending"][0]["lines"][0]["pending"] == "1.000" and r["pending"][0]["return_by"] == (T + timedelta(days=365)).isoformat()
    assert gs.api.c.post(f"{gs.base}/job-work", headers=gs.h, json={"direction": "in", "party_id": jw["id"], "warehouse_id": gs.main,
                                                                   "lines": [{"product_id": p, "quantity": "1"}]}).status_code == 422


def test_194q_tracker_and_26q(gs):
    big = gs.post("/suppliers", {"name": "Big Distributor", "pan": "ABCDE1234F"})
    bill(gs, big, "BD-1", "6000000")
    rows = gs.get("/tds/194q")["rows"]
    r = next(x for x in rows if x["name"] == "Big Distributor")
    assert (D(r["purchases"]), D(r["excess"]), D(r["tds"])) == (D(6000000), D(1000000), D(1000))
    pinv = bill(gs, big, "BD-2", "1000")
    gs.post("/payments", {"direction": "out", "party_id": big["id"], "mode": "bank_transfer", "amount": "999",
                          "tds_amount": "1", "tds_section": "194Q", "allocations": [{"document_id": pinv["id"], "amount": "1000"}]})
    csv = gs.api.c.get(f"{gs.base}/tds/26q.csv", headers=gs.h, params={"date_from": TODAY, "date_to": TODAY}).text
    lines = csv.strip().splitlines()
    assert lines[0].lstrip("﻿").startswith("Deductee name") and "ABCDE1234F" in lines[1] and "194Q" in lines[1]
