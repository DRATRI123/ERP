"""Stock engine: valuation (weighted average + FIFO), documents, reversals, batches, serials,
counts, concurrency, branch scoping and cost visibility."""

from __future__ import annotations

import threading
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.events.models import OutboxEvent
from tests.conftest import TODAY, Shop

D = Decimal


# ---------------------------------------------------------------------------- valuation


def test_weighted_average_valuation(shop, db):
    p = shop.product("tulsi")["id"]
    shop.opening([{"product_id": p, "quantity": "10", "unit_cost": "100"}])
    shop.adjust([{"product_id": p, "quantity": "10", "unit_cost": "120"}])
    assert shop.stock(p) == (D("20"), D("2200"))
    doc = shop.adjust([{"product_id": p, "quantity": "-5"}], reason="damage")
    out = doc["entries"][0]
    assert D(out["value"]) == D("-550") and D(out["unit_cost"]) == D("110")
    assert shop.stock(p) == (D("15"), D("1650"))
    shop.assert_consistent(db)


def test_fifo_valuation_consumes_oldest_layers(fifo_shop, db):
    s = fifo_shop
    p = s.product("ashwagandha")["id"]
    s.opening([{"product_id": p, "quantity": "10", "unit_cost": "100"}])
    s.adjust([{"product_id": p, "quantity": "10", "unit_cost": "120"}])
    out = s.adjust([{"product_id": p, "quantity": "-15"}])["entries"][0]
    assert D(out["value"]) == D("-1600")  # 10 @ 100 + 5 @ 120
    assert s.stock(p) == (D("5"), D("600"))
    out = s.adjust([{"product_id": p, "quantity": "-5"}])["entries"][0]
    assert D(out["value"]) == D("-600")
    assert s.stock(p) == (D("0"), D("0"))
    s.assert_consistent(db)


@pytest.mark.parametrize("valuation", ["weighted_average", "fifo"])
def test_rounding_never_leaves_residue(api, owner_headers, db, valuation):
    s = Shop(api, owner_headers, valuation)
    p = s.product("triphala")["id"]
    s.opening([{"product_id": p, "quantity": "3", "unit_cost": "33.3333"}])  # 99.9999
    s.adjust([{"product_id": p, "quantity": "4", "unit_cost": "0.0001"}])
    for _ in range(7):
        s.adjust([{"product_id": p, "quantity": "-1"}])
    assert s.stock(p) == (D("0"), D("0"))
    s.assert_consistent(db)


def test_quantity_precision_follows_unit(shop):
    piece = shop.product("pcs-item")["id"]
    kg = shop.product("ghee", unit="KG")["id"]
    shop.post("/inventory/opening-stock", {"warehouse_id": shop.main, "doc_date": TODAY,
              "lines": [{"product_id": piece, "quantity": "1.5", "unit_cost": "1"}]}, expect=422)
    shop.opening([{"product_id": kg, "quantity": "1.234", "unit_cost": "500"}])
    assert shop.stock(kg) == (D("1.234"), D("617"))


