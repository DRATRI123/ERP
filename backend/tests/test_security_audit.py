"""Phase 10: automated security audit over the *whole* API surface.

Instead of testing endpoints one by one, these tests enumerate every route the application
serves and check the access-control rules against each, so a new endpoint that forgets a
permission check fails here:

* only an explicit allow-list of routes is reachable without credentials;
* every other route answers 401 without (or with a forged / expired / wrong-type) token;
* a member with no permissions beyond viewing the business cannot change anything;
* nobody can reach another business's data: every business-scoped route answers 404;
* non-owners get 403 on every owner-only route;
* hostile input never produces a server error, and errors never leak internals.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi.routing import APIRoute

from app.core.config import get_settings
from app.main import API_PREFIX, create_app

# Reachable without a user session or API key — each one is safe by design (credential
# endpoints, provider webhooks verified by signature, probes, public catalogues).
PUBLIC_ROUTES = {
    ("POST", "/auth/login"), ("POST", "/auth/login/mfa"), ("POST", "/auth/refresh"),
    ("POST", "/auth/accept-invite"), ("POST", "/auth/reset-password"),
    ("GET", "/hooks/google/oauth/callback"), ("GET", "/hooks/whatsapp_cloud/{integration_id}"),
    ("POST", "/hooks/{provider}/{integration_id}"),
    ("GET", "/developer/scopes"), ("GET", "/developer/events"), ("POST", "/oauth/token"),
    ("GET", "/ready"), ("GET", "/metrics"), ("GET", "/system/tls-ask"), ("GET", "/domain-info"),
    ("GET", "/health"),
    # Link-only order catalogue: a 32-byte secret token, rate-limited orders that only create drafts.
    ("GET", "/public/catalogue/{token}"), ("POST", "/public/catalogue/{token}/orders"),
    # Balance confirmation link: a 24-byte secret token, valid 90 days, answers only once.
    ("GET", "/public/confirm/{token}"), ("POST", "/public/confirm/{token}"),
    # Product photos by signed link (HMAC of the file id with the server secret).
    ("GET", "/public/img/{file_id}"),
}
AUTH_DEPS = {"get_any_principal", "get_principal", "require_owner"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _routes() -> list[tuple[str, str, APIRoute]]:
    out = []
    for inc in create_app().routes:
        for r in getattr(getattr(inc, "original_router", None), "routes", []):
            if isinstance(r, APIRoute):
                out += [(m, r.path, r) for m in sorted(r.methods)]
    # Routers registered directly on the app (older FastAPI) are covered too.
    out += [(m, r.path.removeprefix(API_PREFIX), r) for r in create_app().routes
            if isinstance(r, APIRoute) for m in r.methods]
    unique = {(m, p): r for m, p, r in out}
    return [(m, p, r) for (m, p), r in sorted(unique.items(), key=lambda kv: (kv[0][1], kv[0][0]))]


ROUTES = _routes()


def _dep_names(dependant, acc: set[str]) -> set[str]:
    for d in dependant.dependencies:
        acc.add(getattr(d.call, "__name__", ""))
        _dep_names(d, acc)
    return acc


def _fill(path: str, business_id: str, **fixed: str) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name == "business_id":
            return business_id
        if name in fixed:
            return fixed[name]
        if name == "kind":
            return "products"
        if name == "provider":
            return "razorpay"
        return str(uuid.uuid4())
    return API_PREFIX + re.sub(r"\{(\w+)\}", repl, path)


def _call(client, method: str, url: str, headers: dict | None = None):
    kw = {"headers": headers or {}}
    if method in ("POST", "PUT", "PATCH"):
        kw["json"] = {}
    return client.request(method, url, **kw)


def test_route_inventory_is_complete():
    assert len(ROUTES) > 200
    unauthenticated = {(m, p) for m, p, r in ROUTES if not (_dep_names(r.dependant, set()) & AUTH_DEPS)}
    assert unauthenticated == PUBLIC_ROUTES, (
        f"new public routes: {unauthenticated - PUBLIC_ROUTES}; now protected: {PUBLIC_ROUTES - unauthenticated}")


def test_every_protected_route_rejects_missing_and_forged_tokens(client, owner):
    s = get_settings()
    now = datetime.now(UTC)
    base = {"iss": "erp", "sub": str(owner.id), "sid": str(uuid.uuid4()), "iat": now,
            "exp": now + timedelta(minutes=5), "jti": "x"}
    forged = {
        "none": {"Authorization": "Bearer " + jwt.encode({**base, "typ": "access"}, key=None, algorithm="none")},
        "wrong key": {"Authorization": "Bearer " + jwt.encode({**base, "typ": "access"}, "k" * 40, algorithm="HS256")},
        "expired": {"Authorization": "Bearer " + jwt.encode(
            {**base, "typ": "access", "exp": now - timedelta(seconds=1)}, s.secret_key, algorithm="HS256")},
        "mfa token": {"Authorization": "Bearer " + jwt.encode({**base, "typ": "mfa"}, s.secret_key, algorithm="HS256")},
        "no session": {"Authorization": "Bearer " + jwt.encode({**base, "typ": "access"}, s.secret_key, algorithm="HS256")},
        "bad api key": {"Authorization": "Bearer erp_abcdef12_notarealsecretnotarealsecret"},
        "garbage": {"Authorization": "Bearer ' OR 1=1 --"},
    }
    failures = []
    biz = str(uuid.uuid4())
    for method, path, _ in ROUTES:
        if (method, path) in PUBLIC_ROUTES:
            continue
        url = _fill(path, biz)
        for label, headers in [("missing", {}), *forged.items()]:
            r = _call(client, method, url, headers)
            if r.status_code != 401:
                failures.append(f"{method} {path} [{label}] -> {r.status_code}")
    assert not failures, "\n".join(failures[:30])


def _business_routes(methods: set[str] | None = None):
    return [(m, p) for m, p, _ in ROUTES if p.startswith("/businesses/{business_id}")
            and (methods is None or m in methods)]


def test_no_route_reaches_another_business(api, owner_headers, client):
    """A business administrator of A gets 404 for *every* route of business B — reads and
    writes alike, so not even the existence of B's records leaks."""
    a = api.create_business(owner_headers, name="Clinic A")
    b = api.create_business(owner_headers, name="Pharmacy B")
    admin_a, _ = api.member_session(owner_headers, a["id"], "admin-a@example.com", "business_admin")
    leaks = []
    for method, path in _business_routes():
        r = _call(client, method, _fill(path, b["id"]), admin_a)
        if r.status_code != 404:
            leaks.append(f"{method} {path} -> {r.status_code}")
    assert not leaks, "\n".join(leaks[:30])
    # Sanity: the same member does reach their own business.
    assert client.get(f"/api/v1/businesses/{a['id']}", headers=admin_a).status_code == 200


