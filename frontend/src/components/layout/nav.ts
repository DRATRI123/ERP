import {
  BarChart3Icon, BookOpenIcon, BotIcon, BoxesIcon, Building2Icon, CodeXmlIcon, FileTextIcon, LandmarkIcon,
  LayoutDashboardIcon, PlugIcon, ReceiptIndianRupeeIcon, SettingsIcon, ShoppingCartIcon, TruckIcon, UsersIcon,
  UserCogIcon, WalletIcon, type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Shown when the member holds any of these permissions (owner sees everything). */
  any?: string[];
  ownerOnly?: boolean;
  shortcut?: string; // "g" + key
  group: "main" | "money" | "manage";
}

export const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboardIcon, group: "main", shortcut: "d" },
  { href: "/sales", label: "Sales & Invoices", icon: ReceiptIndianRupeeIcon, any: ["invoices.view"], group: "main", shortcut: "s" },
  { href: "/purchases", label: "Purchases", icon: ShoppingCartIcon, any: ["purchases.view"], group: "main", shortcut: "p" },
  { href: "/inventory", label: "Products & Inventory", icon: BoxesIcon, any: ["products.view", "stock.view"], group: "main", shortcut: "i" },
  { href: "/customers", label: "Customers", icon: UsersIcon, any: ["customers.view"], group: "main", shortcut: "c" },
  { href: "/suppliers", label: "Suppliers", icon: TruckIcon, any: ["suppliers.view"], group: "main" },
  { href: "/accounting", label: "Accounting", icon: BookOpenIcon, any: ["accounting.view", "expenses.view"], group: "money", shortcut: "a" },
  { href: "/payments", label: "Banking & Payments", icon: WalletIcon, any: ["payments.view"], group: "money" },
  { href: "/gst", label: "GST & Tax", icon: LandmarkIcon, any: ["gst.view"], group: "money", shortcut: "t" },
  { href: "/reports", label: "Reports", icon: BarChart3Icon, any: ["reports.view", "accounting.view"], group: "money", shortcut: "r" },
  { href: "/documents", label: "Documents", icon: FileTextIcon, group: "manage" },
  { href: "/automation", label: "Automation", icon: BotIcon, group: "manage" },
  { href: "/integrations", label: "Integrations", icon: PlugIcon, any: ["integrations.manage"], group: "manage" },
  { href: "/developer", label: "Developer API", icon: CodeXmlIcon, any: ["developer.manage"], group: "manage" },
  { href: "/users", label: "Users & Permissions", icon: UserCogIcon, any: ["users.view"], group: "manage", shortcut: "u" },
  { href: "/businesses", label: "Businesses", icon: Building2Icon, ownerOnly: true, group: "manage" },
  { href: "/settings", label: "Settings", icon: SettingsIcon, group: "manage" },
];

export const GROUP_LABEL: Record<NavItem["group"], string> = { main: "Operations", money: "Finance", manage: "Workspace" };

export function visibleNav(can: (code: string) => boolean, isOwner: boolean) {
  return NAV.filter((n) => (n.ownerOnly ? isOwner : isOwner || !n.any || n.any.some(can)));
}
