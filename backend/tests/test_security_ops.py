"""Phase 8: two-factor auth, login protection, security headers, custom domains, encrypted
backups with restore drills, readiness / metrics / system health."""

from __future__ import annotations

import io
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, inspect, select, text

from app.core.config import get_settings
from app.modules.system import backupcrypt, backups, domains, totp
from app.modules.system.models import BackupRun, BackupStatus, CustomDomain, DomainStatus, SecurityEvent
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, USER_PASSWORD, Shop
from tests.test_billing import customer, invoice, stocked

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32("12345678901234567890")


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()

    def set_(**kw):
        for k, v in kw.items():
            monkeypatch.setattr(s, k, v)
    return set_


def code_now(secret: str, offset_steps: int = 0) -> str:
    return totp.hotp(secret, totp.current_step() + offset_steps)


# ---------------------------------------------------------------------------- TOTP


def test_totp_matches_rfc6238_vectors():
    # RFC 6238 appendix B (SHA-1), last six digits.
    for t, expected in [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"),
                        (2000000000, "279037")]:
        assert totp.hotp(RFC_SECRET, t // 30) == expected
        assert totp.verify(RFC_SECRET, expected, last_step=None, now=t) == t // 30
    # Drift of one step either side is accepted, two is not; a used step is never accepted again.
    assert totp.verify(RFC_SECRET, "287082", last_step=None, now=59 + 30) == 1
    assert totp.verify(RFC_SECRET, "287082", last_step=None, now=59 + 60) is None
    assert totp.verify(RFC_SECRET, "287082", last_step=1, now=59) is None
    assert totp.verify(RFC_SECRET, "28708", last_step=None, now=59) is None
    uri = totp.provisioning_uri("ABC", "a@example.com")
    assert uri.startswith("otpauth://totp/Private%20ERP%3Aa%40example.com?secret=ABC&issuer=")
    assert "<svg" in totp.qr_svg(uri)


def _enable_mfa(api, headers) -> tuple[str, list[str]]:
    r = api.c.post("/api/v1/auth/2fa/setup", json={"password": "wrong-password-1"}, headers=headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "wrong_password"
    setup = api.c.post("/api/v1/auth/2fa/setup", json={"password": OWNER_PASSWORD}, headers=headers).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and "<svg" in setup["qr_svg"]
    bad = api.c.post("/api/v1/auth/2fa/enable", json={"code": "000000"}, headers=headers)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_mfa_code"
    r = api.c.post("/api/v1/auth/2fa/enable", json={"code": code_now(setup["secret"])}, headers=headers)
    assert r.status_code == 200, r.text
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    return setup["secret"], codes


def test_mfa_login_flow_recovery_codes_and_lockout(api, owner, owner_headers, db):
    other = api.auth(OWNER_EMAIL, OWNER_PASSWORD)  # a second session
    secret, codes = _enable_mfa(api, owner_headers)
    assert api.c.get("/api/v1/auth/me", headers=other).status_code == 401  # other sessions signed out
    st = api.c.get("/api/v1/auth/2fa", headers=owner_headers).json()
    assert st["enabled"] and st["recovery_codes_left"] == 10
    assert api.c.get("/api/v1/auth/me", headers=owner_headers).json()["user"]["mfa_enabled"] is True

    # Password alone no longer yields tokens.
    step1 = api.c.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}).json()
    assert step1["mfa_required"] is True and "access_token" not in step1 and "refresh_token" not in step1
    # The challenge token is not an access token.
    assert api.c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {step1['mfa_token']}"}).status_code == 401

    # The code used to enable 2FA cannot be replayed (same time step).
    r = api.c.post("/api/v1/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": code_now(secret)})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_mfa_code"
    r = api.c.post("/api/v1/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": code_now(secret, 1)})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] and r.json()["refresh_token"]

    # Recovery code: works once, normalised (case, dashes, spaces).
    step1 = api.c.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}).json()
    rc = codes[0].upper().replace("-", " ")
    r = api.c.post("/api/v1/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "recovery_code": rc})
    assert r.status_code == 200, r.text
    r = api.c.post("/api/v1/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "recovery_code": codes[0]})
    assert r.status_code == 401
    kinds = [e.kind for e in db.scalars(select(SecurityEvent).where(SecurityEvent.user_id == owner.id))]
    assert "2fa_enabled" in kinds and "recovery_code_used" in kinds
    db.rollback()  # release the SQLite read transaction before more requests

    # Wrong second factors count towards the account lockout; a correct password does not reset it.
    for attempt in range(1, 10):
        r = api.c.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
        if r.status_code == 429:
            break
        assert api.c.post("/api/v1/auth/login/mfa",
                          json={"mfa_token": r.json()["mfa_token"], "code": "123456"}).status_code == 401
    assert r.status_code == 429 and r.json()["error"]["code"] == "account_locked"
    assert attempt <= get_settings().login_max_failures  # earlier failures counted too
    db.expire_all()
    assert "account_locked" in [e.kind for e in db.scalars(select(SecurityEvent).where(
        SecurityEvent.user_id == owner.id))]


def test_mfa_token_is_bound_to_password_and_expires(api, owner, owner_headers, db, settings):
    from app.core.security import create_mfa_token

    secret, _ = _enable_mfa(api, owner_headers)
    step1 = api.c.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}).json()
    r = api.c.post("/api/v1/auth/change-password", headers=owner_headers,
                   json={"current_password": OWNER_PASSWORD, "new_password": "Brand-New-Pass-77!"})
    assert r.status_code == 204
    r = api.c.post("/api/v1/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": code_now(secret, 1)})
    assert r.status_code == 401 and r.json()["error"]["code"] == "mfa_token_invalid"
    settings(mfa_token_ttl_minutes=-1)
    expired, _ = create_mfa_token(owner.id, "irrelevant")
    r = api.c.post("/api/v1/auth/login/mfa", json={"mfa_token": expired, "code": code_now(secret, 1)})
    assert r.status_code == 401 and r.json()["error"]["code"] == "mfa_token_invalid"


