"""Chat between a field salesperson and the owner."""

from __future__ import annotations

import io
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import Shop
from tests.test_billing import customer, doc, finalize, stocked


@pytest.fixture
def s(api, owner_headers) -> Shop:
    return Shop(api, owner_headers)


def test_chat_salesperson_and_owner(api, owner_headers, s):
    rep_h, _ = api.member_session(owner_headers, s.bid, "rep1@example.com", "field_rep")
    owner_id = api.c.get("/api/v1/auth/me", headers=owner_headers).json()["user"]["id"]
    rep_id = api.c.get("/api/v1/auth/me", headers=rep_h).json()["user"]["id"]
    base = f"{s.base}/chat"

    people = api.c.get(f"{base}/contacts", headers=rep_h).json()["contacts"]
    assert any(p["id"] == owner_id and p["role"] == "owner" for p in people)

    m = s.post("/chat/messages", {"to": owner_id, "body": "Gupta Medical wants 10 + 2 on Amla"}, headers=rep_h)
    assert m["mine"] is True
    box = api.c.get(f"{base}/contacts", headers=owner_headers).json()
    assert box["unread"] == 1 and next(c for c in box["contacts"] if c["id"] == rep_id)["unread"] == 1

    # An order shared in chat shows as a label; a photo is resized and served by signed link.
    p = stocked(s, "ch1", qty="20", selling_price="100")
    order = finalize(s, doc(s, "sales_order", [{"product_id": p, "quantity": "2"}], party_id=customer(s)["id"], headers=rep_h)["id"], headers=rep_h)
    shared = s.post("/chat/messages", {"to": owner_id, "document_id": order["id"]}, headers=rep_h)
    assert shared["document_label"].startswith("Sales Order SO/")
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1600, 900), (10, 120, 60)).save(buf, "JPEG")
    r = api.c.post(f"{base}/photos", headers=rep_h, data={"to": owner_id, "caption": "Shelf"}, files={"file": ("shelf.jpg", buf.getvalue())})
    assert r.status_code == 201, r.text
    assert api.c.get(r.json()["thumb"]).status_code == 200

    # The owner opens the thread: oldest first, and the rep's messages become read.
    msgs = api.c.get(f"{base}/with/{rep_id}", headers=owner_headers).json()
    assert msgs[0]["body"] == "Gupta Medical wants 10 + 2 on Amla" and len(msgs) == 3
    assert api.c.get(f"{base}/contacts", headers=owner_headers).json()["unread"] == 0

    # Waiting: a reply sent while the rep waits arrives at once.
    since = datetime.now(UTC).isoformat()
    def reply() -> None:
        time.sleep(1.5)
        api.c.post(f"{base}/messages", headers=owner_headers, json={"to": rep_id, "body": "OK, give it"})
    threading.Thread(target=reply).start()
    t0 = time.time()
    got = api.c.get(f"{base}/wait", headers=rep_h, params={"after": since}).json()
    assert [x["body"] for x in got["messages"]] == ["OK, give it"] and got["unread"] == 1 and time.time() - t0 < 6

    # Nobody outside the business; not to yourself; empty messages refused.
    assert api.c.post(f"{base}/messages", headers=rep_h, json={"to": rep_id, "body": "hi"}).status_code == 422
    assert api.c.post(f"{base}/messages", headers=rep_h, json={"to": owner_id}).status_code == 422
    other = Shop(api, owner_headers)
    assert api.c.get(f"{other.base}/chat/contacts", headers=rep_h).status_code in (403, 404)
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    assert api.c.get(f"{base}/wait", headers=owner_headers, params={"after": old}).status_code == 200


def test_chat_websocket_live(api, owner_headers, s):
    rep_h, _ = api.member_session(owner_headers, s.bid, "rep2@example.com", "field_rep")
    owner_tok = owner_headers["Authorization"].split()[1]
    rep_tok = rep_h["Authorization"].split()[1]
    owner_id = api.c.get("/api/v1/auth/me", headers=owner_headers).json()["user"]["id"]
    rep_id = api.c.get("/api/v1/auth/me", headers=rep_h).json()["user"]["id"]

    # Refused: bad token, other business, no auth frame.
    with api.c.websocket_connect("/api/v1/chat/ws") as ws:
        ws.send_json({"type": "auth", "token": "nope", "business_id": s.bid})
        assert ws.receive_json()["type"] == "error"
    other = Shop(api, owner_headers)
    with api.c.websocket_connect("/api/v1/chat/ws") as ws:
        ws.send_json({"type": "auth", "token": rep_tok, "business_id": other.bid})
        assert ws.receive_json() == {"type": "error", "message": "No access to this business"}

    with api.c.websocket_connect("/api/v1/chat/ws") as own, api.c.websocket_connect("/api/v1/chat/ws") as rep:
        own.send_json({"type": "auth", "token": owner_tok, "business_id": s.bid})
        assert own.receive_json()["type"] == "ready"
        assert own.receive_json()["online"] == [owner_id]  # presence after joining
        rep.send_json({"type": "auth", "token": rep_tok, "business_id": s.bid})
        ready = rep.receive_json()
        assert ready["type"] == "ready" and owner_id in ready["online"]
        assert set(rep.receive_json()["online"]) == {owner_id, rep_id}
        assert set(own.receive_json()["online"]) == {owner_id, rep_id}  # presence: the rep came online

        rep.send_json({"type": "typing", "to": owner_id})
        assert own.receive_json() == {"type": "typing", "from": rep_id}

        rep.send_json({"type": "send", "to": owner_id, "body": "Order booked for Gupta Medical", "ref": "r1"})
        frames = [rep.receive_json(), rep.receive_json()]
        assert {f["type"] for f in frames} == {"message", "sent"}
        got = own.receive_json()
        assert got["type"] == "message" and got["message"]["body"] == "Order booked for Gupta Medical" and got["unread"] == 1

        own.send_json({"type": "read", "peer": rep_id})
        assert rep.receive_json() == {"type": "read", "by": owner_id}

        # A message sent the normal way (HTTP) is pushed to the socket too.
        api.c.post(f"{s.base}/chat/messages", headers=owner_headers, json={"to": rep_id, "body": "Well done"})
        pushed = rep.receive_json()
        assert pushed["type"] == "message" and pushed["message"]["body"] == "Well done" and pushed["message"]["mine"] is False

        rep.send_json({"type": "send", "to": rep_id, "body": "me", "ref": "r2"})
        assert rep.receive_json() == {"type": "error", "message": "You cannot message yourself", "ref": "r2"}
        rep.send_json({"type": "ping"})
        assert rep.receive_json() == {"type": "pong"}
