import {
  BadgePercentIcon, BanknoteIcon, BarChart3Icon, TargetIcon, WrenchIcon, FileUpIcon, PackageOpenIcon, ScanLineIcon, RouteIcon, PackageXIcon, StarIcon, BookOpenIcon, PercentIcon, FactoryIcon, BotIcon, BoxesIcon, Building2Icon, CodeXmlIcon, FileTextIcon, LandmarkIcon,
  LayoutDashboardIcon, PlugIcon, ReceiptIndianRupeeIcon, SettingsIcon, ShoppingCartIcon, TruckIcon, UsersIcon,
  IdCardIcon, LifeBuoyIcon, MessageCircleIcon, ReceiptIcon, ShieldCheckIcon, StethoscopeIcon, TagsIcon, UserCogIcon, WalletIcon, type LucideIcon,
} from "lucide-react";

import { isModuleOn, type ModuleKey } from "@/hooks/use-modules";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Shown when the member holds any of these permissions (owner sees everything). */
  any?: string[];
  ownerOnly?: boolean;
  shortcut?: string; // "g" + key
  group: "main" | "money" | "manage";
  /** Optional module (Settings → Modules); hidden while the business has it switched off. */
  module?: ModuleKey;
}

export const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboardIcon, group: "main", shortcut: "d" },
  { href: "/owner", label: "All businesses", icon: Building2Icon, ownerOnly: true, group: "main", shortcut: "o" },
  { href: "/sales", label: "Sales & Invoices", icon: ReceiptIndianRupeeIcon, any: ["invoices.view"], group: "main", shortcut: "s" },
  { href: "/purchases", label: "Purchases", icon: ShoppingCartIcon, any: ["purchases.view"], group: "main", shortcut: "p" },
  { href: "/inventory", label: "Products & Inventory", icon: BoxesIcon, any: ["products.view", "stock.view"], group: "main", shortcut: "i" },
  { href: "/stock-audit", label: "Stock audit", icon: ScanLineIcon, any: ["stock.view"], group: "main" },
  { href: "/sale-or-return", label: "Sale or return", icon: PackageOpenIcon, any: ["invoices.view"], group: "main" },
  { href: "/damage", label: "Damage & claims", icon: PackageXIcon, any: ["stock.view"], group: "main", module: "damage" },
  { href: "/manufacturing", label: "Manufacturing", icon: FactoryIcon, any: ["stock.view"], group: "main", module: "manufacturing" },
  { href: "/pricing", label: "Pricing & Schemes", icon: TagsIcon, any: ["products.view"], group: "main", module: "pricing" },
  { href: "/catalogue", label: "WhatsApp catalogue", icon: MessageCircleIcon, any: ["business.settings", "invoices.view"], group: "main", module: "catalogue" },
  { href: "/field", label: "Field sales", icon: RouteIcon, any: ["customers.view"], group: "main", module: "field" },
  { href: "/leads", label: "Leads & follow-ups", icon: TargetIcon, any: ["customers.view"], group: "main", module: "leads" },
  { href: "/dispatch", label: "Dispatch & delivery", icon: TruckIcon, any: ["invoices.view"], group: "main", module: "dispatch" },
  { href: "/service", label: "Service & AMC", icon: WrenchIcon, any: ["invoices.view"], group: "main", module: "service" },
  { href: "/approvals", label: "Discount approvals", icon: BadgePercentIcon, any: ["discounts.approve"], group: "main" },
  { href: "/customers", label: "Customers", icon: UsersIcon, any: ["customers.view"], group: "main", shortcut: "c" },
  { href: "/suppliers", label: "Suppliers", icon: TruckIcon, any: ["suppliers.view"], group: "main" },
  { href: "/loyalty", label: "Loyalty points", icon: StarIcon, any: ["customers.view"], group: "main", module: "loyalty" },
  { href: "/doctors", label: "Doctors", icon: StethoscopeIcon, any: ["invoices.view"], group: "main", module: "pharmacy" },
  { href: "/accounting", label: "Accounting", icon: BookOpenIcon, any: ["accounting.view", "expenses.view"], group: "money", shortcut: "a" },
  { href: "/payments", label: "Banking & Payments", icon: WalletIcon, any: ["payments.view"], group: "money" },
  { href: "/cheques", label: "Cheques (PDC)", icon: BanknoteIcon, any: ["payments.view"], group: "money", module: "cheques" },
  { href: "/tds", label: "TDS", icon: LandmarkIcon, any: ["payments.view"], group: "money", module: "tds" },
  { href: "/interest", label: "Interest on dues", icon: PercentIcon, any: ["invoices.view"], group: "money", module: "interest" },
  { href: "/claims", label: "Expense claims", icon: ReceiptIcon, any: ["expenses.claim", "expenses.manage"], group: "money", module: "claims" },
  { href: "/staff", label: "Staff & Salary", icon: IdCardIcon, any: ["payroll.manage", "attendance.mark"], group: "money", module: "payroll" },
  { href: "/gst", label: "GST & Tax", icon: LandmarkIcon, any: ["gst.view"], group: "money", shortcut: "t" },
  { href: "/reports", label: "Reports", icon: BarChart3Icon, any: ["reports.view", "accounting.view"], group: "money", shortcut: "r" },
  { href: "/documents", label: "Documents", icon: FileTextIcon, group: "manage" },
  { href: "/licences", label: "Licences", icon: ShieldCheckIcon, group: "manage", module: "licences" },
  { href: "/automation", label: "Automation", icon: BotIcon, group: "manage", module: "automation" },
  { href: "/import", label: "Import data", icon: FileUpIcon, any: ["products.manage", "customers.manage", "suppliers.manage"], group: "manage" },
  { href: "/integrations", label: "Integrations", icon: PlugIcon, any: ["integrations.manage"], group: "manage" },
  { href: "/developer", label: "Developer API", icon: CodeXmlIcon, any: ["developer.manage"], group: "manage", module: "developer" },
  { href: "/users", label: "Users & Permissions", icon: UserCogIcon, any: ["users.view"], group: "manage", shortcut: "u" },
  { href: "/businesses", label: "Businesses", icon: Building2Icon, ownerOnly: true, group: "manage" },
  { href: "/settings", label: "Settings", icon: SettingsIcon, group: "manage" },
  { href: "/help", label: "Help & guide", icon: LifeBuoyIcon, group: "manage", shortcut: "h" },
];

export const GROUP_LABEL: Record<NavItem["group"], string> = { main: "Operations", money: "Finance", manage: "Workspace" };

export function visibleNav(can: (code: string) => boolean, isOwner: boolean, modules?: Record<string, boolean>) {
  return NAV.filter((n) => (!n.module || isModuleOn(modules, n.module))
    && (n.ownerOnly ? isOwner : isOwner || !n.any || n.any.some(can)));
}
