"""End to end: Python API + worker (outbox → Redis Stream) + Node real-time service + WebSocket
clients, each as its own process, exactly as deployed.

Skipped when Node, the built realtime service or redis-server is unavailable.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
RT = ROOT / "services" / "realtime"
pytestmark = pytest.mark.skipif(
    not (shutil.which("node") and shutil.which("redis-server") and (RT / "node_modules").exists()),
    reason="needs node, redis-server and services/realtime dependencies")
OWNER = ("boss@example.com", "E2E-Strong-Pass-1")


def free_port() -> int:
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 30) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not come up")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    if not (RT / "dist" / "src" / "index.js").exists():
        subprocess.run(["npx", "tsc", "-p", "."], cwd=RT, check=True)
    redis_port, api_port, rt_port = free_port(), free_port(), free_port()
    env = {**os.environ, "ERP_ENV": "development", "ERP_DATABASE_URL": f"sqlite:///{tmp / 'e2e.db'}",
           "ERP_REDIS_URL": f"redis://127.0.0.1:{redis_port}/0", "ERP_SECRET_KEY": "e2e-" + "x" * 60}
    env.pop("ERP_TEST_DATABASE_URL", None)
    procs = [subprocess.Popen(["redis-server", "--port", str(redis_port), "--save", "", "--appendonly", "no"],
                              stdout=subprocess.DEVNULL)]
    try:
        subprocess.run([sys.executable, "-m", "app.cli", "migrate"], cwd=BACKEND, env=env, check=True,
                       capture_output=True)
        subprocess.run([sys.executable, "-m", "app.cli", "create-owner", "--email", OWNER[0], "--name", "Owner",
                        "--password", OWNER[1]], cwd=BACKEND, env=env, check=True, capture_output=True)
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.asgi:app", "--port", str(api_port)],
                                      cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_http(f"http://127.0.0.1:{api_port}/api/v1/health")
        procs.append(subprocess.Popen(["node", "dist/src/index.js"], cwd=RT, stdout=subprocess.DEVNULL, env={
            **os.environ, "RT_PORT": str(rt_port), "RT_HOST": "127.0.0.1", "ERP_API_URL": f"http://127.0.0.1:{api_port}",
            "ERP_REDIS_URL": env["ERP_REDIS_URL"], "RT_REVALIDATE_MS": "1000"}))
        wait_http(f"http://127.0.0.1:{rt_port}/health")

        def worker() -> str:
            return subprocess.run([sys.executable, "-m", "app.cli", "worker", "--once"], cwd=BACKEND, env=env,
                                  check=True, capture_output=True, text=True).stdout

        yield {"api": f"http://127.0.0.1:{api_port}/api/v1", "ws": f"ws://127.0.0.1:{rt_port}/ws",
               "health": f"http://127.0.0.1:{rt_port}/health", "worker": worker, "env": env}
    finally:
        for p in reversed(procs):
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


class WS:
    def __init__(self, url: str, token: str) -> None:
        from websockets.sync.client import connect

        self._conn = connect(url, open_timeout=10)
        self.ws = self._conn.__enter__()
        self.seen: list[dict] = []  # every message received, in order
        self.send({"type": "auth", "token": token})

    def send(self, msg: dict) -> None:
        self.ws.send(json.dumps(msg))

    def recv_until(self, pred, timeout: float = 10) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            msg = json.loads(self.ws.recv(timeout=max(0.1, end - time.time())))
            self.seen.append(msg)
            if pred(msg):
                return msg
        raise TimeoutError("expected message did not arrive")

    def drain(self, seconds: float = 0.5) -> list[dict]:
        out = []
        end = time.time() + seconds
        while time.time() < end:
            try:
                out.append(json.loads(self.ws.recv(timeout=0.1)))
                self.seen.append(out[-1])
            except TimeoutError:
                pass
        return out

    def close(self) -> None:
        self._conn.__exit__(None, None, None)


def login(api: str, email: str, password: str) -> str:
    r = httpx.post(f"{api}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_live_events_permissions_presence_resume_and_revocation(stack):
    api = stack["api"]
    owner_t = login(api, *OWNER)
    h = {"Authorization": f"Bearer {owner_t}"}
    biz = httpx.post(f"{api}/businesses", headers=h, json={"name": "Live Shop", "state_code": "27"}).json()
    base = f"{api}/businesses/{biz['id']}"
    units = {u["code"]: u["id"] for u in httpx.get(f"{base}/units", headers=h).json()}
    wh = httpx.get(f"{base}/warehouses", headers=h).json()[0]["id"]
    prod = httpx.post(f"{base}/products", headers=h, json={"name": "Tulsi", "sku": "T1", "unit_id": units["NOS"],
                                                           "selling_price": "100"}).json()
    httpx.post(f"{base}/inventory/opening-stock", headers=h, json={
        "warehouse_id": wh, "doc_date": time.strftime("%Y-%m-%d"),
        "lines": [{"product_id": prod["id"], "quantity": "50", "unit_cost": "40"}]})
    roles = {r["code"]: r["id"] for r in httpx.get(f"{base}/roles", headers=h).json()}
    invite = httpx.post(f"{base}/members", headers=h, json={"email": "op@example.com", "full_name": "Counter Operator",
                                                            "role_id": roles["billing_operator"]}).json()
    httpx.post(f"{api}/auth/accept-invite", json={"token": invite["invite_token"], "password": "Operator-Pass-77"})
    op_t = login(api, "op@example.com", "Operator-Pass-77")
    stack["worker"]()  # flush setup events

    owner_ws, op_ws = WS(stack["ws"], owner_t), WS(stack["ws"], op_t)
    for w in (owner_ws, op_ws):
        ready = w.recv_until(lambda m: m["type"] == "ready")
        assert [b["business_id"] for b in ready["businesses"]] == [biz["id"]]
        w.send({"type": "subscribe", "business_id": biz["id"]})
        w.recv_until(lambda m: m["type"] == "subscribed")
    pres = owner_ws.recv_until(lambda m: m["type"] == "presence" and len(m["users"]) == 2)
    assert {u["name"] for u in pres["users"]} == {"Owner", "Counter Operator"}
    op_ws.send({"type": "activity", "business_id": biz["id"], "viewing": "new bill"})
    owner_ws.recv_until(lambda m: m["type"] == "presence" and any("new bill" in u["viewing"] for u in m["users"]))
    assert not [m for m in op_ws.drain() if m["type"] == "presence"]  # operators lack users.view

    # A cash sale by the operator reaches both screens; the books entry only reaches the owner.
    oh = {"Authorization": f"Bearer {op_t}"}
    doc = httpx.post(f"{base}/documents", headers=oh, json={"doc_type": "sales_invoice",
                                                            "lines": [{"product_id": prod["id"], "quantity": "2"}]}).json()
    inv = httpx.post(f"{base}/documents/{doc['id']}/finalize", headers=oh,
                     json={"payments": [{"mode": "cash", "amount": doc["grand_total"]}]}).json()
    assert "events_streamed" in stack["worker"]()
    got_owner = owner_ws.recv_until(lambda m: m["type"] == "event" and m["event"]["type"] == "invoice.created")
    assert got_owner["event"]["data"]["number"] == inv["number"]
    op_ws.recv_until(lambda m: m["type"] == "event" and m["event"]["type"] == "invoice.created")
    owner_ws.drain(1)
    op_ws.drain(1)
    owner_types = {m["event"]["type"] for m in owner_ws.seen if m["type"] == "event"}
    op_types = {m["event"]["type"] for m in op_ws.seen if m["type"] == "event"}
    assert "journal.created" in owner_types and "journal.created" not in op_types
    assert "payment.created" in op_types  # counter staff record payments, so they see them
    assert not any(t.startswith(("journal.", "expense.")) for t in op_types)  # no accounting.view
    cursor = got_owner["stream_id"]

    # Reconnect and resume: events published while away are replayed, nothing is lost.
    owner_ws.close()
    doc2 = httpx.post(f"{base}/documents", headers=h, json={"doc_type": "sales_invoice",
                                                           "lines": [{"product_id": prod["id"]}]}).json()
    inv2 = httpx.post(f"{base}/documents/{doc2['id']}/finalize", headers=h,
                      json={"payments": [{"mode": "upi", "amount": doc2["grand_total"]}]}).json()
    stack["worker"]()
    back = WS(stack["ws"], owner_t)
    back.recv_until(lambda m: m["type"] == "ready")
    back.send({"type": "subscribe", "business_id": biz["id"], "last_event_id": cursor})
    replayed = back.recv_until(lambda m: m["type"] == "event" and m["event"]["type"] == "invoice.created")
    assert replayed["event"]["data"]["number"] == inv2["number"]
    assert back.recv_until(lambda m: m["type"] == "subscribed")["replayed"] >= 2
    back.send({"type": "subscribe", "business_id": biz["id"], "last_event_id": "0-1"})
    assert back.recv_until(lambda m: m["type"] in ("resync_required", "subscribed"))["type"] in (
        "resync_required", "subscribed")

    # Logging out ends the live connection too (re-validated against the API).
    httpx.post(f"{api}/auth/logout", headers=oh)
    op_ws.recv_until(lambda m: m["type"] == "session_ended", timeout=10)

    bad = WS(stack["ws"], "not-a-token")
    assert bad.recv_until(lambda m: m["type"] == "session_ended")
    health = httpx.get(stack["health"]).json()
    assert health["redis"] is True and health["delivered"] > 0
    back.close()


def test_rate_limit_is_shared_through_redis(stack):
    import redis

    api = stack["api"]
    h = {"Authorization": f"Bearer {login(api, *OWNER)}"}
    biz = httpx.get(f"{api}/businesses", headers=h).json()[0]
    app = httpx.post(f"{api}/developer/apps", headers=h, json={"name": "Limited", "business_ids": [biz["id"]],
                                                              "scopes": ["customers:read"], "rate_limit_per_minute": 2}).json()
    key = httpx.post(f"{api}/developer/apps/{app['id']}/keys", headers=h, json={}).json()["key"]
    kh = {"Authorization": f"Bearer {key}"}
    codes = [httpx.get(f"{api}/businesses/{biz['id']}/customers", headers=kh).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = redis.Redis.from_url(stack["env"]["ERP_REDIS_URL"])
    assert r.zcard(f"erp:ratelimit:{app['id']}") == 2  # the window lives in Redis, shared by all API processes
