"""E-way bills: which documents need one, the portal's bulk JSON, recording and validity, printing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.gst.ewaybill import validity
from tests.conftest import VALID_GSTIN, Shop
from tests.test_billing import invoice, stocked


@pytest.fixture
def eshop(api, owner_headers) -> Shop:
    s = Shop(api, owner_headers, gstin=VALID_GSTIN, state_code="27")
    s.api.c.patch(f"{s.base}/branches/{s.ho}", headers=s.h, json={"address_line1": "12 FC Road", "city": "Pune",
                                                                    "pincode": "411004", "state_code": "27"})
    return s


def test_pending_json_record_and_print(eshop):
    p = stocked(eshop, "ew1", selling_price="30000", gst_rate="12", hsn_sac="30049011")
    buyer = eshop.post("/customers", {"name": "Karnataka Wellness", "state_code": "29", "addresses": [
        {"kind": "billing", "address_line1": "4 MG Road", "city": "Bengaluru", "pincode": "560001", "state_code": "29", "is_default": True}]})
    big = invoice(eshop, [{"product_id": p, "quantity": "2"}], party_id=buyer["id"])  # 60,000 + IGST
    small = invoice(eshop, [{"product_id": p, "quantity": "1"}], party_id=buyer["id"])  # 30,000 + tax: under the limit
    pend = {x["number"] for x in eshop.get("/gst/eway-bills/pending")}
    assert big["number"] in pend and small["number"] not in pend

    bad = eshop.api.c.post(f"{eshop.base}/gst/eway-bills/json", headers=eshop.h,
                           json={"document_ids": [big["id"]], "distance_km": 840, "vehicle_no": "not a truck"})
    assert bad.status_code == 422
    r = eshop.api.c.post(f"{eshop.base}/gst/eway-bills/json", headers=eshop.h,
                         json={"document_ids": [big["id"]], "distance_km": 840, "vehicle_no": "MH 12 AB 1234"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    bill = r.json()["billLists"][0]
    assert bill["docNo"] == big["number"] and bill["toGstin"] == "URP" and bill["fromPincode"] == 411004
    assert bill["toPincode"] == 560001 and bill["toStateCode"] == 29 and bill["vehicleNo"] == "MH12AB1234"
    assert bill["igstValue"] > 0 and bill["cgstValue"] == 0 and bill["itemList"][0]["hsnCode"] == 30049011
    assert bill["totInvValue"] == float(big["grand_total"])

    rec = eshop.post(f"/gst/eway-bills/{big['id']}", {"eway_bill_no": "3410 1234 5678"}, expect=200)
    assert rec["eway_bill_no"] == "341012345678" and rec["vehicle_no"] == "MH12AB1234"
    assert big["number"] not in {x["number"] for x in eshop.get("/gst/eway-bills/pending")}
    html = eshop.api.c.get(f"{eshop.base}/documents/{big['id']}/print", headers=eshop.h).text
    assert "341012345678" in html and "MH12AB1234" in html
    again = eshop.api.c.post(f"{eshop.base}/gst/eway-bills/{big['id']}", headers=eshop.h, json={"eway_bill_no": "999999999999"})
    assert again.status_code == 409
    assert eshop.api.c.post(f"{eshop.base}/gst/eway-bills/{small['id']}", headers=eshop.h,
                            json={"eway_bill_no": "12345"}).status_code == 422


def test_validity_rules():
    at = datetime(2026, 11, 24, 6, 30, tzinfo=UTC)  # 12:00 IST
    assert validity(at, 150).astimezone(UTC).isoformat().startswith("2026-11-25T18:29:59")  # 1 day → end of 25th IST
    assert validity(at, 840).astimezone(UTC).isoformat().startswith("2026-11-29T18:29:59")  # 5 days
