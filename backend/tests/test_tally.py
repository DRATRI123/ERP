"""Tally XML export: well-formed, balanced vouchers, party ledgers, escaping and permissions."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest

from tests.conftest import USER_PASSWORD, Shop
from tests.test_billing import customer, invoice, stocked


@pytest.fixture
def tshop(api, owner_headers) -> Shop:
    return Shop(api, owner_headers, name="Tally & <Sons>")


def test_tally_masters_and_vouchers(tshop):
    p = stocked(tshop, "tl1", selling_price="1000", gst_rate="12")
    c = customer(tshop, 'Arogya "Pharma" & Co', state_code="27")
    inv = invoice(tshop, [{"product_id": p}], party_id=c["id"])
    tshop.post("/payments", {"direction": "in", "party_id": c["id"], "mode": "upi", "amount": inv["grand_total"],
                             "allocations": [{"document_id": inv["id"], "amount": inv["grand_total"]}]})

    r = tshop.api.c.get(f"{tshop.base}/accounting/export/tally", headers=tshop.h, params={"what": "masters"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(r.content)  # well-formed despite the & < " in names
    ledgers = {led.get("NAME"): led.findtext("PARENT") for led in root.iter("LEDGER")}
    assert ledgers['Arogya "Pharma" & Co'] == "Sundry Debtors" and ledgers["Sales"] == "Sales Accounts"
    assert ledgers["Cash in Hand"] == "Cash-in-Hand" and "Accounts Receivable (Debtors)" not in ledgers

    r = tshop.api.c.get(f"{tshop.base}/accounting/export/tally", headers=tshop.h)
    root = ET.fromstring(r.content)
    vouchers = list(root.iter("VOUCHER"))
    assert int(r.headers["x-voucher-count"]) == len(vouchers) >= 2
    types = {v.findtext("VOUCHERTYPENAME") for v in vouchers}
    assert {"Sales", "Receipt"} <= types
    for v in vouchers:  # every voucher balances to zero, in Tally's sign convention
        amounts = [Decimal(e.findtext("AMOUNT")) for e in v.iter("ALLLEDGERENTRIES.LIST")]
        assert sum(amounts) == 0 and amounts
    sale = next(v for v in vouchers if v.findtext("VOUCHERTYPENAME") == "Sales")
    party_line = next(e for e in sale.iter("ALLLEDGERENTRIES.LIST") if e.findtext("LEDGERNAME") == 'Arogya "Pharma" & Co')
    assert party_line.findtext("ISDEEMEDPOSITIVE") == "Yes" and Decimal(party_line.findtext("AMOUNT")) == -Decimal(inv["grand_total"])
    assert sale.findtext("REFERENCE") == inv["number"]


def test_tally_export_needs_permissions(api, tshop):
    role = tshop.post("/roles", {"name": "Books only", "permissions": ["business.view", "accounting.view"]})
    m = tshop.post("/members", {"email": "books@example.com", "full_name": "B", "role_id": role["id"]})
    api.c.post("/api/v1/auth/accept-invite", json={"token": m["invite_token"], "password": USER_PASSWORD})
    h = api.auth("books@example.com", USER_PASSWORD)
    assert api.c.get(f"{tshop.base}/accounting/export/tally", headers=h).status_code == 403  # needs data.export too
