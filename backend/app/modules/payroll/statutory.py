"""Statutory salary deductions: PF, ESI, professional tax (by state) and TDS on salary (new regime).

Rates and limits as of FY 2025-26; they change with notifications and each Budget, so check them
yearly. Bonus is left out of the PF wage; ESI and PT are on the month's gross.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
PF_RATE = Decimal("0.12")
EPS_RATE = Decimal("0.0833")
PF_WAGE_CEILING = Decimal(15000)
ESI_LIMIT = Decimal(21000)
ESI_EE, ESI_ER = Decimal("0.0075"), Decimal("0.0325")
STANDARD_DEDUCTION = Decimal(75000)
REBATE_LIMIT = Decimal(1200000)  # section 87A, new regime
SLABS = [(Decimal(400000), Decimal(0)), (Decimal(800000), Decimal("0.05")), (Decimal(1200000), Decimal("0.10")),
         (Decimal(1600000), Decimal("0.15")), (Decimal(2000000), Decimal("0.20")), (Decimal(2400000), Decimal("0.25")),
         (None, Decimal("0.30"))]


def rupee(v: Decimal) -> Decimal:
    return v.quantize(Decimal(1), rounding=ROUND_HALF_UP)


def pf(base: Decimal, basic_percent: Decimal, cap: bool) -> dict[str, Decimal]:
    """Employee 12% and employer 12% of the PF wage (basic + DA); of the employer's share 8.33% (on
    at most ₹15,000) goes to the pension scheme (EPS), the rest to EPF."""
    wage = (base * basic_percent / 100).quantize(Decimal("0.01"))
    if cap:
        wage = min(wage, PF_WAGE_CEILING)
    ee = rupee(wage * PF_RATE)
    eps = rupee(min(wage, PF_WAGE_CEILING) * EPS_RATE)
    return {"pf_wage": wage, "pf_employee": ee, "pf_employer": ee, "eps_employer": min(eps, ee)}


def esi(gross: Decimal, monthly_rate: Decimal) -> dict[str, Decimal]:
    """0.75% employee, 3.25% employer, when the monthly wage is up to ₹21,000 (rounded up)."""
    if monthly_rate > ESI_LIMIT or gross <= 0:
        return {"esi_employee": ZERO, "esi_employer": ZERO}
    return {"esi_employee": (gross * ESI_EE).quantize(Decimal(1), rounding=ROUND_CEILING),
            "esi_employer": (gross * ESI_ER).quantize(Decimal(1), rounding=ROUND_CEILING)}


def professional_tax(state: str | None, gross: Decimal, month: date) -> Decimal:
    """Monthly PT by state slab. States not listed: none (enter it as a deduction if it applies)."""
    g = gross
    if state == "24":  # Gujarat
        return Decimal(200) if g >= 12000 else ZERO
    if state == "27":  # Maharashtra (₹300 in February)
        if g <= 7500:
            return ZERO
        if g <= 10000:
            return Decimal(175)
        return Decimal(300) if month.month == 2 else Decimal(200)
    if state == "29":  # Karnataka
        return Decimal(200) if g >= 25000 else ZERO
    if state == "19":  # West Bengal
        for limit, tax in ((10000, 0), (15000, 110), (25000, 130), (40000, 150)):
            if g <= limit:
                return Decimal(tax)
        return Decimal(200)
    if state in ("36", "37"):  # Telangana, Andhra Pradesh
        return ZERO if g <= 15000 else Decimal(150) if g <= 20000 else Decimal(200)
    return ZERO


PT_STATES = {"24": "Gujarat", "27": "Maharashtra", "29": "Karnataka", "19": "West Bengal", "36": "Telangana", "37": "Andhra Pradesh"}


def annual_tax(income: Decimal) -> Decimal:
    """Tax on salary income for the year under the new regime, with the ₹75,000 standard deduction,
    the 87A rebate up to ₹12 lakh and 4% cess (no surcharge)."""
    taxable = max(ZERO, income - STANDARD_DEDUCTION)
    if taxable <= REBATE_LIMIT:
        return ZERO
    tax, lower = ZERO, ZERO
    for upper, rate in SLABS:
        top = taxable if upper is None else min(taxable, upper)
        if top > lower:
            tax += (top - lower) * rate
        if upper is None or taxable <= upper:
            break
        lower = upper
    return rupee(tax * Decimal("1.04"))


def monthly_tds(monthly_gross: Decimal) -> Decimal:
    """Simple projection: this month's salary for twelve months; one twelfth of the year's tax."""
    return rupee(annual_tax(monthly_gross * 12) / 12)
