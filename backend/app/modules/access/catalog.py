"""Permission catalog and built-in role templates.

Permission codes are the single vocabulary used by the backend to authorize every action.
They are defined in code (not editable at runtime) so that a code path can never reference a
permission that does not exist. Roles map to sets of these codes and are stored in the DB.

The Owner is not a role: the owner account implicitly holds every permission in every business.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Permission:
    code: str
    group: str
    description: str


_P = Permission

PERMISSIONS: tuple[Permission, ...] = (
    # Business & settings
    _P("business.view", "Business", "View business profile"),
    _P("business.settings", "Business", "Edit business profile and settings"),
    _P("licences.manage", "Business", "Add and renew licences (drug, FSSAI, AYUSH, trade…)"),
    _P("branches.manage", "Business", "Create and edit branches"),
    _P("financial_years.manage", "Business", "Create, lock and close financial years"),
    # Users & access
    _P("users.view", "Users", "View business members"),
    _P("users.manage", "Users", "Invite, edit and suspend business members"),
    _P("roles.manage", "Users", "Create and edit custom roles"),
    _P("audit.view", "Users", "View audit and activity logs"),
    # Sales / billing
    _P("invoices.view", "Sales", "View invoices"),
    _P("invoices.create", "Sales", "Create invoices"),
    _P("invoices.edit", "Sales", "Edit draft invoices"),
    _P("invoices.cancel", "Sales", "Cancel finalized invoices"),
    _P("templates.manage", "Sales", "Configure invoice templates and numbering series"),
    _P("credit_limit.override", "Sales", "Finalize sales beyond a customer's credit limit"),
    _P("discounts.approve", "Sales", "Give or approve discounts above the business limit"),
    _P("dispatch.update", "Sales", "Pack, dispatch and mark deliveries (with proof of delivery)"),
    # Purchases
    _P("purchases.view", "Purchases", "View purchases"),
    _P("purchases.create", "Purchases", "Create purchases"),
    _P("purchases.edit", "Purchases", "Edit draft purchases"),
    _P("purchases.approve", "Purchases", "Approve purchases"),
    # Inventory
    _P("products.view", "Inventory", "View products"),
    _P("products.manage", "Inventory", "Create and edit products"),
    _P("stock.view", "Inventory", "View stock levels"),
    _P("stock.edit", "Inventory", "Post opening stock, adjustments, transfers and counts"),
    _P("expenses.claim", "Staff", "Submit own expense claims with the bill"),
    _P("payroll.manage", "Staff", "Manage employees, salaries and see pay details"),
    _P("attendance.mark", "Staff", "Mark staff attendance"),
    _P("production.manage", "Inventory", "Manage recipes, record production and release batches after QC"),
    _P("stock_value.view", "Inventory", "View purchase prices, stock cost and valuation"),
    _P("warehouses.manage", "Inventory", "Create and edit warehouses"),
    # Parties
    _P("customers.view", "Parties", "View customers"),
    _P("customers.manage", "Parties", "Create and edit customers"),
    _P("customer_balances.view", "Parties", "View customer outstanding balances"),
    _P("suppliers.view", "Parties", "View suppliers"),
    _P("suppliers.manage", "Parties", "Create and edit suppliers"),
    _P("supplier_balances.view", "Parties", "View supplier outstanding balances"),
    # Finance
    _P("payments.view", "Finance", "View payments"),
    _P("payments.create", "Finance", "Record payments and receipts"),
    _P("expenses.view", "Finance", "View expenses"),
    _P("expenses.manage", "Finance", "Record expenses"),
    _P("accounting.view", "Finance", "View ledgers, journals and financial statements"),
    _P("accounting.post", "Finance", "Post manual journal entries"),
    _P("profit.view", "Finance", "View profit and margin figures"),
    _P("gst.view", "Finance", "View GST reports"),
    _P("gst.file", "Finance", "Prepare and submit GST returns"),
    # Data & platform
    _P("reports.view", "Reports", "View business reports"),
    _P("data.export", "Reports", "Export data (PDF/Excel/CSV)"),
    _P("integrations.manage", "Platform", "Configure integrations"),
    _P("developer.manage", "Platform", "Manage API keys and webhooks"),
    _P("files.view", "Files", "View and download files in the document library"),
    _P("files.manage", "Files", "Upload, rename, move and delete files"),
    _P("automation.manage", "Platform", "Create and change automation rules"),
)

PERMISSION_CODES: frozenset[str] = frozenset(p.code for p in PERMISSIONS)


@dataclass(frozen=True)
class RoleTemplate:
    code: str
    name: str
    description: str
    permissions: frozenset[str]


def _codes(*codes: str) -> frozenset[str]:
    unknown = set(codes) - PERMISSION_CODES
    if unknown:
        raise ValueError(f"Unknown permission codes: {sorted(unknown)}")
    return frozenset(codes)


_VIEW_ALL = {p.code for p in PERMISSIONS if p.code.endswith(".view")}

SYSTEM_ROLES: tuple[RoleTemplate, ...] = (
    RoleTemplate(
        "business_admin",
        "Business Administrator",
        "Full control of one business, except owner-only actions",
        PERMISSION_CODES,
    ),
    RoleTemplate(
        "branch_manager",
        "Branch Manager",
        "Runs day-to-day operations of assigned branches",
        _codes(
            "expenses.claim",
            *_VIEW_ALL - {"audit.view"},
            "invoices.create", "invoices.edit", "invoices.cancel",
            "purchases.create", "purchases.edit", "purchases.approve",
            "products.manage", "stock.edit", "customers.manage", "suppliers.manage",
            "warehouses.manage", "credit_limit.override", "discounts.approve", "dispatch.update",
            "payments.create", "expenses.manage", "data.export", "files.manage", "licences.manage",
            "attendance.mark",
        ),
    ),
    RoleTemplate(
        "accountant",
        "Accountant",
        "Books, ledgers, GST and financial reporting",
        _codes(
            "expenses.claim",
            *_VIEW_ALL - {"users.view"},
            "payments.create", "expenses.manage", "accounting.post", "gst.file",
            "financial_years.manage", "data.export", "files.manage",
        ),
    ),
    RoleTemplate(
        "billing_operator",
        "Billing Operator",
        "Counter billing: create invoices and collect payments",
        _codes(
            "expenses.claim",
            "business.view", "invoices.view", "invoices.create", "invoices.edit",
            "products.view", "stock.view", "customers.view", "customers.manage",
            "payments.view", "payments.create", "dispatch.update",
        ),
    ),
    RoleTemplate(
        "inventory_manager",
        "Inventory Manager",
        "Products, stock levels, transfers and adjustments",
        _codes(
            "expenses.claim",
            "business.view", "products.view", "products.manage", "stock.view", "stock.edit",
            "stock_value.view", "warehouses.manage", "purchases.view", "suppliers.view",
            "reports.view",
        ),
    ),
    RoleTemplate(
        "purchase_manager",
        "Purchase Manager",
        "Purchase orders, bills and supplier relationships",
        _codes(
            "expenses.claim",
            "business.view", "purchases.view", "purchases.create", "purchases.edit",
            "purchases.approve", "suppliers.view", "suppliers.manage",
            "supplier_balances.view", "products.view", "stock.view", "stock_value.view",
            "payments.view", "reports.view",
        ),
    ),
    RoleTemplate(
        "sales_executive",
        "Sales Executive",
        "Quotations, orders and customer relationships",
        _codes(
            "expenses.claim",
            "business.view", "invoices.view", "invoices.create", "customers.view",
            "customers.manage", "customer_balances.view", "products.view", "stock.view",
        ),
    ),
)

SYSTEM_ROLE_BY_CODE: dict[str, RoleTemplate] = {r.code: r for r in SYSTEM_ROLES}