def test_valuation_method_locked_after_stock(shop):
    r = shop.api.c.patch(shop.base, headers=shop.h, json={"inventory_valuation": "fifo"})
    assert r.status_code == 200  # no stock yet
    p = shop.product("x1")["id"]
    shop.opening([{"product_id": p, "quantity": "1", "unit_cost": "1"}])
    r = shop.api.c.patch(shop.base, headers=shop.h, json={"inventory_valuation": "weighted_average"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------- atomicity & numbering


def test_insufficient_stock_rolls_back_whole_document(shop, db):
    a = shop.product("aa")["id"]
    b = shop.product("bb")["id"]
    shop.opening([{"product_id": a, "quantity": "5", "unit_cost": "10"},
                  {"product_id": b, "quantity": "1", "unit_cost": "10"}])
    r = shop.adjust([{"product_id": a, "quantity": "-2"}, {"product_id": b, "quantity": "-3"}],
                    expect=409)
    assert r["error"]["code"] == "insufficient_stock"
    assert r["error"]["details"]["available"] == "1.000"
    assert shop.stock(a) == (D("5"), D("50"))  # first line not applied
    doc = shop.adjust([{"product_id": a, "quantity": "-1"}])
    assert doc["number"].endswith("/0001")  # the failed document consumed no number
    shop.assert_consistent(db)


def test_numbering_and_financial_year_check(shop):
    p = shop.product("num")["id"]
    d1 = shop.opening([{"product_id": p, "quantity": "1", "unit_cost": "1"}])
    d2 = shop.opening([{"product_id": p, "quantity": "1", "unit_cost": "1"}])
    fy = shop.get("/financial-years")[0]["code"]
    assert (d1["number"], d2["number"]) == (f"OS/{fy}/0001", f"OS/{fy}/0002")
    r = shop.post("/inventory/opening-stock", {"warehouse_id": shop.main, "doc_date": "2001-01-01",
                  "lines": [{"product_id": p, "quantity": "1", "unit_cost": "1"}]}, expect=422)
    assert "financial year" in r["error"]["message"]


# ---------------------------------------------------------------------------- transfers & cancel


@pytest.mark.parametrize("valuation", ["weighted_average", "fifo"])
def test_transfer_moves_exact_value(api, owner_headers, db, valuation):
    s = Shop(api, owner_headers, valuation)
    shop2 = s.warehouse("GODOWN")
    p = s.product("brahmi")["id"]
    s.opening([{"product_id": p, "quantity": "3", "unit_cost": "10"}])
    s.adjust([{"product_id": p, "quantity": "3", "unit_cost": "20"}])
    doc = s.transfer(s.main, shop2, [{"product_id": p, "quantity": "4"}])
    moved = D("50") if valuation == "fifo" else D("60")  # FIFO 3@10 + 1@20; WAC 4@15
    assert s.stock(p, shop2) == (D("4"), moved)
    assert s.stock(p) == (D("2"), D("90") - moved)
    # FIFO keeps the original layers at the destination: the next issue there is at 10.
    if valuation == "fifo":
        out = s.adjust([{"product_id": p, "quantity": "-3"}], warehouse=shop2)["entries"][0]
        assert D(out["value"]) == D("-30")
    events = db.scalars(select(OutboxEvent.event_type).where(
        OutboxEvent.aggregate_id == doc["id"])).all()
    assert set(events) == {"stock.updated", "stock.transferred"}
    s.assert_consistent(db)


@pytest.mark.parametrize("valuation", ["weighted_average", "fifo"])
def test_cancel_restores_stock_and_value_exactly(api, owner_headers, db, valuation):
    s = Shop(api, owner_headers, valuation)
    gd = s.warehouse("GD")
    p = s.product("neem")["id"]
    s.opening([{"product_id": p, "quantity": "10", "unit_cost": "7"}])
    s.adjust([{"product_id": p, "quantity": "5", "unit_cost": "13"}])
    before = s.stock(p)
    out = s.adjust([{"product_id": p, "quantity": "-12"}])
    trf = s.transfer(s.main, gd, [{"product_id": p, "quantity": "2"}])
    s.cancel(trf["id"])
    s.cancel(out["id"])
    assert s.stock(p) == before and s.stock(p, gd) == (D("0"), D("0"))
    doc = s.get(f"/inventory/documents/{out['id']}")
    assert doc["status"] == "cancelled" and len(doc["entries"]) == 2
    assert doc["entries"][1]["reverses_entry_id"] == doc["entries"][0]["id"]
    s.cancel(out["id"], expect=409)  # only once
    s.assert_consistent(db)


def test_cannot_cancel_receipt_already_issued_under_fifo(fifo_shop, db):
    s = fifo_shop
    p = s.product("amla")["id"]
    opening = s.opening([{"product_id": p, "quantity": "5", "unit_cost": "10"}])
    s.adjust([{"product_id": p, "quantity": "-1"}])
    r = s.cancel(opening["id"], expect=409)
    assert "already been issued" in r["error"]["message"]
    s.assert_consistent(db)


# ---------------------------------------------------------------------------- batches & expiry


def test_batches_and_expiry(shop, db):
    p = shop.product("syrup", batch_tracking=True)["id"]
    shop.post("/inventory/opening-stock", {"warehouse_id": shop.main, "doc_date": TODAY,
              "lines": [{"product_id": p, "quantity": "5", "unit_cost": "50"}]}, expect=422)
    shop.opening([
        {"product_id": p, "quantity": "5", "unit_cost": "50", "batch_no": "B-OLD",
         "expiry_date": "2026-12-01"},
        {"product_id": p, "quantity": "8", "unit_cost": "55", "batch_no": "B-NEW",
         "expiry_date": "2028-01-01"},
    ])
    shop.adjust([{"product_id": p, "quantity": "-2", "batch_no": "B-NEW"}], reason="sample")
    rows = {r["batch_no"]: D(r["quantity"]) for r in
            shop.get("/inventory/balances", product_id=p)["items"]}
    assert rows == {"B-OLD": D("5"), "B-NEW": D("6")}
    shop.adjust([{"product_id": p, "quantity": "-1", "batch_no": "NOPE"}], expect=422)
    shop.adjust([{"product_id": p, "quantity": "-6", "batch_no": "B-OLD"}], expect=409)

    soon = shop.get("/inventory/expiring", within_days=3650)
    assert [r["batch_no"] for r in soon] == ["B-OLD", "B-NEW"]
    assert len(shop.get(f"/products/{p}/batches")) == 2
    shop.assert_consistent(db)


# ---------------------------------------------------------------------------- serials


def test_serial_numbers_track_each_unit(shop, db):
    gd = shop.warehouse("GD")
    p = shop.product("pulse-oximeter", serial_tracking=True)["id"]
    shop.post("/inventory/opening-stock", {"warehouse_id": shop.main, "doc_date": TODAY,
              "lines": [{"product_id": p, "quantity": "2", "unit_cost": "900",
                         "serials": ["S1"]}]}, expect=422)
    shop.opening([{"product_id": p, "quantity": "3", "unit_cost": "900",
                   "serials": ["S1", "S2", "S3"]}])
    shop.post("/inventory/opening-stock", {"warehouse_id": shop.main, "doc_date": TODAY,
              "lines": [{"product_id": p, "quantity": "1", "unit_cost": "900",
                         "serials": ["S2"]}]}, expect=409)
    shop.transfer(shop.main, gd, [{"product_id": p, "quantity": "1", "serials": ["S3"]}])
    # S3 is no longer in MAIN.
    shop.adjust([{"product_id": p, "quantity": "-1", "serials": ["S3"]}], expect=422)
    out = shop.adjust([{"product_id": p, "quantity": "-1", "serials": ["S1"]}], reason="damage")

    serials = {x["serial_no"]: (x["status"], x["warehouse_id"])
               for x in shop.get("/inventory/serials", product_id=p)["items"]}
    assert serials == {"S1": ("out", None), "S2": ("in_stock", shop.main), "S3": ("in_stock", gd)}
    shop.cancel(out["id"])
    assert shop.get("/inventory/serials", q="S1")["items"][0]["status"] == "in_stock"
    shop.assert_consistent(db)


# ---------------------------------------------------------------------------- counts


def test_physical_count_posts_differences(shop, db):
    a = shop.product("count-a")["id"]
    b = shop.product("count-b")["id"]
    c = shop.product("count-c")["id"]
    shop.opening([{"product_id": a, "quantity": "10", "unit_cost": "4"},
                  {"product_id": b, "quantity": "10", "unit_cost": "6"},
                  {"product_id": c, "quantity": "2", "unit_cost": "1"}])
    doc = shop.post("/inventory/counts", {"warehouse_id": shop.main, "doc_date": TODAY, "lines": [
        {"product_id": a, "counted_quantity": "7"},
        {"product_id": b, "counted_quantity": "12"},
        {"product_id": c, "counted_quantity": "2"}]})
    lines = {ln["product_id"]: ln for ln in doc["lines"]}
    assert D(lines[a]["system_quantity"]) == D("10") and D(lines[a]["quantity"]) == D("-3")
    assert D(lines[b]["quantity"]) == D("2") and D(lines[b]["unit_cost"]) == D("6")
    assert len(doc["entries"]) == 2  # no entry for the matching line
    assert shop.stock(a) == (D("7"), D("28")) and shop.stock(b) == (D("12"), D("72"))

    serial = shop.product("serial-x", serial_tracking=True)["id"]
    shop.post("/inventory/counts", {"warehouse_id": shop.main, "doc_date": TODAY, "lines": [
        {"product_id": serial, "counted_quantity": "1"}]}, expect=422)
    shop.assert_consistent(db)


# ---------------------------------------------------------------------------- alerts


def test_low_stock_report_and_event(shop, db):
    p = shop.product("low", reorder_level="5", reorder_quantity="20")["id"]
    shop.product("no-level")
    shop.opening([{"product_id": p, "quantity": "8", "unit_cost": "1"}])
    assert shop.get("/inventory/low-stock") == []
    shop.adjust([{"product_id": p, "quantity": "-3"}])
    low = shop.get("/inventory/low-stock")
    assert [(x["sku"], x["on_hand"]) for x in low] == [("low", "5.000")]
    shop.adjust([{"product_id": p, "quantity": "-1"}])
    events = db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "stock.low")).all()
    assert len(events) == 1  # only when crossing the level, not on every further issue
    assert events[0].payload["on_hand"] == "5.000"


