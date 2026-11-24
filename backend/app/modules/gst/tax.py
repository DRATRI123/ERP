"""GST calculation — pure functions, no database.

Rules implemented:

* Intra-state supply → CGST + SGST (or UTGST in Union Territories without a legislature);
  inter-state supply, exports and SEZ → IGST.
* Prices may be tax-exclusive (tax added on top) or tax-inclusive (tax extracted). For
  inclusive prices the line total always equals the entered amount exactly; the taxable value
  absorbs the paisa rounding, and CGST and SGST are always equal.
* Ad-valorem compensation cess on the same taxable value.
* Exempt, nil-rated, non-GST and zero-rated (LUT) lines carry no tax.
* No tax is charged when the seller cannot charge it (composition dealer / unregistered /
  bill of supply). Reverse charge is computed but not collected from the buyer — the document
  layer excludes it from the amount payable.
* All amounts are rounded to paise per line (ROUND_HALF_UP). Invoice round-off to the rupee is
  separate and optional.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

Q2 = Decimal("0.01")
ZERO = Decimal(0)
HUNDRED = Decimal(100)

# GST state codes of Union Territories without legislature: UTGST replaces SGST.
UTGST_STATES = frozenset({"04", "26", "31", "35", "38"})


def q2(d: Decimal) -> Decimal:
    return d.quantize(Q2, rounding=ROUND_HALF_UP)


class TaxCategory(enum.StrEnum):
    TAXABLE = "taxable"
    EXEMPT = "exempt"
    NIL_RATED = "nil_rated"
    NON_GST = "non_gst"
    ZERO_RATED = "zero_rated"  # export / SEZ supply under LUT, without payment of IGST


@dataclass(frozen=True)
class LineInput:
    quantity: Decimal
    unit_price: Decimal
    gst_rate: Decimal = ZERO
    cess_rate: Decimal = ZERO
    discount_percent: Decimal = ZERO
    discount_amount: Decimal = ZERO  # flat discount on the line, in addition to the percent
    price_includes_tax: bool = False
    category: TaxCategory = TaxCategory.TAXABLE


@dataclass(frozen=True)
class LineTax:
    gross: Decimal  # quantity × unit price, as entered
    discount: Decimal
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal  # SGST or UTGST — the document records which
    igst: Decimal
    cess: Decimal
    total: Decimal  # taxable value + all tax

    @property
    def tax(self) -> Decimal:
        return self.cgst + self.sgst + self.igst + self.cess


class TaxError(ValueError):
    pass


def compute_line(line: LineInput, *, interstate: bool, charge_tax: bool) -> LineTax:
    if line.quantity <= 0:
        raise TaxError("Quantity must be positive")
    if line.unit_price < 0:
        raise TaxError("Price cannot be negative")
    if not (ZERO <= line.discount_percent <= HUNDRED):
        raise TaxError("Discount percent must be between 0 and 100")
    if line.gst_rate < 0 or line.cess_rate < 0:
        raise TaxError("Tax rates cannot be negative")

    gross = q2(line.quantity * line.unit_price)
    discount = q2(gross * line.discount_percent / HUNDRED) + q2(line.discount_amount)
    if discount < 0 or discount > gross:
        raise TaxError("Discount cannot exceed the line amount")
    net = gross - discount

    taxed = charge_tax and line.category == TaxCategory.TAXABLE
    gst_rate = line.gst_rate if taxed else ZERO
    cess_rate = line.cess_rate if taxed else ZERO
    combined = gst_rate + cess_rate

    if combined == 0:
        return LineTax(gross, discount, net, ZERO, ZERO, ZERO, ZERO, net)

    base = q2(net * HUNDRED / (HUNDRED + combined)) if line.price_includes_tax else net
    if interstate:
        cgst = sgst = ZERO
        igst = q2(base * gst_rate / HUNDRED)
    else:
        cgst = sgst = q2(base * gst_rate / (2 * HUNDRED))
        igst = ZERO
    cess = q2(base * cess_rate / HUNDRED)
    tax = cgst + sgst + igst + cess
    if line.price_includes_tax:
        taxable = net - tax  # total stays exactly what the customer was quoted
        total = net
    else:
        taxable = net
        total = net + tax
    return LineTax(gross, discount, taxable, cgst, sgst, igst, cess, total)


def is_interstate(seller_state: str | None, place_of_supply: str | None, *,
                  export_or_sez: bool = False) -> bool:
    """Exports and supplies to SEZ are always inter-state. Without both states known the
    supply is treated as intra-state (the seller's own state)."""
    if export_or_sez:
        return True
    if not seller_state or not place_of_supply:
        return False
    return seller_state != place_of_supply


def round_off(total: Decimal) -> Decimal:
    """Adjustment that rounds a document total to the nearest rupee."""
    return total.quantize(Decimal(1), rounding=ROUND_HALF_UP) - total
