"""Products, masters, customers and suppliers."""

from __future__ import annotations

from sqlalchemy import select

from app.modules.events.models import OutboxEvent
from tests.conftest import TODAY, VALID_GSTIN

OTHER_GSTIN = "29AAGCB7383J1Z4"


# ---------------------------------------------------------------------------- setup defaults


def test_business_gets_default_units_and_warehouse(shop):
    assert {"NOS", "KG", "LTR", "BTL", "STRIP"} <= set(shop.units)
    whs = shop.get("/warehouses")
    assert [(w["code"], w["branch_id"]) for w in whs] == [("MAIN", shop.ho)]


# ---------------------------------------------------------------------------- products


def test_product_create_validate_and_lookup(shop, db):
    p = shop.product("SKU-1", barcode="8901234567890", hsn_sac="3004", gst_rate="12",
                     selling_price="99.50", mrp="120", prices_include_tax=True,
                     attributes={"potency": "500mg"})
    assert p["gst_rate"] == "12.0000" and p["attributes"] == {"potency": "500mg"}
    assert p["on_hand"] == "0"

    assert shop.get("/products/lookup", code="8901234567890")["id"] == p["id"]
    assert shop.get("/products/lookup", code="SKU-1")["id"] == p["id"]
    # SKU and barcode must not collide with each other either (scanner looks up both).
    for bad in ({"sku": "SKU-1"}, {"sku": "8901234567890"}, {"sku": "X", "barcode": "SKU-1"}):
        shop.post("/products", {"name": "Dup", "unit_id": shop.units["NOS"], "sku": "Y", **bad},
                  expect=409)
    for bad in ({"hsn_sac": "12"}, {"gst_rate": "101"}, {"selling_price": "-1"},
                {"selling_price": "200", "mrp": "100", "prices_include_tax": True},
                {"unit_id": "00000000-0000-0000-0000-000000000000"}):
        r = shop.api.c.post(f"{shop.base}/products", headers=shop.h,
                            json={"name": "Bad", "sku": "BAD", "unit_id": shop.units["NOS"], **bad})
        assert r.status_code in (404, 422), (bad, r.text)

    found = shop.get("/products", q="sku-")["items"]
    assert [x["sku"] for x in found] == ["SKU-1"]
    events = db.scalars(select(OutboxEvent.event_type).where(
        OutboxEvent.aggregate_id == p["id"])).all()
    assert events == ["product.created"]


def test_services_do_not_track_stock(shop):
    s = shop.product("CONSULT", unit="HRS", kind="service", hsn_sac="999312")
    assert s["track_inventory"] is False and s["on_hand"] is None
    shop.post("/products", {"name": "Bad SAC", "sku": "S2", "unit_id": shop.units["HRS"],
                            "kind": "service", "hsn_sac": "3004"}, expect=422)
    shop.post("/products", {"name": "Bad", "sku": "S3", "unit_id": shop.units["HRS"],
                            "kind": "service", "serial_tracking": True}, expect=422)
    shop.post("/inventory/opening-stock", {"warehouse_id": shop.main, "doc_date": TODAY,
              "lines": [{"product_id": s["id"], "quantity": "1", "unit_cost": "1"}]}, expect=422)


def test_tracking_fields_locked_after_stock_and_archive_rules(shop):
    p = shop.product("LOCK")["id"]
    url = f"{shop.base}/products/{p}"
    assert shop.api.c.patch(url, headers=shop.h, json={"unit_id": shop.units["BOX"]}).status_code == 200
    shop.opening([{"product_id": p, "quantity": "2", "unit_cost": "5"}])
    r = shop.api.c.patch(url, headers=shop.h, json={"batch_tracking": True})
    assert r.status_code == 422 and r.json()["error"]["details"]["fields"] == ["batch_tracking"]
    assert shop.api.c.patch(url, headers=shop.h, json={"selling_price": "9"}).status_code == 200

    shop.post(f"/products/{p}/archive", {}, expect=422)  # still has stock
    shop.adjust([{"product_id": p, "quantity": "-2"}])
    archived = shop.post(f"/products/{p}/archive", {}, expect=200)
    assert archived["is_active"] is False
    assert shop.get("/products")["items"] == []
    shop.adjust([{"product_id": p, "quantity": "1", "unit_cost": "1"}], expect=422)
    assert shop.post(f"/products/{p}/restore", {}, expect=200)["is_active"] is True


def test_categories_brands_units(shop):
    root = shop.post("/categories", {"name": "Medicines"})
    child = shop.post("/categories", {"name": "Churna", "parent_id": root["id"]})
    shop.post("/categories", {"name": "churna", "parent_id": root["id"]}, expect=409)
    shop.post("/categories", {"name": "Churna"})  # same name, different parent: fine
    r = shop.api.c.patch(f"{shop.base}/categories/{root['id']}", headers=shop.h,
                         json={"parent_id": child["id"]})
    assert r.status_code == 422  # cycle
    brand = shop.post("/brands", {"name": "Own Label"})
    shop.post("/brands", {"name": "own label"}, expect=409)
    shop.post("/units", {"code": "nos", "name": "Dup"}, expect=409)
    jar = shop.post("/units", {"code": "JAR", "name": "Jar", "uqc": "oth"})
    assert jar["uqc"] == "OTH"
    p = shop.product("CAT", category_id=child["id"], brand_id=brand["id"])
    assert [x["id"] for x in shop.get("/products", category_id=child["id"])["items"]] == [p["id"]]