# ---------------------------------------------------------------------------- access


def test_branch_scoped_user_sees_only_their_warehouses(shop, db):
    api, h = shop.api, shop.h
    b1 = shop.post("/branches", {"code": "B1", "name": "Branch One"})["id"]
    w1 = shop.warehouse("W1", b1)
    p = shop.product("scoped")["id"]
    shop.opening([{"product_id": p, "quantity": "4", "unit_cost": "1"}])
    shop.opening([{"product_id": p, "quantity": "6", "unit_cost": "1"}], warehouse=w1)

    mh, _ = api.member_session(h, shop.bid, "mgr@example.com", "inventory_manager",
                               all_branches=False, branch_ids=[b1])
    assert [w["code"] for w in shop.get("/warehouses", headers=mh)] == ["W1"]
    rows = shop.get("/inventory/balances", headers=mh)["items"]
    assert [(r["warehouse_code"], r["quantity"]) for r in rows] == [("W1", "6.000")]
    assert shop.get(f"/products/{p}", headers=mh)["on_hand"] == "6.000"
    shop.post("/inventory/adjustments", {"warehouse_id": shop.main, "doc_date": TODAY,
              "reason": "damage", "lines": [{"product_id": p, "quantity": "-1"}]},
              expect=404, headers=mh)
    shop.post("/inventory/transfers", {"from_warehouse_id": w1, "to_warehouse_id": shop.main,
              "doc_date": TODAY, "lines": [{"product_id": p, "quantity": "1"}]},
              expect=404, headers=mh)
    shop.adjust([{"product_id": p, "quantity": "-1"}], warehouse=w1, headers=mh)


