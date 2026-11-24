"""Cross-cutting: error envelope, request ids, audit trail, health, CLI."""

from __future__ import annotations

from app.cli import main as cli_main


def test_error_envelope_and_request_id(api, owner_headers):
    r = api.c.get("/api/v1/businesses/not-a-uuid", headers={**owner_headers, "X-Request-ID": "abc-123"})
    assert r.status_code == 422
    assert r.headers["X-Request-ID"] == "abc-123"
    err = r.json()["error"]
    assert err["code"] == "validation_failed" and err["request_id"] == "abc-123"
    assert err["details"][0]["loc"][-1] == "business_id"

    r = api.c.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    # Malicious request ids are replaced, not echoed.
    r = api.c.get("/api/v1/health", headers={"X-Request-ID": "<script>"})
    assert r.headers["X-Request-ID"] != "<script>"


def test_security_headers(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"


def test_audit_trail_records_actor_request_and_is_scoped(api, owner, owner_headers):
    a = api.create_business(owner_headers, name="A")
    b = api.create_business(owner_headers, name="B")
    api.add_member({**owner_headers, "X-Request-ID": "req-42"}, a["id"], "m@example.com",
                   "accountant")
    logs = api.c.get(f"/api/v1/businesses/{a['id']}/audit-logs", headers=owner_headers).json()
    actions = [x["action"] for x in logs["items"]]
    assert "member.added" in actions and "business.created" in actions
    added = next(x for x in logs["items"] if x["action"] == "member.added")
    assert added["actor_user_id"] == str(owner.id) and added["request_id"] == "req-42"
    assert all(x["business_id"] == a["id"] for x in logs["items"])

    everything = api.c.get("/api/v1/audit-logs?limit=200", headers=owner_headers).json()
    assert {x["business_id"] for x in everything["items"]} >= {a["id"], b["id"], None}


def test_audit_never_contains_secrets(api, owner_headers):
    biz = api.create_business(owner_headers)
    out = api.add_member(owner_headers, biz["id"], "m@example.com", "accountant")
    dump = api.c.get("/api/v1/audit-logs?limit=200", headers=owner_headers).text
    assert out["invite_token"] not in dump
    assert "argon2" not in dump


def test_owner_only_endpoints(api, owner_headers):
    biz = api.create_business(owner_headers)
    h, _ = api.member_session(owner_headers, biz["id"], "admin@example.com", "business_admin")
    for method, path in [("get", "/api/v1/users"), ("get", "/api/v1/audit-logs"),
                         ("get", "/api/v1/system/db-integrity")]:
        assert getattr(api.c, method)(path, headers=h).status_code == 403, path
    r = api.c.get("/api/v1/system/db-integrity", headers=owner_headers)
    assert r.status_code == 200 and r.json()["ok"] is True


def test_cli_create_owner_once(engine, capsys):
    assert cli_main(["create-owner", "--email", "Boss@Example.com", "--name", "Boss",
                     "--password", "Strong-Owner-99"]) == 0
    assert "boss@example.com" in capsys.readouterr().out
    assert cli_main(["create-owner", "--email", "two@example.com", "--name", "Two",
                     "--password", "Strong-Owner-99"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert cli_main(["db-check"]) == 0


def test_cli_works_as_a_fresh_process(tmp_path):
    """Run the real CLI in a clean interpreter: catches models that only resolve because the
    test process happened to import everything."""
    import os
    import subprocess
    import sys

    from app.core.config import BACKEND_DIR

    env = {**os.environ, "ERP_DATABASE_URL": f"sqlite:///{tmp_path / 'cli.db'}"}

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "app.cli", *args], cwd=BACKEND_DIR,
                              env=env, capture_output=True, text=True, timeout=120)

    assert run("migrate").returncode == 0
    r = run("create-owner", "--email", "o@example.com", "--name", "O",
            "--password", "Fresh-Process-77")
    assert r.returncode == 0, r.stderr
    assert run("db-check").returncode == 0
    r = run("db-backup", "--dest", str(tmp_path / "bk"))
    assert r.returncode == 0 and list((tmp_path / "bk").glob("*.db"))
