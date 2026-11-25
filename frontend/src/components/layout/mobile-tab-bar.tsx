"use client";

import { BoxesIcon, LayoutDashboardIcon, MenuIcon, PlusIcon, ReceiptIndianRupeeIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useBusiness } from "@/hooks/use-business";
import { cn } from "@/lib/utils";

/** Phones: the four places used all day, a big "new bill" button, and the full menu. */
export function MobileTabBar({ onMore }: { onMore: () => void }) {
  const path = usePathname();
  const { can, isOwner } = useBusiness();
  const sell = isOwner || can("invoices.create");
  const tabs = [
    { href: "/dashboard", label: "Home", icon: LayoutDashboardIcon, show: true },
    { href: "/sales", label: "Sales", icon: ReceiptIndianRupeeIcon, show: isOwner || can("invoices.view") },
    { href: "/inventory", label: "Stock", icon: BoxesIcon, show: isOwner || can("products.view") || can("stock.view") },
  ].filter((t) => t.show);
  const active = (href: string) => path === href || (path.startsWith(`${href}/`) && !path.startsWith("/sales/new"));
  return (
    <nav aria-label="Quick" className="no-print fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
      <div className="mx-auto grid h-16 max-w-md grid-cols-5 items-center">
        {tabs.slice(0, 2).map((t) => <Tab key={t.href} {...t} active={active(t.href)} />)}
        {tabs.length < 2 && <span />}
        <div className="flex justify-center">
          {sell ? (
            <Link href="/sales/new" aria-label="New bill" className="relative z-10 -mt-6 flex size-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg ring-4 ring-background active:scale-95">
              <PlusIcon className="size-6" />
            </Link>
          ) : <span />}
        </div>
        {tabs[2] ? <Tab {...tabs[2]} active={active(tabs[2].href)} /> : <span />}
        <button type="button" onClick={onMore} className="flex flex-col items-center gap-0.5 text-[11px] text-muted-foreground">
          <MenuIcon className="size-5" />More
        </button>
      </div>
    </nav>
  );
}

function Tab({ href, label, icon: Icon, active }: { href: string; label: string; icon: React.ComponentType<{ className?: string }>; active: boolean }) {
  return (
    <Link href={href} aria-current={active ? "page" : undefined}
      className={cn("flex flex-col items-center gap-0.5 text-[11px]", active ? "font-semibold text-primary" : "text-muted-foreground")}>
      <Icon className="size-5" />{label}
    </Link>
  );
}