def test_disable_and_regenerate_require_password_and_code(api, owner, owner_headers):
    secret, codes = _enable_mfa(api, owner_headers)
    r = api.c.post("/api/v1/auth/2fa/recovery-codes", headers=owner_headers,
                   json={"password": OWNER_PASSWORD, "code": "000000"})
    assert r.status_code == 422
    r = api.c.post("/api/v1/auth/2fa/recovery-codes", headers=owner_headers,
                   json={"password": OWNER_PASSWORD, "code": code_now(secret, 1)})
    assert r.status_code == 200 and set(r.json()["recovery_codes"]).isdisjoint(codes)
    r = api.c.post("/api/v1/auth/2fa/disable", headers=owner_headers,
                   json={"password": "Not-The-Password-1", "code": code_now(secret, -1)})
    assert r.status_code == 422
    r = api.c.post("/api/v1/auth/2fa/disable", headers=owner_headers,
                   json={"password": OWNER_PASSWORD, "code": code_now(secret, -1)})
    assert r.status_code == 422  # step -1 is older than the last used step (+1): replay protection
    from app.db.session import SessionLocal
    from app.modules.users.models import User

    s = SessionLocal()  # pretend a few minutes have passed since the last code was used
    u = s.scalar(select(User).where(User.email == OWNER_EMAIL))
    u.totp_last_step = totp.current_step() - 5
    s.commit()
    s.close()
    r = api.c.post("/api/v1/auth/2fa/disable", headers=owner_headers,
                   json={"password": OWNER_PASSWORD, "code": code_now(secret)})
    assert r.status_code == 204, r.text
    out = api.login(OWNER_EMAIL, OWNER_PASSWORD)
    assert out["access_token"] and not out.get("mfa_required")


def test_owner_can_reset_member_mfa(api, owner_headers):
    biz = api.create_business(owner_headers)
    h, member = api.member_session(owner_headers, biz["id"], "staff@example.com", "billing_operator")
    setup = api.c.post("/api/v1/auth/2fa/setup", json={"password": USER_PASSWORD}, headers=h).json()
    api.c.post("/api/v1/auth/2fa/enable", json={"code": code_now(setup["secret"])}, headers=h)
    assert api.c.post("/api/v1/auth/login", json={"email": "staff@example.com",
                                                  "password": USER_PASSWORD}).json()["mfa_required"]
    uid = member["user_id"] if "user_id" in member else member["user"]["id"]
    assert api.c.delete(f"/api/v1/system/users/{uid}/2fa", headers=h).status_code == 403
    assert api.c.delete(f"/api/v1/system/users/{uid}/2fa", headers=owner_headers).status_code == 204
    assert api.login("staff@example.com", USER_PASSWORD)["access_token"]


