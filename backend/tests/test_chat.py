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
