"""Moving from Tally / Busy / Marg, scheme claims from manufacturers, and sale-or-return."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.conftest import TODAY, Shop
from tests.test_accounting import bal, books_ok, fy
from tests.test_billing import customer, doc, finalize, stocked

D = Decimal


@pytest.fixture
def s(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def upload(s: Shop, path: str, name: str, data: bytes, expect: int = 200, **params):
    r = s.api.c.post(f"{s.base}{path}", headers=s.h, params=params, files={"file": (name, data)})
    assert r.status_code == expect, r.text
    return r.json()


# ---------------------------------------------------------------------------- Busy / Marg headers


def test_busy_marg_column_names_are_understood(s):
    csv = (b"Item Name,Main Unit,HSN/SAC Code,Sale Price,Purc Price,M.R.P.,Tax Category\n"
           b"Ashwagandha Churna 100g,Pcs.,30049011,120,80,150,GST 12%\n"
           b"Triphala Tablets,Bottles,30049011,90,60,110,12%\n")
    r = upload(s, "/data/import/products", "busy.csv", csv)
    assert r["imported"] == 2, r
    p = {x["name"]: x for x in s.get("/products", q="Ashwagandha")["items"]}["Ashwagandha Churna 100g"]
    assert p["sku"] == "ASHWAGANDHA-CHURNA-1" and D(p["gst_rate"]) == 12 and D(p["mrp"]) == 150

    parties = (b"Party Name,GST No,Mobile No,State,Address 1,Station\n"
               b"Shree Medical,24AAACS1234A1Z8,9876543210,Gujarat,Station Road,Anand\n")
    r = upload(s, "/data/import/customers", "marg.csv", parties)
    assert r["imported"] == 1, r
    c = s.get("/customers", q="Shree")["items"][0]
    assert c["state_code"] == "24" and c["gstin"] == "24AAACS1234A1Z8"


# ---------------------------------------------------------------------------- Tally XML

TALLY = b"""<ENVELOPE><BODY><IMPORTDATA><REQUESTDATA>
<TALLYMESSAGE><LEDGER NAME="Patel Traders"><PARENT>Sundry Debtors</PARENT>
  <ADDRESS.LIST><ADDRESS>12 Market Yard</ADDRESS><ADDRESS>Rajkot</ADDRESS></ADDRESS.LIST>
  <LEDSTATENAME>Gujarat</LEDSTATENAME><PARTYGSTIN>24AAACP1234B1Z9</PARTYGSTIN>
  <OPENINGBALANCE>-15000.00</OPENINGBALANCE></LEDGER></TALLYMESSAGE>
<TALLYMESSAGE><LEDGER NAME="Herbal Labs Pvt Ltd"><PARENT>Sundry Creditors</PARENT>
  <LEDSTATENAME>Maharashtra</LEDSTATENAME><OPENINGBALANCE>8000.00</OPENINGBALANCE></LEDGER></TALLYMESSAGE>
<TALLYMESSAGE><LEDGER NAME="Rent"><PARENT>Indirect Expenses</PARENT></LEDGER></TALLYMESSAGE>
<TALLYMESSAGE><STOCKITEM NAME="Chyawanprash 500g"><BASEUNITS>Nos</BASEUNITS><HSNCODE>21069099</HSNCODE>
  <GSTDETAILS.LIST><STATEWISEDETAILS.LIST><RATEDETAILS.LIST><GSTRATEDUTYHEAD>IGST</GSTRATEDUTYHEAD>
  <GSTRATE>18</GSTRATE></RATEDETAILS.LIST></STATEWISEDETAILS.LIST></GSTDETAILS.LIST>
  <OPENINGBALANCE> 40 Nos</OPENINGBALANCE><OPENINGRATE>210.00/Nos</OPENINGRATE></STOCKITEM></TALLYMESSAGE>