# ---------------------------------------------------------------------------- login protection


def test_new_device_alert_and_email(api, owner, db, monkeypatch, settings):
    from app.modules.system import security

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(security, "_smtp_send", lambda to, subj, body: sent.append((to, subj, body)))
    api.login(OWNER_EMAIL, OWNER_PASSWORD)  # first ever sign-in: no alert
    api.login(OWNER_EMAIL, OWNER_PASSWORD)  # same device: no alert
    r = api.c.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
                   headers={"User-Agent": "Mozilla/5.0 (Android 15) NewPhone"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    events = api.c.get("/api/v1/auth/security-events", headers=headers).json()["items"]
    assert [e["kind"] for e in events] == ["new_device_login"]
    assert "NewPhone" in events[0]["detail"]["user_agent"]

    assert security.deliver_alerts(db) == {}  # no system SMTP configured
    settings(smtp_host="smtp.example.com", smtp_from="erp@example.com")
    assert security.deliver_alerts(db) == {"alerts_sent": 1, "alerts_failed": 0}
    assert sent[0][0] == OWNER_EMAIL and "New sign-in" in sent[0][1] and "NewPhone" in sent[0][2]
    assert security.deliver_alerts(db) == {"alerts_sent": 0, "alerts_failed": 0}  # once only


def test_login_rate_limited_per_ip(api, owner, settings):
    settings(login_ip_limit=3)
    for _ in range(3):
        assert api.c.post("/api/v1/auth/login", json={"email": "nobody@example.com",
                                                      "password": "whatever-123"}).status_code == 401
    r = api.c.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert r.status_code == 429 and r.json()["error"]["code"] == "ip_rate_limited"
    assert int(r.headers["Retry-After"]) > 0


def test_refresh_cookie_with_csrf(api, owner, settings):
    settings(auth_cookie=True)
    r = api.c.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert r.status_code == 200 and "refresh_token" not in r.json()
    cookies = r.headers.get_list("set-cookie")
    refresh_cookie = next(c for c in cookies if c.startswith("erp_refresh="))
    assert "HttpOnly" in refresh_cookie and "SameSite=strict" in refresh_cookie
    assert "Path=/api/v1/auth" in refresh_cookie
    csrf = api.c.cookies.get("erp_csrf")
    assert csrf
    assert api.c.post("/api/v1/auth/refresh", json={}).status_code == 403  # no CSRF header
    assert api.c.post("/api/v1/auth/refresh", json={}, headers={"X-CSRF-Token": "forged"}).status_code == 403
    r = api.c.post("/api/v1/auth/refresh", json={}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["access_token"] and "refresh_token" not in r.json()
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = api.c.post("/api/v1/auth/logout", headers=h)
    assert r.status_code == 204
    assert any(c.startswith('erp_refresh=""') or "Max-Age=0" in c for c in r.headers.get_list("set-cookie"))


# ---------------------------------------------------------------------------- headers / hosts


def test_security_headers(client):
    r = client.get("/api/v1/health")
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert "camera=()" in r.headers["permissions-policy"]
    assert "strict-transport-security" not in r.headers  # only with ERP_FORCE_HTTPS
    docs = client.get("/api/v1/docs")
    assert "cdn.jsdelivr.net" in docs.headers["content-security-policy"]


def test_force_https_redirect_and_hsts(client, settings):
    settings(force_https=True)
    r = client.get("/api/v1/auth/me", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"].startswith("https://testserver/api/v1/auth/me")
    assert client.get("/api/v1/health").status_code == 200  # probes stay on plain HTTP
    # Internal service-to-service calls (realtime → http://api:8000) are not redirected.
    assert client.get("/api/v1/auth/me", headers={"Host": "api:8000"}).status_code == 401
    secure = client.get("https://testserver/api/v1/health")
    assert secure.headers["strict-transport-security"].startswith("max-age=63072000")


@pytest.fixture
def dns(monkeypatch):
    records: dict[str, list[str]] = {}

    def lookup(name: str) -> list[str]:
        if name == "_erp-verify.broken.example.org":
            raise LookupError("DNS lookup failed: Timeout")
        return records.get(name, [])
    domains.set_txt_lookup(lookup)
    yield records
    domains.set_txt_lookup(None)


def test_custom_domain_lifecycle(api, owner_headers, client, dns, db, settings):
    settings(allowed_hosts=["erp.example.com", "testserver"], tls_ask_token="ask-secret")
    biz = api.create_business(owner_headers)
    r = client.post("/api/v1/system/domains", headers=owner_headers,
                    json={"hostname": "Billing.Ayur-Clinic.IN.", "default_business_id": biz["id"]})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["hostname"] == "billing.ayur-clinic.in" and d["status"] == "pending"
    assert d["dns_record"]["name"] == "_erp-verify.billing.ayur-clinic.in"
    for bad in ("localhost", "10.0.0.1", "-bad-.com", "erp.example.com"):
        assert client.post("/api/v1/system/domains", headers=owner_headers,
                           json={"hostname": bad}).status_code == 422, bad
    assert client.post("/api/v1/system/domains", headers=owner_headers,
                       json={"hostname": "billing.ayur-clinic.in"}).status_code == 409

    ask = "/api/v1/system/tls-ask"
    # Unverified: no certificate, and requests for that host are refused.
    assert client.get(ask, params={"domain": "billing.ayur-clinic.in", "token": "ask-secret"}).status_code == 404
    assert client.get("/api/v1/health", headers={"Host": "billing.ayur-clinic.in"}).status_code == 200  # probe
    assert client.get("/api/v1/domain-info", headers={"Host": "billing.ayur-clinic.in"}).status_code == 421
    assert client.get(ask, params={"domain": "erp.example.com", "token": "wrong"}).status_code == 403
    assert client.get(ask, params={"domain": "erp.example.com", "token": "ask-secret"}).status_code == 200
    assert client.get(ask, params={"domain": "evil.example.net", "token": "ask-secret"}).status_code == 404

    r = client.post(f"/api/v1/system/domains/{d['id']}/verify", headers=owner_headers).json()
    assert r["status"] == "pending" and r["last_error"] == "TXT record not found"
    dns["_erp-verify.billing.ayur-clinic.in"] = ["something-else", d["dns_record"]["value"]]
    r = client.post(f"/api/v1/system/domains/{d['id']}/verify", headers=owner_headers).json()
    assert r["status"] == "active" and r["verified_at"]
    assert client.get(ask, params={"domain": "billing.ayur-clinic.in", "token": "ask-secret"}).status_code == 200
    info = client.get("/api/v1/domain-info", headers={"Host": "billing.ayur-clinic.in"}).json()
    assert info == {"custom_domain": True, "default_business_id": biz["id"]}
    # The domain grants nothing: unauthenticated requests on it are still rejected.
    assert client.get(f"/api/v1/businesses/{biz['id']}", headers={"Host": "billing.ayur-clinic.in"}).status_code == 401

    # The record disappears: disabled after three failed daily checks.
    dns.clear()
    dom = db.get(CustomDomain, uuid.UUID(d["id"]))
    for i in range(domains.MAX_FAILURES):
        dom.last_checked_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
        assert domains.reverify_due(db) == {"checked": 1, "failed": 1}
        db.refresh(dom)
    assert dom.status == DomainStatus.DISABLED
    assert domains.reverify_due(db) == {"checked": 0, "failed": 0}  # disabled ones are left alone
    domains.host_cache.invalidate()
    assert client.get("/api/v1/domain-info", headers={"Host": "billing.ayur-clinic.in"}).status_code == 421

    # Re-enabling needs a fresh proof.
    r = client.patch(f"/api/v1/system/domains/{d['id']}", headers=owner_headers, json={"enabled": True}).json()
    assert r["status"] == "pending"
    dns["_erp-verify.billing.ayur-clinic.in"] = [d["dns_record"]["value"]]
    assert client.post(f"/api/v1/system/domains/{d['id']}/verify", headers=owner_headers).json()["status"] == "active"
    r = client.patch(f"/api/v1/system/domains/{d['id']}", headers=owner_headers, json={"default_business_id": None})
    assert r.json()["default_business_id"] is None

    # DNS errors are recorded, not raised.
    b = client.post("/api/v1/system/domains", headers=owner_headers, json={"hostname": "broken.example.org"}).json()
    r = client.post(f"/api/v1/system/domains/{b['id']}/verify", headers=owner_headers).json()
    assert r["status"] == "pending" and "Timeout" in r["last_error"]
    assert client.delete(f"/api/v1/system/domains/{b['id']}", headers=owner_headers).status_code == 204
    assert [x["hostname"] for x in client.get("/api/v1/system/domains", headers=owner_headers).json()] == [
        "billing.ayur-clinic.in"]


def test_unknown_host_rejected_and_owner_only(api, owner_headers, client, settings):
    settings(allowed_hosts=["erp.example.com"])
    assert client.get("/api/v1/auth/me", headers={"Host": "attacker.example"}).status_code == 421
    assert client.get("/api/v1/health", headers={"Host": "attacker.example"}).status_code == 200
    ok = client.get("/api/v1/auth/me", headers={**owner_headers, "Host": "erp.example.com:443"})
    assert ok.status_code == 200
    settings(allowed_hosts=[])
    biz = api.create_business(owner_headers)
    h, _ = api.member_session(owner_headers, biz["id"], "acct@example.com", "accountant")
    for method, path in [("get", "/system/domains"), ("get", "/system/backups"), ("get", "/system/health"),
                         ("post", "/system/backups"), ("get", "/system/backups/settings")]:
        assert getattr(client, method)(f"/api/v1{path}", headers=h).status_code == 403, path


# ---------------------------------------------------------------------------- backups


def test_backup_crypto_roundtrip_and_tamper_detection(tmp_path):
    key = backupcrypt.parse_key(backupcrypt.generate_key())
    data = __import__("os").urandom(backupcrypt.CHUNK * 2 + 12345)
    src, enc = tmp_path / "p", tmp_path / "p.erpbk"
    src.write_bytes(data)
    backupcrypt.encrypt_file(src, enc, key, {"payload": "x"})
    blob = enc.read_bytes()
    assert data[:4096] not in blob
    out = tmp_path / "out"
    assert backupcrypt.decrypt_file(enc, out, key)["payload"] == "x"
    assert out.read_bytes() == data

    def fails(content: bytes, k: bytes = key, msg: str = "") -> None:
        enc.write_bytes(content)
        with pytest.raises(backupcrypt.BackupCryptoError, match=msg):
            for _ in backupcrypt.decrypt_chunks(io.BytesIO(content), k):
                pass

    flipped = bytearray(blob)
    flipped[len(blob) // 2] ^= 1
    fails(bytes(flipped), msg="corrupt or was modified")
    # Truncated exactly at a chunk boundary: still detected (the final flag is missing).
    hdr_len = len(backupcrypt.MAGIC) + 4 + int.from_bytes(blob[len(backupcrypt.MAGIC):len(backupcrypt.MAGIC) + 4])
    first_chunk_end = hdr_len + 4 + backupcrypt.CHUNK + 16
    fails(blob[:first_chunk_end], msg="truncated")
    fails(blob + b"x", msg="Unexpected data|truncated")
    fails(blob, k=backupcrypt.parse_key(backupcrypt.generate_key()), msg="different key")
    # The header is authenticated too.
    tampered = blob.replace(b'"payload":"x"', b'"payload":"y"')
    fails(tampered, msg="corrupt or was modified")
    with pytest.raises(backupcrypt.BackupCryptoError):
        backupcrypt.parse_key("c2hvcnQ=")


def test_gfs_retention_plan():
    start = datetime(2026, 1, 1, 20, tzinfo=UTC)
    items = [(uuid.uuid4(), start + timedelta(days=i)) for i in range(120)]  # one per day
    items.append((uuid.uuid4(), items[-1][1] - timedelta(hours=2)))  # two on the last day
    keep = backups.plan_retention(items, keep_daily=7, keep_weekly=4, keep_monthly=6)
    kept = sorted(ts for rid, ts in items if rid in keep)
    newest = max(ts for _, ts in items)
    assert newest in kept
    assert len([t for t in kept if t > newest - timedelta(days=7)]) == 7  # newest per day only
    months = {(t.year, t.month) for t in kept}
    assert len(months) >= 4 and len(kept) <= 7 + 4 + 6
    assert backups.plan_retention([], keep_daily=1, keep_weekly=0, keep_monthly=0) == set()


def _counts(engine) -> dict[str, int]:
    out = {}
    with engine.connect() as c:
        for t in inspect(engine).get_table_names():
            out[t] = c.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
    return out


def _sample_books(api, owner_headers) -> Shop:
    s = Shop(api, owner_headers)
    p = stocked(s, "ashwagandha", selling_price="450")
    c = customer(s, "Restore Clinic")
    invoice(s, [{"product_id": p, "quantity": "3"}], party_id=c["id"])
    return s


def test_backup_worker_download_and_sqlite_restore_drill(api, owner_headers, client, engine, tmp_path, db):
    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite restore drill")
    s = _sample_books(api, owner_headers)
    r = client.post("/api/v1/system/backups", headers=owner_headers)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert client.post("/api/v1/system/backups", headers=owner_headers).status_code == 422  # one at a time
    assert backups.run_pending() == "succeeded"
    runs = client.get("/api/v1/system/backups", headers=owner_headers).json()["items"]
    assert runs[0]["status"] == "succeeded" and runs[0]["verified"] and runs[0]["trigger"] == "manual"

    dl = client.get(f"/api/v1/system/backups/{runs[0]['id']}/download", headers=owner_headers)
    assert dl.status_code == 200 and dl.content.startswith(b"ERPBK1\n")
    assert b"Restore Clinic" not in dl.content  # encrypted
    downloaded = tmp_path / "dl.erpbk"
    downloaded.write_bytes(dl.content)
    from app.cli import main

    assert main(["verify-backup", str(downloaded)]) == 0

    # Restore drill: into a fresh file, then compare every table and re-check the books.
    target = tmp_path / "restored.db"
    res = backups.restore(downloaded, f"sqlite:///{target}")
    assert res.revision and res.dialect == "sqlite"
    with pytest.raises(backups.BackupError, match="exists"):
        backups.restore(downloaded, f"sqlite:///{target}")
    restored = create_engine(f"sqlite:///{target}")
    live = _counts(engine)
    got = _counts(restored)
    for t in ("backup_runs", "audit_logs"):  # written after the dump (run row, download audit)
        live.pop(t), got.pop(t)
    assert got == live and got["documents"] >= 1 and got["journal_entries"] >= 1
    from sqlalchemy.orm import Session

    from app.modules.accounting.reports import verify_books
    from app.modules.inventory.engine import verify_balances

    with Session(restored) as rs:
        assert verify_books(rs, uuid.UUID(s.bid)) == []
        assert verify_balances(rs, uuid.UUID(s.bid)) == []
    restored.dispose()

    # A modified file is refused before anything is written.
    bad = bytearray(downloaded.read_bytes())
    bad[-100] ^= 0xFF
    downloaded.write_bytes(bytes(bad))
    with pytest.raises(backups.BackupError, match="corrupt"):
        backups.restore(downloaded, f"sqlite:///{tmp_path / 'other.db'}")
    assert not (tmp_path / "other.db").exists()
    assert main(["verify-backup", str(downloaded)]) == 2


def test_scheduled_backup_retention_and_failures(owner, db, settings, monkeypatch):
    now = datetime.now(UTC)
    backups.save_backup_settings(db, {"hour_utc": now.hour, "keep_daily": 2, "keep_weekly": 0,
                                      "keep_monthly": 0})
    db.commit()
    assert backups.run_pending(now) == "succeeded"
    first = db.scalars(select(BackupRun).where(BackupRun.trigger == "schedule")).all()
    assert len(first) == 1 and first[0].status == BackupStatus.SUCCEEDED
    db.commit()  # end the read transaction (SQLite) before the next backup writes
    assert backups.run_pending(now) is None  # already done for this slot

    # Older runs are pruned to keep_daily distinct days, files included.
    for days in (1, 2, 3):
        db.expire_all()
        run = backups.run_backup(trigger="cli")
        r = db.get(BackupRun, run.id)
        r.started_at = now - timedelta(days=days)
        db.commit()
    db.expire_all()
    backups.apply_retention(db)
    db.commit()
    alive = db.scalars(select(BackupRun).where(BackupRun.status == BackupStatus.SUCCEEDED,
                                               BackupRun.deleted_at.is_(None))).all()
    assert len(alive) == 2
    files = {p.name for p in Path(get_settings().backup_dir).glob("*.erpbk")}
    assert {r.filename for r in alive} <= files
    pruned = db.scalars(select(BackupRun).where(BackupRun.deleted_at.is_not(None))).all()
    assert pruned and all(r.filename not in files for r in pruned)
    db.commit()

    # Failures are recorded, not raised; no key → not scheduled.
    settings(min_free_disk_mb=10**9)
    failed = backups.run_backup(trigger="cli")
    assert failed.status == BackupStatus.FAILED and "disk" in failed.error
    settings(backup_key=None, min_free_disk_mb=1)
    assert backups.run_pending(now + timedelta(days=1)) is None


def test_postgresql_backup_restore_drill(api, owner_headers, engine):
    if engine.dialect.name != "postgresql" or not shutil.which("pg_dump"):
        pytest.skip("PostgreSQL restore drill")
    from sqlalchemy.engine import make_url

    s = _sample_books(api, owner_headers)
    path, _ = backups.create_backup(engine)
    url = make_url(engine.url.render_as_string(hide_password=False))
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    name = "erp_restore_drill"
    try:
        with admin.connect() as c:
            c.execute(text(f"DROP DATABASE IF EXISTS {name}"))
            c.execute(text(f"CREATE DATABASE {name}"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot create a scratch database: {exc}")
    target = url.set(database=name)
    try:
        backups.restore(path, target.render_as_string(hide_password=False))
        restored = create_engine(target)
        live, got = _counts(engine), _counts(restored)
        assert got == live and got["documents"] >= 1
        from sqlalchemy.orm import Session

        from app.modules.accounting.reports import verify_books

        with Session(restored) as rs:
            assert verify_books(rs, uuid.UUID(s.bid)) == []
        restored.dispose()
    finally:
        with admin.connect() as c:
            c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        admin.dispose()


def test_backup_settings_validation(api, owner_headers, client):
    r = client.put("/api/v1/system/backups/settings", headers=owner_headers, json={"hour_utc": 25})
    assert r.status_code == 422
    r = client.put("/api/v1/system/backups/settings", headers=owner_headers,
                   json={"offsite_integration_id": str(uuid.uuid4())})
    assert r.status_code == 422
    r = client.put("/api/v1/system/backups/settings", headers=owner_headers, json={"hour_utc": 2, "keep_monthly": 24})
    assert r.status_code == 200 and r.json()["hour_utc"] == 2
    got = client.get("/api/v1/system/backups/settings", headers=owner_headers).json()
    assert got["keep_monthly"] == 24 and got["key_configured"] is True


# ---------------------------------------------------------------------------- monitoring


def test_ready_metrics_and_system_health(api, owner_headers, client, db, settings):
    r = client.get("/api/v1/ready")
    assert r.status_code == 200 and r.json() == {"ready": True, "checks": {
        "database": True, "migrations": True, "disk": True}}
    settings(min_free_disk_mb=10**9)
    assert client.get("/api/v1/ready").status_code == 503
    settings(min_free_disk_mb=1)

    assert client.get("/api/v1/metrics").status_code == 404  # disabled without a token
    settings(metrics_token="scrape-me")
    assert client.get("/api/v1/metrics", headers={"Authorization": "Bearer nope"}).status_code == 403
    m = client.get("/api/v1/metrics", headers={"Authorization": "Bearer scrape-me"})
    assert m.status_code == 200 and m.headers["content-type"].startswith("text/plain; version=0.0.4")
    body = m.text
    assert 'erp_http_requests_total{method="GET",route="/api/v1/ready",status="2xx"}' in body
    assert 'erp_queue_pending{queue="messages_queued"} 0' in body
    assert "erp_worker_heartbeat_age_seconds -1" in body
    assert 'erp_ready{check="database"} 1' in body

    h = client.get("/api/v1/system/health", headers=owner_headers).json()
    assert h["status"] == "attention"
    assert any("worker is not running" in w for w in h["warnings"])
    assert any("No successful backup" in w for w in h["warnings"])
    from app.modules.system import monitor

    monitor.heartbeat(db, "worker", {"sent": 1})
    backups.run_backup(trigger="cli")
    h = client.get("/api/v1/system/health", headers=owner_headers).json()
    assert h["worker"]["alive"] and h["backups"]["last_status"] == "succeeded"
    assert h["status"] == "ok", h["warnings"]
    assert h["queues"]["messages_queued"] == {"pending": 0, "oldest_seconds": 0}


def test_worker_once_runs_every_job(owner, db, dns, monkeypatch):
    from app.cli import main

    assert main(["worker", "--once"]) == 0
    from app.modules.system import monitor

    db.expire_all()
    assert monitor.last_heartbeat(db) is not None
    assert db.scalar(select(func.count()).select_from(BackupRun)) >= 0
