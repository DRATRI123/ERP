/** Shapes returned by the backend API (decimal amounts are strings). */

export type DocType =
  | "quotation" | "estimate" | "proforma" | "sales_order" | "delivery_challan" | "sales_invoice"
  | "credit_note" | "debit_note" | "purchase_order" | "purchase_invoice" | "purchase_return";

export const DOC_LABEL: Record<DocType, string> = {
  quotation: "Quotation", estimate: "Estimate", proforma: "Proforma invoice", sales_order: "Sales order",
  delivery_challan: "Delivery challan", sales_invoice: "Sales invoice", credit_note: "Credit note",
  debit_note: "Debit note", purchase_order: "Purchase order", purchase_invoice: "Purchase bill",
  purchase_return: "Purchase return",
};
export const SALES_TYPES: DocType[] = ["sales_invoice", "quotation", "estimate", "proforma", "sales_order", "delivery_challan", "credit_note", "debit_note"];
export const PURCHASE_TYPES: DocType[] = ["purchase_invoice", "purchase_order", "purchase_return"];

export interface DocumentSummary {
  id: string; doc_type: DocType; status: "draft" | "final" | "cancelled"; number: string | null; doc_date: string;
  due_date: string | null; branch_id: string; party_id: string | null; party_name: string | null;
  grand_total: string; balance_due: string; payment_status: "unpaid" | "partial" | "paid" | "not_applicable" | string;
  client_ref: string | null; created_at: string;
}

export interface DocumentLine {
  id: string; line_no: number; product_id: string | null; description: string; hsn_sac: string | null;
  quantity: string; unit_code?: string | null; unit_price: string; discount_percent: string; discount_amount: string;
  taxable_value: string; gst_rate: string; cgst_amount: string; sgst_amount: string; igst_amount: string;
  cess_amount: string; line_total: string; [k: string]: unknown;
}

export interface DocumentFull extends DocumentSummary {
  place_of_supply: string | null; seller_gstin: string | null; is_interstate: boolean; notes?: string | null;
  subtotal: string; discount_total?: string; taxable_total?: string; cgst_total?: string; sgst_total?: string;
  igst_total?: string; cess_total?: string; round_off?: string; amount_paid?: string; lines: DocumentLine[];
  [k: string]: unknown;
}

export interface Product {
  id: string; sku: string; name: string; kind: string; hsn_sac: string | null; barcode: string | null;
  unit_id: string; unit_code?: string; selling_price: string | null; purchase_price: string | null; mrp: string | null;
  gst_rate: string; prices_include_tax: boolean; track_inventory: boolean; batch_tracking: boolean;
  serial_tracking: boolean; is_active: boolean; reorder_level: string | null; category_id: string | null;
  [k: string]: unknown;
}

export interface Party {
  id: string; name: string; display_name?: string | null; gstin: string | null; phone: string | null;
  email: string | null; state_code: string | null; credit_limit: string | null; credit_days: number | null;
  is_active: boolean; [k: string]: unknown;
}

export interface Dashboard {
  business_id: string; business_name: string; today: string; month_start: string;
  sales?: { today: string; month: string; invoices_today: number; receivable: string; receivable_overdue: string; open_invoices: number };
  purchases?: { month: string; payable: string; payable_overdue: string; open_bills: number };
  payments_today?: { received: string; paid: string };
  profit?: { revenue: string; cost_of_goods_sold: string; expenses: string; gross_profit: string; net_profit: string; gross_margin_percent: string | null };
  stock?: { low_stock_count: number; expiring_30d: number; low_stock: { product_id: string; sku: string; name: string; on_hand: string; reorder_level: string }[] };
  daily?: { date: string; sales: string; purchases: string; invoices: number }[];
  recent?: { id: string; doc_type: DocType; number: string | null; doc_date: string; party_name: string | null; grand_total: string; status: string; payment_status: string }[];
  top_customers?: { name: string; amount: string }[];
}