def test_view_only_member_cannot_change_anything(api, owner_headers, client):
    biz = api.create_business(owner_headers, name="Locked Down")
    r = client.post(f"/api/v1/businesses/{biz['id']}/roles", headers=owner_headers,
                    json={"name": "Look only", "permissions": ["business.view"]})
    assert r.status_code == 201, r.text
    role_id = r.json()["id"]
    r = client.post(f"/api/v1/businesses/{biz['id']}/members", headers=owner_headers,
                    json={"email": "viewer@example.com", "full_name": "Viewer", "role_id": role_id})
    assert r.status_code == 201, r.text
    from tests.conftest import USER_PASSWORD

    client.post("/api/v1/auth/accept-invite", json={"token": r.json()["invite_token"], "password": USER_PASSWORD})
    viewer = api.auth("viewer@example.com", USER_PASSWORD)

    allowed = []
    for method, path in _business_routes(MUTATING):
        r = _call(client, method, _fill(path, biz["id"]), viewer)
        if r.status_code not in (403, 404):
            allowed.append(f"{method} {path} -> {r.status_code}")
    assert not allowed, "\n".join(allowed[:30])
    view_ok = {p for m, p, r in ROUTES if "require_business_view" in _dep_names(r.dependant, set())}
    for method, path in _business_routes({"GET"}):
        if path in view_ok:  # needs only business.view (business profile, branches, series…)
            continue
        r = _call(client, method, _fill(path, biz["id"]), viewer)
        if r.status_code == 200 and path.count("{") == 1:
            body = r.json()
            items = body.get("items", body) if isinstance(body, dict) else body
            assert not items, f"GET {path} returned data to a view-only member"


def test_owner_only_routes_refuse_members(api, owner_headers, client):
    biz = api.create_business(owner_headers)
    admin, _ = api.member_session(owner_headers, biz["id"], "boss2@example.com", "business_admin")
    owner_only = [(m, p) for m, p, r in ROUTES if "require_owner" in _dep_names(r.dependant, set())]
    assert len(owner_only) >= 15
    bad = []
    for method, path in owner_only:
        r = _call(client, method, _fill(path, biz["id"]), admin)
        if r.status_code not in (403, 404):
            bad.append(f"{method} {path} -> {r.status_code}")
    assert not bad, "\n".join(bad)


