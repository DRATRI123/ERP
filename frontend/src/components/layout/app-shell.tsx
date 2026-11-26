"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { CommandPalette } from "@/components/layout/command-palette";
import { MobileTabBar } from "@/components/layout/mobile-tab-bar";
import { ModuleGate } from "@/components/layout/module-gate";
import { OfflineSync } from "@/components/layout/offline-sync";
import { Sidebar, SidebarContent } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { Dialog, DialogContent, DialogHeader, DialogTitle, Sheet } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useGlobalShortcuts } from "@/hooks/use-shortcuts";
import { useRealtime } from "@/hooks/use-realtime";
import { restoreSession } from "@/lib/session";
import { useBusiness } from "@/hooks/use-business";
import { useAuth } from "@/stores/auth";

const SHORTCUTS: [string, string][] = [
  ["Ctrl K  or  /", "Search and jump anywhere"], ["Alt N", "New invoice"], ["G then D", "Dashboard"],
  ["G then S", "Sales & invoices"], ["G then P", "Purchases"], ["G then I", "Products & inventory"],
  ["G then C", "Customers"], ["G then A", "Accounting"], ["G then T", "GST"], ["G then R", "Reports"],
  ["[", "Collapse / expand sidebar"], ["F2  (billing)", "Focus product search"], ["F4  (billing)", "Choose customer"],
  ["F9  (billing)", "Save & finalize"], ["Esc", "Close dialogs"],
];

/** Route protection + layout for every signed-in page. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const status = useAuth((s) => s.status);
  const router = useRouter();
  const path = usePathname();
  // Opened for a given path, so navigating elsewhere closes it without an effect.
  const [navOpenAt, setNavOpenAt] = useState<string | null>(null);
  const mobileNav = navOpenAt === path;
  const setMobileNav = (v: boolean) => setNavOpenAt(v ? path : null);
  const [search, setSearch] = useState(false);
  const [help, setHelp] = useState(false);
  const live = useRealtime();

  useEffect(() => {
    if (status === "unknown") void restoreSession();
    else if (status === "signed-out") router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [status, router, path]);
  // Field salespeople / MRs work in the phone portal, not the full ERP.
  const { business } = useBusiness();
  const fieldRep = status === "signed-in" && business?.role === "field_rep";
  useEffect(() => { if (fieldRep) router.replace("/sp"); }, [fieldRep, router]);

  const openSearch = useCallback(() => setSearch(true), []);
  const openHelp = useCallback(() => setHelp(true), []);
  useGlobalShortcuts({ openSearch, openHelp });

  if (status !== "signed-in" || fieldRep) {
    return (
      <div className="flex min-h-dvh">
        <div className="hidden w-64 border-r bg-sidebar p-4 lg:block"><Skeleton className="h-8 w-32" /><Skeleton className="mt-6 h-11" />
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="mt-3 h-7" />)}</div>
        <div className="flex-1 p-6"><Skeleton className="h-9 w-64" /><div className="mt-6 grid gap-4 sm:grid-cols-4">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-28" />)}</div></div>
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh">
      <Sidebar />
      <Sheet open={mobileNav} onOpenChange={setMobileNav}><SidebarContent onNavigate={() => setMobileNav(false)} /></Sheet>
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onMenu={() => setMobileNav(true)} onSearch={openSearch} onHelp={openHelp} live={live} />
        <main className="w-full min-w-0 flex-1 animate-in px-3 pt-4 pb-24 sm:px-6 sm:pt-6 lg:px-8 lg:pb-8 2xl:px-10"><ModuleGate>{children}</ModuleGate></main>
        <MobileTabBar onMore={() => setMobileNav(true)} />
        <OfflineSync />
      </div>
      <CommandPalette open={search} onOpenChange={setSearch} />
      <Dialog open={help} onOpenChange={setHelp}>
        <DialogContent>
          <DialogHeader><DialogTitle>Keyboard shortcuts</DialogTitle></DialogHeader>
          <div className="divide-y text-sm">
            {SHORTCUTS.map(([k, v]) => (
              <div key={k} className="flex items-center justify-between py-2"><span>{v}</span><kbd className="rounded border bg-muted px-2 py-0.5 text-xs">{k}</kbd></div>
            ))}
          </div>
          <Link href="/help" onClick={() => setHelp(false)} className="text-sm text-blue hover:underline">Open the Help & guide — step-by-step for every page →</Link>
        </DialogContent>
      </Dialog>
    </div>
  );
}
