"""Optional modules: each business switches on only what it uses.

The ERP serves very different businesses (a medical store, an Ayurveda manufacturer, a retail
shop, a distributor, a service business). The core (sales, purchases, stock, parties, payments,
accounting, GST, reports, users, settings) is always on; these modules can be switched off so
their menus and screens stay out of the way. Stored in ``business.settings["modules"]``.

Switching a module off hides it in the web app; its data is kept and comes back when it is
switched on again. The pharmacy module also changes billing rules (prescriptions for scheduled
drugs, near-expiry discount), so those rules apply only while it is on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Module:
    key: str
    label: str
    description: str
    default: bool


MODULES: tuple[Module, ...] = (
    Module("pharmacy", "Pharmacy / medical store",
           "Salt and substitutes, rack, drug schedules, PTR / PTS, strip + loose selling, doctors, prescriptions on "
           "Schedule H bills, the H1 register and the near-expiry discount.", False),
    Module("cheques", "Cheques (post-dated) & cheque printing",
           "Register of post-dated cheques received and issued, clearing and bounces, and printing on the bank's leaf.", True),
    Module("tds", "TDS", "Deduct TDS on payments, record TDS customers deduct, section-wise register and challan deposits.", True),
    Module("interest", "Interest on overdue bills",
           "Interest at a yearly % on late payments, day by day, drafted as debit notes.", False),
    Module("damage", "Damage & breakage claims",
           "Write off broken, leaking or damaged stock and claim it from the supplier until they credit it.", True),
    Module("loyalty", "Loyalty points", "Customers earn points on every bill and spend them as payment.", False),
    Module("field", "Field sales",
           "Beats / routes for salesmen, a phone page for orders, collections and visit notes, and monthly targets.", False),
    Module("manufacturing", "Manufacturing", "Recipes, production runs, batch costing and quality control.", True),
    Module("pricing", "Price lists & schemes", "Customer price lists, \"10 + 1\" and quantity-discount schemes.", True),
    Module("catalogue", "WhatsApp catalogue", "A link where buyers order from their phone.", True),
    Module("payroll", "Staff & salary", "Employees, attendance, salaries and commission.", True),
    Module("claims", "Expense claims & petty cash", "Staff submit bills with a photo; approvers pay from petty cash.", True),
    Module("licences", "Licences & compliance", "Drug, FSSAI, trade and other licences with renewal reminders.", True),
    Module("automation", "Automation", "Your own \"when this happens, do that\" rules.", True),
    Module("developer", "Developer API", "API keys and webhooks for other software.", True),
)
BY_KEY = {m.key: m for m in MODULES}


def enabled_modules(settings: dict[str, Any] | None) -> dict[str, bool]:
    stored = (settings or {}).get("modules") or {}
    return {m.key: bool(stored.get(m.key, m.default)) for m in MODULES}


def is_enabled(settings: dict[str, Any] | None, key: str) -> bool:
    return enabled_modules(settings)[key]