def test_products_isolated_between_businesses(api, owner_headers, shop):
    from tests.conftest import Shop

    other = Shop(api, owner_headers)
    p = other.product("THEIRS")["id"]
    assert api.c.get(f"{shop.base}/products/{p}", headers=shop.h).status_code == 404
    shop.post("/inventory/opening-stock", {"warehouse_id": shop.main, "doc_date": TODAY,
              "lines": [{"product_id": p, "quantity": "1", "unit_cost": "1"}]}, expect=422)
    shop.post("/products", {"name": "x", "sku": "x", "unit_id": other.units["NOS"]}, expect=404)
    shop.post("/inventory/opening-stock", {"warehouse_id": other.main, "doc_date": TODAY,
              "lines": []}, expect=422)


# ---------------------------------------------------------------------------- parties


def test_customer_gstin_derives_state_pan_and_type(shop, db):
    c = shop.post("/customers", {"name": "Wellness Clinic", "gstin": VALID_GSTIN,
                                 "phone": "+91 98200 00000"})
    assert (c["state_code"], c["pan"], c["gst_type"]) == ("27", "AAPFU0939F", "regular")
    assert c["is_customer"] and not c["is_supplier"]
    shop.post("/customers", {"name": "Dup", "gstin": VALID_GSTIN}, expect=409)
    shop.post("/customers", {"name": "Bad state", "gstin": OTHER_GSTIN, "state_code": "27"},
              expect=422)
    shop.post("/customers", {"name": "Reg w/o GSTIN", "gst_type": "regular"}, expect=422)
    walk_in = shop.post("/customers", {"name": "Walk-in", "gst_type": "consumer"})
    assert walk_in["gstin"] is None

    assert [x["name"] for x in shop.get("/customers", q="98200")["items"]] == ["Wellness Clinic"]
    assert [x["name"] for x in shop.get("/customers", q=VALID_GSTIN)["items"]] == ["Wellness Clinic"]
    assert shop.get("/suppliers")["items"] == []
    assert db.scalars(select(OutboxEvent.event_type).where(
        OutboxEvent.aggregate_id == c["id"])).all() == ["customer.created"]


def test_party_can_be_both_but_needs_both_permissions(shop):
    api, h = shop.api, shop.h
    oh, _ = api.member_session(h, shop.bid, "op@example.com", "billing_operator")
    shop.post("/customers", {"name": "Both", "is_supplier": True}, expect=403, headers=oh)
    c = shop.post("/customers", {"name": "Cust only"}, headers=oh)
    r = api.c.patch(f"{shop.base}/customers/{c['id']}", headers=oh, json={"is_supplier": True})
    assert r.status_code == 403
    r = api.c.patch(f"{shop.base}/customers/{c['id']}", headers=h, json={"is_supplier": True})
    assert r.status_code == 200
    assert [x["id"] for x in shop.get("/suppliers")["items"]] == [c["id"]]
    assert api.c.get(f"{shop.base}/suppliers", headers=oh).status_code == 403
    r = api.c.patch(f"{shop.base}/suppliers/{c['id']}", headers=h, json={"is_supplier": False})
    assert r.status_code == 200
    r = api.c.patch(f"{shop.base}/customers/{c['id']}", headers=h, json={"is_customer": False})
    assert r.status_code == 422  # would have no role left


def test_party_addresses_and_defaults(shop):
    c = shop.post("/customers", {"name": "Addr", "addresses": [
        {"kind": "billing", "address_line1": "1 MG Road", "state_code": "27", "pincode": "411001"},
        {"kind": "shipping", "address_line1": "Warehouse A"},
        {"kind": "shipping", "address_line1": "Warehouse B", "is_default": True},
    ]})
    defaults = {(a["kind"], a["address_line1"]) for a in c["addresses"] if a["is_default"]}
    assert defaults == {("billing", "1 MG Road"), ("shipping", "Warehouse B")}
    url = f"/customers/{c['id']}/addresses"
    new = shop.post(url, {"kind": "shipping", "address_line1": "Warehouse C", "is_default": True})
    party = shop.get(f"/customers/{c['id']}")
    assert [a["address_line1"] for a in party["addresses"] if a["kind"] == "shipping" and
            a["is_default"]] == ["Warehouse C"]
    r = shop.api.c.delete(f"{shop.base}{url}/{new['id']}", headers=shop.h)
    assert r.status_code == 204
    party = shop.get(f"/customers/{c['id']}")
    assert sum(a["is_default"] for a in party["addresses"] if a["kind"] == "shipping") == 1


def test_party_groups_credit_and_archive(shop):
    g = shop.post("/customers/groups", {"name": "Wholesale"})
    shop.post("/customers/groups", {"name": "wholesale"}, expect=409)
    sg = shop.post("/suppliers/groups", {"name": "Herb farms"})
    shop.post("/customers", {"name": "Wrong group", "customer_group_id": sg["id"]}, expect=404)
    c = shop.post("/customers", {"name": "Dealer", "customer_group_id": g["id"],
                                 "credit_limit": "50000", "credit_days": 30})
    assert c["credit_limit"] == "50000.0000"
    assert [x["id"] for x in shop.get("/customers", group_id=g["id"])["items"]] == [c["id"]]
    shop.post(f"/customers/{c['id']}/archive", {}, expect=200)
    assert shop.get("/customers")["items"] == []
    assert len(shop.get("/customers", include_archived=True)["items"]) == 1
    s = shop.post("/suppliers", {"name": "Farm", "gstin": OTHER_GSTIN, "supplier_group_id": sg["id"]})
    assert s["state_code"] == "29" and s["is_supplier"]