def test_cost_hidden_without_stock_value_permission(shop):
    api, h = shop.api, shop.h
    p = shop.product("secret-cost", purchase_price="40", selling_price="60")["id"]
    doc = shop.opening([{"product_id": p, "quantity": "2", "unit_cost": "40"}])
    oh, _ = api.member_session(h, shop.bid, "op@example.com", "billing_operator")

    prod = shop.get(f"/products/{p}", headers=oh)
    assert prod["purchase_price"] is None and prod["selling_price"] == "60.0000"
    assert prod["on_hand"] == "2.000"
    row = shop.get("/inventory/balances", headers=oh)["items"][0]
    assert row["value"] is None and row["average_cost"] is None and row["quantity"] == "2.000"
    entry = shop.get(f"/inventory/documents/{doc['id']}", headers=oh)["entries"][0]
    assert entry["value"] is None and entry["unit_cost"] is None
    assert api.c.get(f"{shop.base}/inventory/valuation", headers=oh).status_code == 403
    # Billing operators cannot post stock documents directly.
    shop.post("/inventory/adjustments", {"warehouse_id": shop.main, "doc_date": TODAY,
              "reason": "damage", "lines": [{"product_id": p, "quantity": "-1"}]},
              expect=403, headers=oh)


def test_valuation_report(shop):
    gd = shop.warehouse("GD")
    p = shop.product("val")["id"]
    shop.opening([{"product_id": p, "quantity": "2", "unit_cost": "10.5"}])
    shop.opening([{"product_id": p, "quantity": "1", "unit_cost": "3"}], warehouse=gd)
    v = shop.get("/inventory/valuation")
    assert v["method"] == "weighted_average" and D(v["total_value"]) == D("24")
    assert {w["code"]: D(w["value"]) for w in v["warehouses"]} == {"GD": D("3"), "MAIN": D("21")}


