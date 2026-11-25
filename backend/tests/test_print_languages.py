"""Printed labels in Hindi, Gujarati or bilingual."""

from __future__ import annotations

from app.modules.billing.print_labels import translator
from tests.conftest import Shop
from tests.test_billing import doc, finalize


def test_translator():
    assert translator("en")("Bill to") == "Bill to"
    assert translator("hi")("Bill to") == "बिल प्राप्तकर्ता"
    assert translator("gu")("Total") == "કુલ"
    assert translator("en_hi")("Tax Invoice") == "Tax Invoice / कर बीजक"
    assert translator("en_gu")("Something new") == "Something new"


def test_invoice_prints_in_chosen_language(api, owner_headers):
    s = Shop(api, owner_headers)
    inv = finalize(s, doc(s, "sales_invoice", [{"description": "Consulting", "quantity": "1", "unit_price": "100", "gst_rate": "0"}])["id"],
                   [{"mode": "cash", "amount": "100"}])
    for lang, expect, thermal in (("hi", "कर बीजक", False), ("en_gu", "Tax Invoice / ટેક્સ ઇન્વૉઇસ", False), ("gu", "કુલ", True)):
        cfg = {"label_language": lang, **({"paper": "thermal_80"} if thermal else {})}
        t = s.post("/invoice-templates", {"name": f"T {lang}", "config": cfg})
        html = api.c.get(f"{s.base}/documents/{inv['id']}/print", headers=s.h, params={"template_id": t["id"]})
        assert html.status_code == 200 and expect in html.text, (lang, html.text[:300])
    default = api.c.get(f"{s.base}/documents/{inv['id']}/print", headers=s.h).text
    assert "Bill to" in default and "कर" not in default
    bad = api.c.post(f"{s.base}/invoice-templates", headers=s.h, json={"name": "x", "config": {"label_language": "fr"}})
    assert bad.status_code == 422
