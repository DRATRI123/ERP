"""Authentication: login, lockout, token rotation and reuse detection, sessions, invites."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, USER_PASSWORD


def test_no_public_registration(client):
    assert client.post("/api/v1/auth/register", json={}).status_code in (404, 405)
    assert client.post("/api/v1/users", json={}).status_code in (401, 405)


def test_login_success_and_me(api, owner):
    tokens = api.login(OWNER_EMAIL.upper(), OWNER_PASSWORD)  # email is case-insensitive
    assert tokens["token_type"] == "bearer" and tokens["refresh_token"]
    r = api.c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 200
    assert r.json()["user"]["is_owner"] is True
    assert r.json()["user"]["email"] == OWNER_EMAIL


def test_wrong_password_and_unknown_user_are_indistinguishable(client, owner):
    a = client.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": "nope-nope-1"})
    b = client.post("/api/v1/auth/login", json={"email": "ghost@x.com", "password": "nope-nope-1"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["message"] == b.json()["error"]["message"]


def test_lockout_after_repeated_failures(client, owner):
    for _ in range(get_settings().login_max_failures):
        r = client.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": "bad-pass-1"})
        assert r.status_code == 401
    # Even the correct password is refused while locked.
    r = client.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert r.status_code == 429 and r.json()["error"]["code"] == "account_locked"


def test_lockout_expires(client, owner, db):
    from app.modules.users.models import User

    for _ in range(get_settings().login_max_failures):
        client.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": "bad-pass-1"})
    u = db.get(User, owner.id)
    u.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    r = client.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert r.status_code == 200


def test_refresh_rotates_and_detects_reuse(api, owner):
    t1 = api.login(OWNER_EMAIL, OWNER_PASSWORD)
    r = api.c.post("/api/v1/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r.status_code == 200
    t2 = r.json()
    assert t2["refresh_token"] != t1["refresh_token"]

    # Replaying the old token = theft signal: the session is revoked entirely.
    r = api.c.post("/api/v1/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r.status_code == 401
    r = api.c.post("/api/v1/auth/refresh", json={"refresh_token": t2["refresh_token"]})
    assert r.status_code == 401
    r = api.c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t2['access_token']}"})
    assert r.status_code == 401


def test_logout_invalidates_access_token_immediately(api, owner):
    h = api.auth(OWNER_EMAIL, OWNER_PASSWORD)
    assert api.c.post("/api/v1/auth/logout", headers=h).status_code == 204
    assert api.c.get("/api/v1/auth/me", headers=h).status_code == 401


def test_tampered_expired_and_missing_tokens_rejected(api, owner):
    assert api.c.get("/api/v1/auth/me").status_code == 401
    h = api.auth(OWNER_EMAIL, OWNER_PASSWORD)
    token = h["Authorization"].split()[1]
    bad = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    assert api.c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401

    claims = jwt.decode(token, options={"verify_signature": False})
    claims["exp"] = datetime.now(UTC) - timedelta(minutes=1)
    expired = jwt.encode(claims, get_settings().secret_key, algorithm="HS256")
    r = api.c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"

    forged = jwt.encode(claims | {"exp": datetime.now(UTC) + timedelta(hours=1)},
                        "x" * 64, algorithm="HS256")
    assert api.c.get("/api/v1/auth/me",
                     headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_sessions_list_and_revoke_other_device(api, owner):
    h1 = api.auth(OWNER_EMAIL, OWNER_PASSWORD)
    h2 = api.auth(OWNER_EMAIL, OWNER_PASSWORD)
    sessions = api.c.get("/api/v1/auth/sessions", headers=h1).json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    other = next(s for s in sessions if not s["current"])
    assert api.c.delete(f"/api/v1/auth/sessions/{other['id']}", headers=h1).status_code == 204
    assert api.c.get("/api/v1/auth/me", headers=h2).status_code == 401
    assert api.c.get("/api/v1/auth/me", headers=h1).status_code == 200


def test_change_password_revokes_other_sessions(api, owner):
    h1 = api.auth(OWNER_EMAIL, OWNER_PASSWORD)
    h2 = api.auth(OWNER_EMAIL, OWNER_PASSWORD)
    new = "Brand-New-Pass-77"
    r = api.c.post("/api/v1/auth/change-password", headers=h1,
                   json={"current_password": "wrong-one-1", "new_password": new})
    assert r.status_code == 422 and r.json()["error"]["code"] == "wrong_password"
    r = api.c.post("/api/v1/auth/change-password", headers=h1,
                   json={"current_password": OWNER_PASSWORD, "new_password": new})
    assert r.status_code == 204
    assert api.c.get("/api/v1/auth/me", headers=h1).status_code == 200
    assert api.c.get("/api/v1/auth/me", headers=h2).status_code == 401
    api.login(OWNER_EMAIL, new)


def test_password_policy(api, owner):
    h = api.auth(OWNER_EMAIL, OWNER_PASSWORD)
    for weak in ("short1", "aaaaaaaaaaaa", "owner-is-my-password"):
        r = api.c.post("/api/v1/auth/change-password", headers=h,
                       json={"current_password": OWNER_PASSWORD, "new_password": weak})
        assert r.status_code == 422, weak


def test_login_history_records_success_and_failure(api, owner):
    api.c.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": "bad-pass-1"})
    h = api.auth(OWNER_EMAIL, OWNER_PASSWORD)
    items = api.c.get("/api/v1/auth/login-history", headers=h).json()["items"]
    assert [i["success"] for i in items[:2]] == [True, False]
    assert items[1]["failure_reason"] == "bad_credentials"


def test_invite_flow_single_use(api, owner_headers):
    biz = api.create_business(owner_headers)
    out = api.add_member(owner_headers, biz["id"], "clerk@example.com", "billing_operator")
    token = out["invite_token"]
    assert token and out["member"]["user"]["status"] == "invited"

    # Cannot log in before accepting.
    r = api.c.post("/api/v1/auth/login", json={"email": "clerk@example.com", "password": "x" * 12})
    assert r.status_code == 401

    body = {"token": token, "password": USER_PASSWORD}
    assert api.c.post("/api/v1/auth/accept-invite", json=body).status_code == 204
    assert api.c.post("/api/v1/auth/accept-invite", json=body).status_code == 422  # used
    api.login("clerk@example.com", USER_PASSWORD)


def test_password_reset_by_owner(api, owner_headers):
    biz = api.create_business(owner_headers)
    h, member = api.member_session(owner_headers, biz["id"], "acct@example.com", "accountant")
    uid = member["user"]["id"]
    r = api.c.post(f"/api/v1/users/{uid}/password-reset", headers=owner_headers)
    assert r.status_code == 200
    token = r.json()["reset_token"]
    new = "Reset-Pass-2026"
    assert api.c.post("/api/v1/auth/reset-password",
                      json={"token": token, "password": new}).status_code == 204
    assert api.c.get("/api/v1/auth/me", headers=h).status_code == 401  # sessions revoked
    api.login("acct@example.com", new)


def test_suspended_user_is_locked_out_immediately(api, owner_headers):
    biz = api.create_business(owner_headers)
    h, member = api.member_session(owner_headers, biz["id"], "temp@example.com", "sales_executive")
    uid = member["user"]["id"]
    assert api.c.post(f"/api/v1/users/{uid}/suspend", headers=owner_headers).status_code == 200
    assert api.c.get("/api/v1/auth/me", headers=h).status_code == 401
    r = api.c.post("/api/v1/auth/login", json={"email": "temp@example.com", "password": USER_PASSWORD})
    assert r.status_code == 401 and r.json()["error"]["code"] == "account_inactive"
    assert api.c.post(f"/api/v1/users/{uid}/reactivate", headers=owner_headers).status_code == 200
    api.login("temp@example.com", USER_PASSWORD)


def test_owner_cannot_be_suspended(api, owner, owner_headers):
    r = api.c.post(f"/api/v1/users/{owner.id}/suspend", headers=owner_headers)
    assert r.status_code == 403
