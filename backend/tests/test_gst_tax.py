"""GST engine and amount-in-words: pure unit tests (no database)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.gst.tax import (
    LineInput,
    TaxCategory,
    TaxError,
    compute_line,
    is_interstate,
    round_off,
)
from app.modules.gst.words import rupees_in_words

D = Decimal


def line(**kw):
    base = {"quantity": D("1"), "unit_price": D("100"), "gst_rate": D("18")}
    return LineInput(**(base | kw))


def test_exclusive_intrastate_splits_cgst_sgst():
    t = compute_line(line(quantity=D("2"), unit_price=D("250")), interstate=False, charge_tax=True)
    assert (t.taxable_value, t.cgst, t.sgst, t.igst, t.total) == (
        D("500.00"), D("45.00"), D("45.00"), D("0"), D("590.00"))


def test_exclusive_interstate_is_igst():
    t = compute_line(line(unit_price=D("999.99")), interstate=True, charge_tax=True)
    assert (t.taxable_value, t.igst, t.cgst, t.total) == (D("999.99"), D("180.00"), D("0"), D("1179.99"))


def test_inclusive_price_total_is_exactly_what_was_quoted():
    # ₹118 inclusive of 18%: taxable 100, CGST 9, SGST 9.
    t = compute_line(line(unit_price=D("118"), price_includes_tax=True), interstate=False, charge_tax=True)
    assert (t.taxable_value, t.cgst, t.sgst, t.total) == (D("100.00"), D("9.00"), D("9.00"), D("118.00"))
    # Awkward amounts: total still exact, CGST == SGST, taxable absorbs the paisa.
    for price in ("99.99", "10.01", "1.00", "12345.67"):
        t = compute_line(line(unit_price=D(price), quantity=D("3"), price_includes_tax=True,
                              gst_rate=D("5")), interstate=False, charge_tax=True)
        assert t.total == D(price) * 3
        assert t.cgst == t.sgst
        assert t.taxable_value + t.tax == t.total


def test_discounts_percent_and_flat():
    t = compute_line(line(quantity=D("4"), unit_price=D("50"), discount_percent=D("10"),
                          discount_amount=D("5")), interstate=False, charge_tax=True)
    assert (t.gross, t.discount, t.taxable_value) == (D("200.00"), D("25.00"), D("175.00"))
    assert t.total == D("206.50")
    with pytest.raises(TaxError):
        compute_line(line(discount_amount=D("101")), interstate=False, charge_tax=True)


def test_cess_on_same_taxable_value():
    t = compute_line(line(unit_price=D("1000"), gst_rate=D("28"), cess_rate=D("12")),
                     interstate=True, charge_tax=True)
    assert (t.igst, t.cess, t.total) == (D("280.00"), D("120.00"), D("1400.00"))


@pytest.mark.parametrize("category", [TaxCategory.EXEMPT, TaxCategory.NIL_RATED,
                                      TaxCategory.NON_GST, TaxCategory.ZERO_RATED])
def test_non_taxable_categories(category):
    t = compute_line(line(category=category, price_includes_tax=True), interstate=True, charge_tax=True)
    assert t.tax == 0 and t.taxable_value == t.total == D("100.00")


def test_no_tax_when_seller_cannot_charge_it():
    t = compute_line(line(unit_price=D("118"), price_includes_tax=True), interstate=False, charge_tax=False)
    assert t.tax == 0 and t.total == D("118.00")


def test_quantity_decimals_and_rounding():
    t = compute_line(line(quantity=D("0.333"), unit_price=D("33.3333"), gst_rate=D("12")),
                     interstate=False, charge_tax=True)
    assert t.gross == D("11.10") and t.cgst == D("0.67") and t.total == D("12.44")


def test_interstate_rules_and_round_off():
    assert is_interstate("27", "29") and not is_interstate("27", "27")
    assert is_interstate("27", "27", export_or_sez=True)
    assert not is_interstate("27", None)
    assert round_off(D("1179.49")) == D("-0.49") and round_off(D("1179.50")) == D("0.50")


@pytest.mark.parametrize(("amount", "words"), [
    ("0", "Rupees Zero Only"),
    ("1", "Rupees One Only"),
    ("115.50", "Rupees One Hundred Fifteen and Fifty Paise Only"),
    ("100000", "Rupees One Lakh Only"),
    ("12345678.09", "Rupees One Crore Twenty Three Lakh Forty Five Thousand Six Hundred Seventy Eight and Nine Paise Only"),
    ("-20", "Minus Rupees Twenty Only"),
])
def test_amount_in_words(amount, words):
    assert rupees_in_words(D(amount)) == words
