/**
 * Which events a user may receive. Deny by default: an event type not listed here is never
 * delivered, so a new event can't leak before someone decides who should see it.
 */

export interface BusinessAccess {
  business_id: string;
  business_name: string;
  role: string | null;
  permissions: string[];
  all_branches: boolean;
  branch_ids: string[];
}

export interface ErpEvent {
  id: string;
  type: string;
  business_id: string;
  created_at: string;
  data: Record<string, unknown>;
}

const BY_PREFIX: [string, string][] = [
  ["invoice.", "invoices.view"],
  ["purchase.", "purchases.view"],
  ["payment.", "payments.view"],
  ["stock.", "stock.view"],
  ["product.", "products.view"],
  ["customer.", "customers.view"],
  ["journal.", "accounting.view"],
  ["expense.", "expenses.view"],
  ["business.", "business.view"],
];

export function requiredPermission(event: ErpEvent): string | null {
  if (event.type.startsWith("document.")) {
    const docType = String(event.data["doc_type"] ?? "");
    return docType.startsWith("purchase") ? "purchases.view" : "invoices.view";
  }
  for (const [prefix, perm] of BY_PREFIX) {
    if (event.type.startsWith(prefix)) return perm;
  }
  return null; // unknown or internal (e.g. webhook.test): never broadcast
}

export function canSee(access: BusinessAccess, event: ErpEvent): boolean {
  if (access.business_id !== event.business_id) return false;
  const perm = requiredPermission(event);
  if (perm === null || !access.permissions.includes(perm)) return false;
  const branch = event.data["branch_id"];
  if (!access.all_branches && typeof branch === "string" && !access.branch_ids.includes(branch)) {
    return false;
  }
  return true;
}

export function canSeePresence(access: BusinessAccess): boolean {
  return access.permissions.includes("users.view");
}
