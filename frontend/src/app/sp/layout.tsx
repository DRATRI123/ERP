"use client";

import { HomeIcon, LogOutIcon, MessagesSquareIcon, ShoppingCartIcon, UsersIcon, WalletIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useChatState, useChatStream } from "@/features/chat/use-chat";
import { useBusiness } from "@/hooks/use-business";
import { restoreSession, signOut } from "@/lib/session";
import { cn } from "@/lib/utils";
import { useAuth } from "@/stores/auth";

const TABS = [
  { v: "home", label: "Today", icon: HomeIcon },
  { v: "customers", label: "Customers", icon: UsersIcon },
  { v: "order", label: "Order", icon: ShoppingCartIcon },
  { v: "collect", label: "Collect", icon: WalletIcon },
  { v: "chat", label: "Chat", icon: MessagesSquareIcon },
] as const;

/** Salesperson / MR portal: a phone-first page with only what a rep in the field needs. */
function Shell({ children }: { children: React.ReactNode }) {
  const status = useAuth((s) => s.status);
  const router = useRouter();
  const path = usePathname();
  const sp = useSearchParams();
  const tab = sp.get("tab") ?? "home";
  const { business, isOwner } = useBusiness();
  const unread = useChatState((s) => s.unread);
  useChatStream((peer) => router.push(`/sp?tab=chat&with=${peer}`));
  useEffect(() => {
    if (status === "unknown") void restoreSession();
    else if (status === "signed-out") router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [status, router, path]);
  if (status !== "signed-in") return <div className="mx-auto max-w-md space-y-3 p-4"><Skeleton className="h-14" /><Skeleton className="h-28" /><Skeleton className="h-40" /></div>;
  return (
    <div className="mx-auto flex min-h-dvh max-w-md flex-col bg-background">
      <header className="sticky top-0 z-20 flex items-center gap-2 bg-primary px-4 py-3 text-primary-foreground">
        <div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold">{business?.business_name ?? "Sales"}</p>
          <p className="text-[11px] opacity-80">Salesperson portal</p></div>
        {(isOwner || business?.role !== "field_rep") && <Link href="/dashboard" className="rounded-md bg-white/15 px-2 py-1 text-xs">Full ERP</Link>}
        <button type="button" aria-label="Sign out" className="rounded-md p-1.5 hover:bg-white/15" onClick={async () => { await signOut(); router.replace("/login?next=/sp"); }}><LogOutIcon className="size-4" /></button>
      </header>
      <main className="flex-1 px-3 pt-3 pb-24">{children}</main>
      <nav className="fixed inset-x-0 bottom-0 z-20 mx-auto grid max-w-md grid-cols-5 border-t bg-card pb-[env(safe-area-inset-bottom)]">
        {TABS.map((t) => (
          <Link key={t.v} href={`/sp?tab=${t.v}`} className={cn("flex flex-col items-center gap-0.5 py-2 text-[11px]", tab === t.v ? "text-primary" : "text-muted-foreground")}>
            <span className="relative"><t.icon className="size-5" />
              {t.v === "chat" && unread > 0 && <span className="absolute -top-1.5 -right-2.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-white">{unread}</span>}</span>{t.label}</Link>
        ))}
      </nav>
    </div>
  );
}

export default function SpLayout({ children }: { children: React.ReactNode }) {
  return <Suspense><Shell>{children}</Shell></Suspense>;
}
