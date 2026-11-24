"""Roles, members, permission overrides, branch scoping and anti-escalation."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.modules.access.catalog import PERMISSION_CODES, SYSTEM_ROLE_BY_CODE


@pytest.fixture
def biz(api, owner_headers):
    return api.create_business(owner_headers)


def members_url(bid: str) -> str:
    return f"/api/v1/businesses/{bid}/members"


def test_permission_catalog(api, owner_headers):
    codes = {p["code"] for p in api.c.get("/api/v1/permissions", headers=owner_headers).json()}
    assert codes == PERMISSION_CODES


@pytest.mark.parametrize(
    ("role", "allowed", "denied"),
    [
        ("billing_operator", "/members?x=", "/audit-logs"),
        ("accountant", "/audit-logs", "/members"),
        ("business_admin", "/audit-logs", None),
    ],
)
def test_role_permissions_enforced_on_backend(api, owner_headers, biz, role, allowed, denied):
    h, member = api.member_session(owner_headers, biz["id"], f"{role}@example.com", role)
    perms = set(member["effective_permissions"])
    assert perms == SYSTEM_ROLE_BY_CODE[role].permissions
    base = f"/api/v1/businesses/{biz['id']}"
    if denied:
        r = api.c.get(base + denied, headers=h)
        assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"
    if role != "billing_operator":
        assert api.c.get(base + allowed, headers=h).status_code == 200


def test_overrides_grant_and_deny(api, owner_headers, biz):
    h, member = api.member_session(
        owner_headers, biz["id"], "op@example.com", "billing_operator",
        grant_permissions=["audit.view"], deny_permissions=["invoices.edit"],
    )
    perms = set(member["effective_permissions"])
    assert "audit.view" in perms and "invoices.edit" not in perms
    assert api.c.get(f"/api/v1/businesses/{biz['id']}/audit-logs", headers=h).status_code == 200

    r = api.c.post(members_url(biz["id"]), headers=owner_headers, json={
        "email": "x@example.com", "full_name": "X",
        "role_id": api.role_id(owner_headers, biz["id"], "accountant"),
        "grant_permissions": ["gst.view"], "deny_permissions": ["gst.view"]})
    assert r.status_code == 422
    r = api.c.post(members_url(biz["id"]), headers=owner_headers, json={
        "email": "y@example.com", "full_name": "Y",
        "role_id": api.role_id(owner_headers, biz["id"], "accountant"),
        "grant_permissions": ["make.coffee"]})
    assert r.status_code == 422


def test_admin_cannot_grant_what_they_lack(api, owner_headers, biz):
    bid = biz["id"]
    admin_h, _ = api.member_session(owner_headers, bid, "admin@example.com", "business_admin",
                                    deny_permissions=["developer.manage"])
    # Custom role containing a permission the admin lacks.
    r = api.c.post(f"/api/v1/businesses/{bid}/roles", headers=admin_h,
                   json={"name": "Dev", "permissions": ["developer.manage"]})
    assert r.status_code == 403
    # Assigning a role that includes it (business_admin has everything).
    r = api.c.post(members_url(bid), headers=admin_h, json={
        "email": "peer@example.com", "full_name": "Peer",
        "role_id": api.role_id(owner_headers, bid, "business_admin")})
    assert r.status_code == 403
    # Granting it as an override.
    r = api.c.post(members_url(bid), headers=admin_h, json={
        "email": "peer@example.com", "full_name": "Peer",
        "role_id": api.role_id(owner_headers, bid, "billing_operator"),
        "grant_permissions": ["developer.manage"]})
    assert r.status_code == 403
    # But a subset is fine.
    r = api.c.post(members_url(bid), headers=admin_h, json={
        "email": "peer@example.com", "full_name": "Peer",
        "role_id": api.role_id(owner_headers, bid, "billing_operator")})
    assert r.status_code == 201


def test_member_cannot_modify_self_or_more_powerful_member(api, owner_headers, biz):
    bid = biz["id"]
    admin_h, admin = api.member_session(owner_headers, bid, "admin@example.com", "business_admin",
                                        deny_permissions=["developer.manage"])
    _, boss = api.member_session(owner_headers, bid, "boss@example.com", "business_admin")
    op_role = api.role_id(owner_headers, bid, "billing_operator")
    assert api.c.patch(f"{members_url(bid)}/{admin['id']}", headers=admin_h,
                       json={"deny_permissions": []}).status_code == 403
    assert api.c.patch(f"{members_url(bid)}/{boss['id']}", headers=admin_h,
                       json={"role_id": op_role}).status_code == 403
    assert api.c.post(f"{members_url(bid)}/{boss['id']}/suspend",
                      headers=admin_h).status_code == 403


def test_branch_scoped_member(api, owner_headers, biz):
    bid = biz["id"]
    burl = f"/api/v1/businesses/{bid}/branches"
    b1 = api.c.post(burl, headers=owner_headers, json={"code": "B1", "name": "One"}).json()
    b2 = api.c.post(burl, headers=owner_headers, json={"code": "B2", "name": "Two"}).json()
    mgr_h, _ = api.member_session(owner_headers, bid, "mgr@example.com", "business_admin",
                                  all_branches=False, branch_ids=[b1["id"]])

    assert [b["code"] for b in api.c.get(burl, headers=mgr_h).json()] == ["B1"]
    assert api.c.get(f"{burl}/{b2['id']}", headers=mgr_h).status_code == 404
    assert api.c.post(burl, headers=mgr_h, json={"code": "B3", "name": "x"}).status_code == 403

    role = api.role_id(owner_headers, bid, "billing_operator")
    r = api.c.post(members_url(bid), headers=mgr_h, json={
        "email": "c1@example.com", "full_name": "C1", "role_id": role, "all_branches": True})
    assert r.status_code == 403
    r = api.c.post(members_url(bid), headers=mgr_h, json={
        "email": "c1@example.com", "full_name": "C1", "role_id": role, "all_branches": False,
        "branch_ids": [b2["id"]]})
    assert r.status_code == 403
    r = api.c.post(members_url(bid), headers=mgr_h, json={
        "email": "c1@example.com", "full_name": "C1", "role_id": role, "all_branches": False,
        "branch_ids": [b1["id"]]})
    assert r.status_code == 201


def test_branch_from_other_business_rejected(api, owner_headers, biz):
    other = api.create_business(owner_headers, name="Other")
    other_ho = api.c.get(f"/api/v1/businesses/{other['id']}/branches",
                         headers=owner_headers).json()[0]
    r = api.c.post(members_url(biz["id"]), headers=owner_headers, json={
        "email": "z@example.com", "full_name": "Z",
        "role_id": api.role_id(owner_headers, biz["id"], "accountant"),
        "all_branches": False, "branch_ids": [other_ho["id"]]})
    assert r.status_code == 422


def test_role_from_other_business_rejected(api, owner_headers, biz):
    other = api.create_business(owner_headers, name="Other")
    r = api.c.post(members_url(biz["id"]), headers=owner_headers, json={
        "email": "z@example.com", "full_name": "Z",
        "role_id": api.role_id(owner_headers, other["id"], "accountant")})
    assert r.status_code == 404


def test_user_limit_per_business(api, owner_headers, biz, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_users_per_business", 2)
    api.add_member(owner_headers, biz["id"], "u1@example.com", "accountant")
    m2 = api.add_member(owner_headers, biz["id"], "u2@example.com", "accountant")["member"]
    r = api.c.post(members_url(biz["id"]), headers=owner_headers, json={
        "email": "u3@example.com", "full_name": "U3",
        "role_id": api.role_id(owner_headers, biz["id"], "accountant")})
    assert r.status_code == 409
    # Suspended members free a seat; reactivating beyond the limit is refused.
    api.c.post(f"{members_url(biz['id'])}/{m2['id']}/suspend", headers=owner_headers)
    m3 = api.add_member(owner_headers, biz["id"], "u3@example.com", "accountant")["member"]
    assert m3["status"] == "active"
    r = api.c.post(f"{members_url(biz['id'])}/{m2['id']}/reactivate", headers=owner_headers)
    assert r.status_code == 409


def test_same_user_in_two_businesses_with_different_roles(api, owner_headers):
    a = api.create_business(owner_headers, name="A")
    b = api.create_business(owner_headers, name="B")
    h, _ = api.member_session(owner_headers, a["id"], "shared@example.com", "accountant")
    out = api.add_member(owner_headers, b["id"], "shared@example.com", "billing_operator")
    assert out["invite_token"] is None  # existing active account; no new invite
    me = api.c.get("/api/v1/auth/me", headers=h).json()
    roles = {x["business_name"]: x["role"] for x in me["businesses"]}
    assert roles == {"A": "accountant", "B": "billing_operator"}
    r = api.c.post(members_url(a["id"]), headers=owner_headers, json={
        "email": "shared@example.com", "role_id": api.role_id(owner_headers, a["id"], "accountant")})
    assert r.status_code == 409


def test_suspended_membership_loses_access(api, owner_headers, biz):
    h, m = api.member_session(owner_headers, biz["id"], "s@example.com", "accountant")
    api.c.post(f"{members_url(biz['id'])}/{m['id']}/suspend", headers=owner_headers)
    assert api.c.get(f"/api/v1/businesses/{biz['id']}", headers=h).status_code == 404
    assert api.c.get("/api/v1/businesses", headers=h).json() == []


def test_owner_cannot_be_added_as_member(api, owner_headers, biz):
    r = api.c.post(members_url(biz["id"]), headers=owner_headers, json={
        "email": "owner@example.com", "role_id": api.role_id(owner_headers, biz["id"], "accountant")})
    assert r.status_code == 422


def test_custom_role_lifecycle(api, owner_headers, biz):
    bid = biz["id"]
    url = f"/api/v1/businesses/{bid}/roles"
    r = api.c.post(url, headers=owner_headers,
                   json={"name": "Front Desk", "permissions": ["customers.view", "invoices.view"]})
    assert r.status_code == 201
    role = r.json()
    assert role["code"] == "custom_front_desk" and not role["is_system"]
    assert api.c.post(url, headers=owner_headers,
                      json={"name": "Front Desk", "permissions": []}).status_code == 409

    h, m = api.member_session(owner_headers, bid, "desk@example.com", "custom_front_desk")
    assert set(m["effective_permissions"]) == {"customers.view", "invoices.view"}

    r = api.c.patch(f"{url}/{role['id']}", headers=owner_headers,
                    json={"permissions": ["customers.view", "business.view"]})
    assert r.status_code == 200
    me = api.c.get("/api/v1/auth/me", headers=h).json()
    assert set(me["businesses"][0]["permissions"]) == {"customers.view", "business.view"}

    assert api.c.delete(f"{url}/{role['id']}", headers=owner_headers).status_code == 409
    api.c.delete(f"{members_url(bid)}/{m['id']}", headers=owner_headers)
    assert api.c.delete(f"{url}/{role['id']}", headers=owner_headers).status_code == 204


def test_system_roles_are_immutable(api, owner_headers, biz):
    rid = api.role_id(owner_headers, biz["id"], "accountant")
    url = f"/api/v1/businesses/{biz['id']}/roles/{rid}"
    assert api.c.patch(url, headers=owner_headers, json={"permissions": []}).status_code == 403
    assert api.c.delete(url, headers=owner_headers).status_code == 403


def test_member_update_changes_role_and_branches(api, owner_headers, biz):
    bid = biz["id"]
    burl = f"/api/v1/businesses/{bid}/branches"
    b1 = api.c.post(burl, headers=owner_headers, json={"code": "B1", "name": "One"}).json()
    b2 = api.c.post(burl, headers=owner_headers, json={"code": "B2", "name": "Two"}).json()
    _, m = api.member_session(owner_headers, bid, "mv@example.com", "billing_operator",
                              all_branches=False, branch_ids=[b1["id"]],
                              grant_permissions=["reports.view"])
    url = f"{members_url(bid)}/{m['id']}"
    r = api.c.patch(url, headers=owner_headers, json={
        "role_id": api.role_id(owner_headers, bid, "accountant"),
        "branch_ids": [b1["id"], b2["id"]], "grant_permissions": ["users.view"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["role"]["code"] == "accountant"
    assert sorted(out["branch_ids"]) == sorted([b1["id"], b2["id"]])
    assert out["grant_permissions"] == ["users.view"]
    r = api.c.patch(url, headers=owner_headers, json={"branch_ids": [b2["id"]]})
    assert r.json()["branch_ids"] == [b2["id"]]
