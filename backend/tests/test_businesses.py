"""Multi-business management: creation, isolation, archive, branches, financial years."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.modules.access.catalog import SYSTEM_ROLES
from app.modules.businesses.service import financial_year_for
from app.modules.events.models import OutboxEvent
from tests.conftest import VALID_GSTIN


@pytest.mark.parametrize(
    ("day", "start_month", "expected"),
    [
        (date(2026, 4, 1), 4, (date(2026, 4, 1), date(2027, 3, 31), "2026-27")),
        (date(2026, 3, 31), 4, (date(2025, 4, 1), date(2026, 3, 31), "2025-26")),
        (date(2024, 2, 29), 4, (date(2023, 4, 1), date(2024, 3, 31), "2023-24")),
        (date(2026, 7, 15), 1, (date(2026, 1, 1), date(2026, 12, 31), "2026")),
    ],
)
def test_financial_year_for(day, start_month, expected):
    assert financial_year_for(day, start_month) == expected


def test_create_business_bootstraps_everything(api, owner_headers, db):
    biz = api.create_business(owner_headers, name="Ayur Clinic", gstin=VALID_GSTIN,
                              pan="AAPFU0939F")
    bid = biz["id"]
    assert biz["gstin"] == VALID_GSTIN and biz["status"] == "active"

    branches = api.c.get(f"/api/v1/businesses/{bid}/branches", headers=owner_headers).json()
    assert [(b["code"], b["kind"]) for b in branches] == [("HO", "head_office")]

    fys = api.c.get(f"/api/v1/businesses/{bid}/financial-years", headers=owner_headers).json()
    assert len(fys) == 1 and fys[0]["start_date"] <= date.today().isoformat() <= fys[0]["end_date"]

    roles = api.c.get(f"/api/v1/businesses/{bid}/roles", headers=owner_headers).json()
    assert {r["code"] for r in roles} == {r.code for r in SYSTEM_ROLES}

    events = db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "business.created")).all()
    assert len(events) == 1 and events[0].payload["name"] == "Ayur Clinic"


@pytest.mark.parametrize(
    ("field", "value"),
    [("gstin", "27AAPFU0939F1ZX"), ("gstin", "99AAPFU0939F1ZV"), ("pan", "ABC"),
     ("state_code", "28"), ("pincode", "12"), ("fy_start_month", 13)],
)
def test_business_validation(api, owner_headers, field, value):
    r = api.c.post("/api/v1/businesses", headers=owner_headers, json={"name": "X", field: value})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_failed"


def test_only_owner_creates_businesses(api, owner_headers):
    biz = api.create_business(owner_headers)
    admin_h, _ = api.member_session(owner_headers, biz["id"], "admin@example.com", "business_admin")
    r = api.c.post("/api/v1/businesses", headers=admin_h, json={"name": "Sneaky"})
    assert r.status_code == 403


def test_members_only_see_their_businesses(api, owner_headers):
    a = api.create_business(owner_headers, name="A")
    b = api.create_business(owner_headers, name="B")
    h, _ = api.member_session(owner_headers, a["id"], "a-staff@example.com", "billing_operator")

    names = [x["name"] for x in api.c.get("/api/v1/businesses", headers=h).json()]
    assert names == ["A"]
    # Another business is indistinguishable from a non-existent one.
    for path in ("", "/branches", "/roles", "/members", "/financial-years"):
        r = api.c.get(f"/api/v1/businesses/{b['id']}{path}", headers=h)
        assert r.status_code == 404, path
    me = api.c.get("/api/v1/auth/me", headers=h).json()
    assert [x["business_name"] for x in me["businesses"]] == ["A"]

    owner_names = [x["name"] for x in api.c.get("/api/v1/businesses", headers=owner_headers).json()]
    assert owner_names == ["A", "B"]


def test_archive_hides_business_from_members_and_owner_can_restore(api, owner_headers):
    biz = api.create_business(owner_headers)
    h, _ = api.member_session(owner_headers, biz["id"], "admin@example.com", "business_admin")
    # Even a business admin cannot archive.
    assert api.c.post(f"/api/v1/businesses/{biz['id']}/archive", headers=h).status_code == 403

    r = api.c.post(f"/api/v1/businesses/{biz['id']}/archive", headers=owner_headers)
    assert r.status_code == 200 and r.json()["status"] == "archived"
    assert api.c.get(f"/api/v1/businesses/{biz['id']}", headers=h).status_code == 404
    assert api.c.get("/api/v1/businesses", headers=owner_headers).json() == []
    assert len(api.c.get("/api/v1/businesses?include_archived=true", headers=owner_headers).json()) == 1
    r = api.c.patch(f"/api/v1/businesses/{biz['id']}", headers=owner_headers, json={"name": "Z"})
    assert r.status_code == 422

    assert api.c.post(f"/api/v1/businesses/{biz['id']}/restore", headers=owner_headers).status_code == 200
    assert api.c.get(f"/api/v1/businesses/{biz['id']}", headers=h).status_code == 200


def test_update_business_requires_permission_and_is_audited(api, owner_headers):
    biz = api.create_business(owner_headers)
    bid = biz["id"]
    op_h, _ = api.member_session(owner_headers, bid, "op@example.com", "billing_operator")
    assert api.c.patch(f"/api/v1/businesses/{bid}", headers=op_h,
                       json={"name": "Hacked"}).status_code == 403

    r = api.c.patch(f"/api/v1/businesses/{bid}", headers=owner_headers,
                    json={"name": "Renamed", "city": "Pune"})
    assert r.status_code == 200 and r.json()["name"] == "Renamed"
    logs = api.c.get(f"/api/v1/businesses/{bid}/audit-logs?action=business.updated",
                     headers=owner_headers).json()["items"]
    assert logs[0]["changes"]["name"] == {"from": "Test Business", "to": "Renamed"}


def test_fy_start_month_locked_once_years_exist(api, owner_headers):
    biz = api.create_business(owner_headers)
    r = api.c.patch(f"/api/v1/businesses/{biz['id']}", headers=owner_headers,
                    json={"fy_start_month": 1})
    assert r.status_code == 422


def test_financial_year_creation_and_overlap(api, owner_headers):
    biz = api.create_business(owner_headers)
    url = f"/api/v1/businesses/{biz['id']}/financial-years"
    assert api.c.post(url, headers=owner_headers,
                      json={"containing_date": date.today().isoformat()}).status_code == 409
    r = api.c.post(url, headers=owner_headers, json={"containing_date": "2030-06-01"})
    assert r.status_code == 201 and r.json()["code"] == "2030-31"


def test_branch_crud(api, owner_headers):
    biz = api.create_business(owner_headers)
    url = f"/api/v1/businesses/{biz['id']}/branches"
    r = api.c.post(url, headers=owner_headers,
                   json={"code": "pune-2", "name": "Pune Camp", "state_code": "27"})
    assert r.status_code == 201 and r.json()["code"] == "PUNE-2"
    branch = r.json()
    assert api.c.post(url, headers=owner_headers,
                      json={"code": "PUNE-2", "name": "Dup"}).status_code == 409
    assert api.c.post(url, headers=owner_headers,
                      json={"code": "HO2", "name": "x", "kind": "head_office"}).status_code == 422

    r = api.c.patch(f"{url}/{branch['id']}", headers=owner_headers, json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert [b["code"] for b in api.c.get(url, headers=owner_headers).json()] == ["HO"]
    assert len(api.c.get(f"{url}?include_inactive=true", headers=owner_headers).json()) == 2

    ho = next(b for b in api.c.get(url, headers=owner_headers).json() if b["code"] == "HO")
    assert api.c.patch(f"{url}/{ho['id']}", headers=owner_headers,
                       json={"is_active": False}).status_code == 422


def test_branch_of_other_business_not_reachable(api, owner_headers):
    a = api.create_business(owner_headers, name="A")
    b = api.create_business(owner_headers, name="B")
    b_ho = api.c.get(f"/api/v1/businesses/{b['id']}/branches", headers=owner_headers).json()[0]
    r = api.c.get(f"/api/v1/businesses/{a['id']}/branches/{b_ho['id']}", headers=owner_headers)
    assert r.status_code == 404
