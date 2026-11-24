"""WhatsApp catalogue: settings, public view, ordering (drafts only), limits, link rotation, isolation."""

from __future__ import annotations

from urllib.parse import unquote

import pytest

from app.core.ratelimit import limiter
from tests.conftest import USER_PASSWORD, Shop
from tests.test_billing import customer, stocked


@pytest.fixture
def sshop(api, owner_headers) -> Shop:
    limiter.reset() if hasattr(limiter, "reset") else None
    return Shop(api, owner_headers, name="Herbal Corner")


def pub(s: Shop, path: str = "", method: str = "GET", **kw):
    return s.api.c.request(method, f"/api/v1/public/catalogue/{path}", **kw)


def test_catalogue_flow(sshop):
    cat = sshop.post("/categories", {"name": "Churna"})
    hidden_cat = sshop.post("/categories", {"name": "Internal"})
    a = stocked(sshop, "sh1", selling_price="250", category_id=cat["id"])
    stocked(sshop, "sh2", selling_price="99", category_id=hidden_cat["id"])
    out = sshop.product("sh3", selling_price="180", category_id=cat["id"])["id"]  # never stocked
    c = sshop.api.c.put(f"{sshop.base}/catalogue", headers=sshop.h, json={
        "title": "Order <b>here</b>", "whatsapp_number": "+91 98765 43210", "category_ids": [cat["id"]]}).json()
    assert c["configured"] and c["whatsapp_number"] == "919876543210" and len(c["token"]) >= 40

    view = pub(sshop, c["token"]).json()
    names = {p["name"]: p for p in view["products"]}
    assert set(names) == {"Sh1", "Sh3"} and names["Sh3"]["in_stock"] is False and names["Sh1"]["in_stock"] is True
    assert "quantity" not in names["Sh1"] and "purchase_price" not in names["Sh1"]  # no stock figures or costs leak
    assert pub(sshop, "x" * 43).status_code == 404

    known = customer(sshop, "Regular Buyer", phone="9822012345")
    r = pub(sshop, f"{c['token']}/orders", "POST", json={"name": "Regular Buyer", "phone": "+91 98220 12345",
                                                         "address": "Shop 4, MG Road", "items": [{"product_id": a, "quantity": 3}]})
    assert r.status_code == 201, r.text
    placed = r.json()
    assert placed["reference"].startswith("WEB-") and "wa.me/919876543210" in placed["whatsapp_url"]
    assert "Sh1 × 3" in unquote(placed["whatsapp_url"])
    orders = sshop.get("/catalogue/orders")
    assert len(orders) == 1 and orders[0]["status"] == "draft" and orders[0]["phone"] == "9822012345"
    doc = sshop.get(f"/documents/{orders[0]['document_id']}")
    assert doc["doc_type"] == "sales_order" and doc["party_id"] == known["id"] and "MG Road" in doc["notes"]

    bad = [({"name": "X", "phone": "12345", "items": [{"product_id": a, "quantity": 1}]}, 422),  # phone
           ({"name": "Bot", "phone": "9876543210", "website": "spam.example", "items": [{"product_id": a, "quantity": 1}]}, 422),
           ({"name": "Y Z", "phone": "9876543210", "items": [{"product_id": out, "quantity": 1.5}]}, 422)]
    for body, code in bad:
        assert pub(sshop, f"{c['token']}/orders", "POST", json=body).status_code == code
    hidden = sshop.get("/products", q="sh2")["items"][0]["id"]
    assert pub(sshop, f"{c['token']}/orders", "POST", json={"name": "Y Z", "phone": "9876543210",
                                                           "items": [{"product_id": hidden, "quantity": 1}]}).status_code == 422

    new = sshop.post("/catalogue/rotate", {}, expect=200)
    assert new["token"] != c["token"] and pub(sshop, c["token"]).status_code == 404 and pub(sshop, new["token"]).status_code == 200
    sshop.api.c.put(f"{sshop.base}/catalogue", headers=sshop.h, json={"is_active": False})
    assert pub(sshop, new["token"]).status_code == 404


def test_order_rate_limit_and_minimum(sshop):
    p = stocked(sshop, "rl1", selling_price="100")
    c = sshop.api.c.put(f"{sshop.base}/catalogue", headers=sshop.h, json={"min_order_value": "500"}).json()
    small = pub(sshop, f"{c['token']}/orders", "POST", json={"name": "Asha", "phone": "9000000001", "items": [{"product_id": p, "quantity": 1}]})
    assert small.status_code == 422 and "minimum" in small.json()["error"]["message"]
    assert sshop.get("/catalogue/orders") == []  # the refused order left nothing behind
    codes = [pub(sshop, f"{c['token']}/orders", "POST", json={"name": "Asha", "phone": "9000000002",
                                                             "items": [{"product_id": p, "quantity": 6}]}).status_code for _ in range(7)]
    assert codes[:5] == [201] * 5 and codes[5] == 429  # 5 orders an hour per phone


def test_catalogue_settings_need_permission(api, sshop):
    role = sshop.post("/roles", {"name": "Counter", "permissions": ["business.view", "invoices.view"]})
    m = sshop.post("/members", {"email": "cat-view@example.com", "full_name": "C", "role_id": role["id"]})
    api.c.post("/api/v1/auth/accept-invite", json={"token": m["invite_token"], "password": USER_PASSWORD})
    h = api.auth("cat-view@example.com", USER_PASSWORD)
    assert api.c.put(f"{sshop.base}/catalogue", headers=h, json={"title": "x"}).status_code == 403
    assert api.c.get(f"{sshop.base}/catalogue/orders", headers=h).status_code == 200