HOSTILE = ["' OR '1'='1", "'; DROP TABLE users; --", "%' UNION SELECT password_hash FROM users --",
           "<script>alert(1)</script>", "../../../../etc/passwd", "\x00", "𝔘𝔫𝔦𝔠𝔬𝔡𝔢 🙂", "a" * 5000,
           "${jndi:ldap://x}", "{{7*7}}"]


def test_hostile_input_never_causes_server_errors(api, owner_headers, client):
    biz = api.create_business(owner_headers)
    errors = []
    list_routes = [p for m, p in _business_routes({"GET"}) if p.count("{") == 1]
    for path in list_routes:
        url = _fill(path, biz["id"])
        for value in HOSTILE:
            r = client.get(url, headers=owner_headers, params={"q": value, "search": value, "limit": "50"})
            if r.status_code >= 500:
                errors.append(f"GET {path} q={value[:20]!r} -> {r.status_code}")
    for value in HOSTILE[:6]:
        r = client.post(f"/api/v1/businesses/{biz['id']}/customers", headers=owner_headers,
                        json={"name": value[:200] or "x", "email": value, "gstin": value})
        if r.status_code >= 500:
            errors.append(f"POST customers {value[:20]!r} -> {r.status_code}")
        r = client.post("/api/v1/auth/login", json={"email": value, "password": value})
        if r.status_code >= 500:
            errors.append(f"login {value[:20]!r} -> {r.status_code}")
    assert not errors, "\n".join(errors[:30])
    # NUL characters are refused cleanly at the edge, the same on SQLite and PostgreSQL.
    r = client.get(f"/api/v1/businesses/{biz['id']}/products", headers=owner_headers, params={"q": "a\x00b"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_characters"
    r = client.post(f"/api/v1/businesses/{biz['id']}/customers", headers=owner_headers, json={"name": "a\u0000b"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_characters"
    # The data survived the injection attempts.
    assert client.get("/api/v1/auth/me", headers=owner_headers).status_code == 200


def test_errors_do_not_leak_internals(client, owner_headers, monkeypatch):
    from app.api.routes import health

    def boom(*a, **k):
        raise RuntimeError("secret connection string postgresql://erp:hunter2@db")
    monkeypatch.setattr(health, "check_integrity", boom)
    r = client.get("/api/v1/system/db-integrity", headers=owner_headers)
    assert r.status_code == 500
    assert "hunter2" not in r.text and "Traceback" not in r.text and "RuntimeError" not in r.text
    assert r.json()["error"]["code"] == "internal_error"
    assert r.json()["error"]["request_id"] == r.headers["x-request-id"]
    # Validation errors describe the field, never the server.
    r = client.post("/api/v1/auth/login", json={"email": "x"})
    assert r.status_code == 422 and "Traceback" not in r.text


def test_responses_never_contain_secrets(api, owner_headers, client):
    """Password hashes, TOTP secrets, refresh-token hashes and stored credentials never appear
    in any GET response the owner can see."""
    biz = api.create_business(owner_headers)
    client.post(f"/api/v1/businesses/{biz['id']}/integrations", headers=owner_headers, json={
        "provider": "smtp", "name": "mail", "config": {"host": "smtp.example.com", "port": "587",
        "security": "starttls", "from_email": "a@example.com", "username": "a@example.com"},
        "credentials": {"password": "sup3r-secret-pw"}})
    from tests.test_security_ops import _enable_mfa

    secret, _ = _enable_mfa(api, owner_headers)
    from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD
    from tests.test_security_ops import code_now

    step = client.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}).json()
    tok = client.post("/api/v1/auth/login/mfa", json={"mfa_token": step["mfa_token"], "code": code_now(secret, 1)}).json()
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    blob = []
    for method, path in [(m, p) for m, p, _ in ROUTES if m == "GET" and p.count("{") <= 1
                         and (p.count("{") == 0 or p.startswith("/businesses/{business_id}"))]:
        if (method, path) in PUBLIC_ROUTES or path.endswith(("/print", "/download")) or "export" in path:
            continue
        r = client.get(_fill(path, biz["id"]), headers=h)
        if r.status_code == 200:
            blob.append(r.text)
    text = "\n".join(blob)
    assert len(blob) > 40
    for needle in ("$argon2", "sup3r-secret-pw", secret, tok["refresh_token"], "totp_secret", "password_hash",
                   "refresh_token_hash", "credentials_encrypted"):
        assert needle not in text, needle
