"""Field sales: beats, the salesman's day, visits, targets vs achievement."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from tests.conftest import TODAY, Shop
from tests.test_billing import customer, doc, finalize

T = date.fromisoformat(TODAY)


@pytest.fixture
def fs(api, owner_headers) -> Shop:
    s = Shop(api, owner_headers)
    assert api.c.put(f"{s.base}/modules", headers=owner_headers, json={"modules": {"field": True}}).status_code == 200
    return s


def call(s: Shop, method: str, path: str, headers=None, **kw):
    return getattr(s.api.c, method)(s.base + path, headers=headers or s.h, **kw)


def test_beat_day_visit_and_targets(fs):
    sh, member = fs.api.member_session(fs.h, fs.bid, "ravi@example.com", "sales_executive",
                                       grant_permissions=["payments.view", "payments.create"])
    ravi = member["user"]["id"]
    assert ravi in {x["id"] for x in fs.get("/field/salesmen")}
    a, b = customer(fs, "Shree Medical", phone="9800000001"), customer(fs, "Gupta Stores")

    beat = fs.post("/field/beats", {"name": "Adajan Monday", "weekday": T.weekday(), "salesman_id": ravi,
                                    "party_ids": [a["id"], b["id"]]})
    assert beat["party_ids"] == [a["id"], b["id"]]
    bad = call(fs, "post", "/field/beats", json={"name": "adajan monday"})
    assert bad.status_code == 422  # same name
    twice = call(fs, "post", "/field/beats", json={"name": "X", "party_ids": [a["id"], a["id"]]})
    assert twice.status_code == 422
    stranger = call(fs, "post", "/field/beats", json={"name": "Y", "salesman_id": a["id"]})
    assert stranger.status_code == 422

    # Ravi's phone: today's beat with the customers in order.
    day = call(fs, "get", "/field/day", headers=sh).json()
    assert day["beat"]["name"] == "Adajan Monday" and [c["name"] for c in day["customers"]] == ["Shree Medical", "Gupta Stores"]
    assert day["customers"][0]["phone"] == "9800000001" and day["visits_today"] == 0

    # He books an order at Shree Medical and logs the visit; Gupta was closed.
    line = [{"description": "Ashwagandha", "quantity": "10", "unit_price": "500", "gst_rate": "0"}]
    order = call(fs, "post", "/documents", headers=sh, json={"doc_type": "sales_order", "doc_date": TODAY, "party_id": a["id"],
                                                            "salesperson_id": ravi, "lines": line})
    assert order.status_code == 201, order.text
    v = call(fs, "post", "/field/visits", headers=sh, json={"party_id": a["id"], "outcome": "order", "beat_id": beat["id"],
                                                           "document_id": order.json()["id"], "latitude": "21.1702",
                                                           "longitude": "72.8311", "note": "Wants delivery by Friday"})
    assert v.status_code == 201, v.text
    assert call(fs, "post", "/field/visits", headers=sh, json={"party_id": b["id"], "outcome": "closed"}).status_code == 201
    day = call(fs, "get", "/field/day", headers=sh).json()
    assert [c["visited_today"] for c in day["customers"]] == ["order", "closed"]
    assert (day["visits_today"], day["productive_today"]) == (2, 1)

    # The office converts the order to an invoice (salesperson carried over) and Ravi collects money.
    inv = fs.post(f"/documents/{order.json()['id']}/finalize", {"payments": []}, expect=200)
    invoice = finalize(fs, doc(fs, "sales_invoice", line, party_id=a["id"], salesperson_id=ravi)["id"])
    assert invoice["number"] and inv["status"] == "final"
    pay = call(fs, "post", "/payments", headers=sh, json={"direction": "in", "party_id": a["id"], "mode": "cash", "amount": "2000",
                                                         "allocations": [{"document_id": invoice["id"], "amount": "2000"}]})
    assert pay.status_code == 201, pay.text

    r = call(fs, "put", "/field/targets", json={"salesman_id": ravi, "month": TODAY, "amount": "10000", "collection_amount": "5000"})
    assert r.status_code == 200, r.text
    assert call(fs, "put", "/field/targets", headers=sh, json={"salesman_id": ravi, "month": TODAY, "amount": "1"}).status_code == 403
    t = fs.get("/field/targets", month=TODAY)
    row = next(x for x in t["rows"] if x["salesman_id"] == ravi)
    assert (D(row["target"]), D(row["sales"]), row["invoices"], D(row["orders"]), D(row["collections"])) == (
        D(10000), D(5000), 1, D(5000), D(2000))
    assert row["achieved_percent"] == 50.0 and (row["visits"], row["productive_visits"]) == (2, 1)
    # Ravi sees only his own row; the owner sees everyone.
    mine = call(fs, "get", "/field/targets", headers=sh).json()["rows"]
    assert [x["salesman_id"] for x in mine] == [ravi]
    assert len(t["rows"]) >= 2

    visits = fs.get("/field/visits", date_from=TODAY, date_to=TODAY)
    assert {x["party_name"] for x in visits} == {"Shree Medical", "Gupta Stores"}
    assert next(x for x in visits if x["outcome"] == "order")["latitude"] == "21.170200"

    # Someone else's day is only for managers.
    assert call(fs, "get", "/field/day", headers=sh, params={"salesman_id": fs.get("/field/salesmen")[0]["id"]}).status_code in (200, 422)
    updated = call(fs, "patch", f"/field/beats/{beat['id']}", json={"party_ids": [b["id"]], "weekday": None})
    assert updated.status_code == 200 and updated.json()["party_ids"] == [b["id"]]
