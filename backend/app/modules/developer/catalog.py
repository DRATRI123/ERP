"""API scopes and webhook event types.

A scope grants a set of ERP permission codes. An API key's effective permissions in a business
are its scopes' permissions ∩ the permissions its creator holds there *now* — so revoking a
user's access also narrows every key they created.
"""

from __future__ import annotations

SCOPES: dict[str, tuple[str, frozenset[str]]] = {
    "businesses:read": ("Read business profile, branches, warehouses", frozenset({"business.view"})),
    "customers:read": ("Read customers and their balances", frozenset({"customers.view", "customer_balances.view"})),
    "customers:write": ("Create and update customers", frozenset({"customers.manage"})),
    "suppliers:read": ("Read suppliers and their balances", frozenset({"suppliers.view", "supplier_balances.view"})),
    "suppliers:write": ("Create and update suppliers", frozenset({"suppliers.manage"})),
    "products:read": ("Read products, units, categories", frozenset({"products.view"})),
    "products:write": ("Create and update products", frozenset({"products.manage"})),
    "inventory:read": ("Read stock levels and movements", frozenset({"stock.view", "business.view"})),
    "inventory:write": ("Post stock adjustments and transfers", frozenset({"stock.edit"})),
    "invoices:read": ("Read sales documents", frozenset({"invoices.view"})),
    "invoices:create": ("Create, edit and finalize sales documents", frozenset({"invoices.create", "invoices.edit"})),
    "invoices:cancel": ("Cancel sales documents", frozenset({"invoices.cancel"})),
    "purchases:read": ("Read purchase documents", frozenset({"purchases.view"})),
    "purchases:write": ("Create and finalize purchase documents", frozenset({"purchases.create", "purchases.edit"})),
    "payments:read": ("Read receipts and payments", frozenset({"payments.view"})),
    "payments:create": ("Record receipts and payments", frozenset({"payments.create"})),
    "reports:read": ("Read reports, GST and accounting statements",
                     frozenset({"reports.view", "gst.view", "accounting.view", "profit.view"})),
}

EVENT_TYPES: dict[str, str] = {
    "business.created": "A business was created", "business.updated": "Business profile or status changed",
    "customer.created": "Customer created", "customer.updated": "Customer updated",
    "customer.deleted": "Customer archived",
    "product.created": "Product created", "product.updated": "Product updated", "product.deleted": "Product archived",
    "stock.updated": "Stock changed (document posted or cancelled)", "stock.low": "Stock fell to its reorder level",
    "stock.transferred": "Stock transferred between warehouses",
    "invoice.created": "Sales invoice finalized", "invoice.updated": "Sales invoice changed (e.g. IRN recorded)",
    "invoice.cancelled": "Sales invoice cancelled", "invoice.paid": "Sales invoice fully paid",
    "invoice.overdue": "Sales invoice passed its due date unpaid",
    "purchase.created": "Purchase invoice finalized", "purchase.updated": "Purchase invoice changed",
    "payment.created": "Receipt or payment recorded", "payment.success": "Payment succeeded",
    "payment.cancelled": "Receipt or payment cancelled",
    "document.finalized": "Any document finalized", "document.cancelled": "Any document cancelled",
    "journal.created": "Journal entry posted", "expense.created": "Expense recorded",
    "webhook.test": "Test event sent from the developer dashboard",
}


def permissions_for(scopes: list[str]) -> frozenset[str]:
    out: set[str] = set()
    for s in scopes:
        out |= SCOPES[s][1]
    return frozenset(out)


def matches(subscriptions: list[str], event_type: str) -> bool:
    """'*' = everything, 'invoice.*' = a family, otherwise an exact type."""
    return any(s == "*" or s == event_type or (s.endswith(".*") and event_type.startswith(s[:-1]))
               for s in subscriptions)