</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"""


def test_tally_masters_with_opening_balances_and_stock(s):
    start = fy(s)["start_date"]
    dry = upload(s, "/data/import-tally", "Master.xml", TALLY, dry_run=True, opening_date=start, warehouse_id=s.main)
    assert (dry["customers"], dry["suppliers"], dry["products"], dry["errors"]) == (1, 1, 1, [])
    assert s.get("/customers", q="Patel")["total"] == 0  # dry run wrote nothing

    r = upload(s, "/data/import-tally", "Master.xml", TALLY, opening_date=start, warehouse_id=s.main)
    assert (r["customers"], r["suppliers"], r["products"], r["opening_balances"], r["opening_stock_lines"]) == (1, 1, 1, 2, 1)
    c = s.get("/customers", q="Patel")["items"][0]
    assert c["gstin"] == "24AAACP1234B1Z9" and c["state_code"] == "24"
    assert bal(s, "ar") == D(15000) and bal(s, "ap") == D(-8000)
    p = s.get("/products", q="Chyawanprash", with_stock=True)["items"][0]
    assert D(p["gst_rate"]) == 18 and p["hsn_sac"] == "21069099" and D(p["on_hand"]) == 40
    assert bal(s, "inventory") == D(8400)
    books_ok(s)

    again = upload(s, "/data/import-tally", "Master.xml", TALLY)  # same names: nothing new
    assert (again["customers"], again["products"], again["skipped"]) == (0, 0, 3)
    bad = upload(s, "/data/import-tally", "x.xml", b"<html>not tally</html>", expect=422)
    assert "No customers" in bad["error"]["message"]
    upload(s, "/data/import-tally", "x.xml", b"<!DOCTYPE x [<!ENTITY a 'b'>]><x>&a;</x>", expect=422)  # entities refused


# ---------------------------------------------------------------------------- scheme claims


def test_scheme_claim_from_manufacturer(s, db):
    mfr = s.post("/suppliers", {"name": "Herbal Labs"})
    p = stocked(s, "chy", selling_price="100", purchase_price="60", cost="60")
    q = stocked(s, "tri", selling_price="200")
    s.post("/schemes", {"name": "10 + 1", "kind": "free_goods", "product_id": p, "min_quantity": "10",
                        "free_quantity": "1", "claim_party_id": mfr["id"]})
    s.post("/schemes", {"name": "5 % off", "kind": "discount", "product_id": q, "min_quantity": "5",
                        "discount_percent": "5", "claim_party_id": mfr["id"], "claim_percent": "50"})
    c = customer(s)
    finalize(s, doc(s, "sales_invoice", [{"product_id": p, "quantity": "20"}, {"product_id": q, "quantity": "10"}],
                    party_id=c["id"])["id"])
    r = s.get("/scheme-claims/claimable", date_from=TODAY, date_to=TODAY)
    g = r["parties"][0]
    # 2 free × ₹60 purchase price = 120; 5 % of 2,000 = 100, half borne by the maker = 50
    assert g["party_name"] == "Herbal Labs" and D(g["amount"]) == D(170) and D(g["free_quantity"]) == 2

    claim = s.post("/scheme-claims", {"party_id": mfr["id"], "date_from": TODAY, "date_to": TODAY})
    assert D(claim["claim_amount"]) == 170 and claim["lines"] == 2
    assert s.get("/scheme-claims/claimable", date_from=TODAY, date_to=TODAY)["parties"] == []  # not claimed twice
    s.post("/scheme-claims", {"party_id": mfr["id"], "date_from": TODAY, "date_to": TODAY}, expect=422)
    assert len(s.get(f"/scheme-claims/{claim['id']}")["items"]) == 2

    done = s.post(f"/scheme-claims/{claim['id']}/settle", {"amount": "160", "reference": "CN-77"}, expect=200)
    assert done["status"] == "settled"
    assert bal(s, "scheme_claims") == D(-160) and bal(s, "ap") == D(160)
    s.post(f"/scheme-claims/{claim['id']}/reject", {"reason": "too late"}, expect=409)
    books_ok(s)


# ---------------------------------------------------------------------------- sale-or-return


def test_sale_or_return_challan(s, db):
    p = stocked(s, "sor", selling_price="50", qty="30", cost="20")
    c = customer(s, "Retailer")
    dc = finalize(s, doc(s, "delivery_challan", [{"product_id": p, "quantity": "10"}], party_id=c["id"])["id"])
    lid = dc["lines"][0]["id"]
    inv = s.post(f"/documents/{dc['id']}/convert", {"target": "sales_invoice"})
    s.api.c.patch(f"{s.base}/documents/{inv['id']}", headers=s.h, json={"lines": [
        {"product_id": p, "quantity": "6", "source_line_id": lid}]})
    finalize(s, inv["id"])

    row = s.get("/sale-or-return")["lines"][0]
    assert (D(row["sent"]), D(row["invoiced"]), D(row["returned"]), D(row["pending"])) == (10, 6, 0, 4)
    assert D(row["pending_value"]) == 200

    bad = s.post(f"/documents/{dc['id']}/return-unsold", {"lines": [{"line_id": lid, "quantity": "5"}]}, expect=422)
    assert "only 4" in bad["error"]["message"]
    inv_before = bal(s, "inventory")
    s.post(f"/documents/{dc['id']}/return-unsold", {"lines": [{"line_id": lid, "quantity": "4"}]})
    assert s.stock(p)[0] == D(24)  # 30 − 10 sent + 4 back
    assert bal(s, "inventory") - inv_before == D(80)  # back at the ₹20 it left at
    assert s.get("/sale-or-return")["lines"] == []  # nothing pending now
    assert D(s.get("/sale-or-return", open_only=False)["lines"][0]["returned"]) == 4
    books_ok(s)
    s.assert_consistent(db)
