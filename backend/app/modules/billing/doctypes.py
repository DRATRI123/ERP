"""The document types of the billing engine and what each one does."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class DocType(enum.StrEnum):
    QUOTATION = "quotation"
    ESTIMATE = "estimate"
    PROFORMA = "proforma"
    SALES_ORDER = "sales_order"
    DELIVERY_CHALLAN = "delivery_challan"
    SALES_INVOICE = "sales_invoice"
    CREDIT_NOTE = "credit_note"  # sales return / reduction in value
    DEBIT_NOTE = "debit_note"  # additional charge to a customer
    PURCHASE_ORDER = "purchase_order"
    PURCHASE_INVOICE = "purchase_invoice"
    PURCHASE_RETURN = "purchase_return"  # debit note issued to a supplier


class Side(enum.StrEnum):
    SALES = "sales"
    PURCHASE = "purchase"


@dataclass(frozen=True)
class DocTypeSpec:
    code: DocType
    label: str
    side: Side
    stock: int  # +1 stock in, -1 stock out, 0 none
    gst_return: bool  # reported in GST registers/returns
    payable: bool  # creates an amount due (tracked by payments)
    prefix: str
    converts_from: tuple[DocType, ...] = ()
    # The source's line quantities cap this document's (returns, invoice from challan).
    consumes_source: tuple[DocType, ...] = ()
    requires_reference: bool = False
    requires_party: bool = True
    # GST rule 46: tax-document numbers are at most 16 characters of A-Z, 0-9, "-" and "/".
    gst_number_rules: bool = False

    @property
    def view_permission(self) -> str:
        return "invoices.view" if self.side == Side.SALES else "purchases.view"

    @property
    def create_permission(self) -> str:
        return "invoices.create" if self.side == Side.SALES else "purchases.create"

    @property
    def edit_permission(self) -> str:
        return "invoices.edit" if self.side == Side.SALES else "purchases.edit"

    @property
    def cancel_permission(self) -> str:
        return "invoices.cancel" if self.side == Side.SALES else "purchases.approve"


_S, _P = Side.SALES, Side.PURCHASE
T = DocType

SPECS: dict[DocType, DocTypeSpec] = {s.code: s for s in (
    DocTypeSpec(T.QUOTATION, "Quotation", _S, 0, False, False, "QT", requires_party=False),
    DocTypeSpec(T.ESTIMATE, "Estimate", _S, 0, False, False, "EST", requires_party=False),
    DocTypeSpec(T.PROFORMA, "Proforma Invoice", _S, 0, False, False, "PI",
                converts_from=(T.QUOTATION, T.ESTIMATE, T.SALES_ORDER)),
    DocTypeSpec(T.SALES_ORDER, "Sales Order", _S, 0, False, False, "SO",
                converts_from=(T.QUOTATION, T.ESTIMATE)),
    DocTypeSpec(T.DELIVERY_CHALLAN, "Delivery Challan", _S, -1, False, False, "DC",
                converts_from=(T.SALES_ORDER,)),
    DocTypeSpec(T.SALES_INVOICE, "Tax Invoice", _S, -1, True, True, "INV",
                converts_from=(T.QUOTATION, T.ESTIMATE, T.PROFORMA, T.SALES_ORDER,
                               T.DELIVERY_CHALLAN),
                consumes_source=(T.DELIVERY_CHALLAN,), requires_party=False,
                gst_number_rules=True),
    DocTypeSpec(T.CREDIT_NOTE, "Credit Note", _S, +1, True, True, "CN",
                converts_from=(T.SALES_INVOICE,), consumes_source=(T.SALES_INVOICE,),
                requires_reference=True, gst_number_rules=True),
    DocTypeSpec(T.DEBIT_NOTE, "Debit Note", _S, 0, True, True, "DN",
                converts_from=(T.SALES_INVOICE,), requires_reference=True,
                gst_number_rules=True),
    DocTypeSpec(T.PURCHASE_ORDER, "Purchase Order", _P, 0, False, False, "PO"),
    DocTypeSpec(T.PURCHASE_INVOICE, "Purchase Invoice", _P, +1, True, True, "PUR",
                converts_from=(T.PURCHASE_ORDER,)),
    DocTypeSpec(T.PURCHASE_RETURN, "Purchase Return", _P, -1, True, True, "PRN",
                converts_from=(T.PURCHASE_INVOICE,), consumes_source=(T.PURCHASE_INVOICE,),
                requires_reference=True),
)}