def test_stock_ledger_history(shop):
    p = shop.product("hist")["id"]
    shop.opening([{"product_id": p, "quantity": "5", "unit_cost": "2"}])
    shop.adjust([{"product_id": p, "quantity": "-2"}])
    items = shop.get("/inventory/ledger", product_id=p)["items"]
    assert [(i["quantity"], i["balance_quantity"]) for i in items] == [
        ("-2.000", "3.000"), ("5.000", "5.000")]
    docs = shop.get("/inventory/documents", kind="adjustment")["items"]
    assert len(docs) == 1 and docs[0]["kind"] == "adjustment"


# ---------------------------------------------------------------------------- concurrency


@pytest.mark.parametrize("valuation", ["weighted_average", "fifo"])
def test_concurrent_issues_never_oversell(api, owner_headers, owner, engine, db, valuation):
    """Many simultaneous issues against 10 units: exactly 10 succeed, the rest are refused."""
    from app.db.session import SessionLocal
    from app.modules.access.service import get_business_access
    from app.modules.inventory.engine import InsufficientStock
    from app.modules.inventory.models import AdjustmentReason
    from app.modules.inventory.service import LineIn, create_adjustment
    from app.modules.users.models import User

    s = Shop(api, owner_headers, valuation)
    p = s.product("hot-item")["id"]
    s.opening([{"product_id": p, "quantity": "10", "unit_cost": "3"}])
    results: list[str] = []
    lock = threading.Lock()

    def issue() -> None:
        session = SessionLocal()
        try:
            for _ in range(5):
                try:
                    user = session.get(User, owner.id)
                    access = get_business_access(session, user, uuid.UUID(s.bid))
                    create_adjustment(session, access, warehouse_id=uuid.UUID(s.main),
                                      doc_date=date.today(),
                                      reason=AdjustmentReason.OTHER,
                                      lines=[LineIn(product_id=uuid.UUID(p), quantity=D("-1"))],
                                      reference=None, notes=None)
                    session.commit()
                    outcome = "ok"
                except InsufficientStock:
                    session.rollback()
                    outcome = "refused"
                except Exception as exc:  # surfaced by the assertion below
                    session.rollback()
                    outcome = f"error: {type(exc).__name__}: {exc}"
                with lock:
                    results.append(outcome)
        finally:
            session.close()

    threads = [threading.Thread(target=issue) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r for r in results if r.startswith("error")] == []
    assert results.count("ok") == 10 and results.count("refused") == 10
    assert s.stock(p) == (D("0"), D("0"))
    numbers = [d["number"] for d in s.get("/inventory/documents", kind="adjustment", limit=50)["items"]]
    assert len(set(numbers)) == 10  # no duplicate document numbers under concurrency
    s.assert_consistent(db)
