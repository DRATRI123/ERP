"use client";

import { KeyboardIcon, LanguagesIcon, LogOutIcon, MenuIcon, MoonIcon, PlusIcon, SearchIcon, SettingsIcon, ShieldCheckIcon, SunIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";

import { OfflineBadge } from "@/components/layout/offline-sync";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/ui/switch";
import { useBusiness } from "@/hooks/use-business";
import type { LiveState } from "@/hooks/use-realtime";
import { initials } from "@/lib/format";
import { LANGS, useT } from "@/lib/i18n";
import { signOut } from "@/lib/session";
import { useCanChat, useChatState } from "@/features/chat/use-chat";
import { MessagesSquareIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/stores/auth";
import { useWorkspace } from "@/stores/business";

export function Topbar({ onMenu, onSearch, onHelp, live: dataLive }: { onMenu: () => void; onSearch: () => void; onHelp: () => void; live: LiveState }) {
  // Chat has its own WebSocket; when it is connected the app is live even without the optional
  // Redis-based live-updates service.
  const chatLive = useChatState((s) => s.transport) === "websocket";
  const live: LiveState = dataLive === "live" || chatLive ? "live" : dataLive;
  const me = useAuth((s) => s.me);
  const { can, isOwner } = useBusiness();
  const { resolvedTheme, setTheme } = useTheme();
  const router = useRouter();
  const t = useT();
  const lang = useWorkspace((s) => s.lang ?? "en");
  const setLang = useWorkspace((s) => s.setLang);
  return (
    <header className="no-print sticky top-0 z-30 flex h-14 items-center gap-1.5 border-b bg-background/85 px-3 backdrop-blur supports-[backdrop-filter]:bg-background/70 sm:px-5">
      <Button variant="ghost" size="icon" className="lg:hidden" onClick={onMenu} aria-label="Open menu"><MenuIcon /></Button>
      <button type="button" onClick={onSearch}
        aria-label="Search"
        className="flex h-9 w-full max-w-md min-w-0 items-center gap-2 rounded-md border bg-card px-3 text-sm text-muted-foreground shadow-xs transition-colors hover:bg-accent">
        <SearchIcon className="size-4 shrink-0" />
        <span className="flex-1 truncate text-left">{t("Search…")}<span className="hidden sm:inline"> {t("or jump to")}</span></span>
        <kbd className="hidden rounded border bg-muted px-1.5 text-[11px] font-medium sm:inline">Ctrl K</kbd>
      </button>
      <div className="ml-auto flex items-center gap-1">
        <OfflineBadge />
        <Tooltip side="bottom" content={live === "live" ? (dataLive === "live" ? "Live updates on" : "Connected — chat is live; other pages refresh as you open them") : live === "connecting" ? "Connecting to live updates…" : "Not connected live — pages refresh as you open them"}>
          <span className="hidden items-center gap-1.5 px-2 text-xs text-muted-foreground sm:flex">
            <span className={cn("size-2 rounded-full", live === "live" ? "bg-success shadow-[0_0_0_3px] shadow-success/20" : live === "connecting" ? "bg-warning" : "bg-muted-foreground/40")} />
            {live === "live" ? "Live" : live === "connecting" ? "…" : "Offline"}
          </span>
        </Tooltip>
        {(isOwner || can("invoices.create")) && (
          <Button size="sm" asChild className="hidden sm:inline-flex"><Link href="/sales/new"><PlusIcon /> {t("New invoice")}</Link></Button>
        )}
        <ChatButton />
        <Button variant="ghost" size="icon" onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")} aria-label="Toggle dark mode">
          {resolvedTheme === "dark" ? <SunIcon /> : <MoonIcon />}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger className="ml-1 flex size-9 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/50" aria-label="Account">
            {initials(me?.user.full_name ?? "?")}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-60">
            <DropdownMenuLabel className="font-normal">
              <p className="truncate text-sm font-medium text-foreground">{me?.user.full_name}</p>
              <p className="truncate text-xs">{me?.user.email}</p>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild><Link href="/settings/security"><ShieldCheckIcon /> {t("Security & 2FA")}{!me?.user.mfa_enabled && <span className="ml-auto text-xs text-warning">Off</span>}</Link></DropdownMenuItem>
            <DropdownMenuItem asChild><Link href="/settings"><SettingsIcon /> {t("Settings")}</Link></DropdownMenuItem>
            <DropdownMenuItem onSelect={onHelp}><KeyboardIcon /> {t("Keyboard shortcuts")}</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="flex items-center gap-1.5 text-xs font-normal"><LanguagesIcon className="size-3.5" /> {t("Language")}</DropdownMenuLabel>
            {LANGS.map(([code, name]) => (
              <DropdownMenuItem key={code} onSelect={() => setLang(code)} className={lang === code ? "font-semibold text-primary" : ""}>
                {lang === code ? "✓ " : ""}{name}</DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={async () => { await signOut(); router.replace("/login"); }}><LogOutIcon /> {t("Sign out")}</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}

function ChatButton() {
  const unread = useChatState((s) => s.unread);
  if (!useCanChat()) return null;
  return (
    <Button variant="ghost" size="icon" asChild className="relative" aria-label={unread ? `Messages, ${unread} unread` : "Messages"}>
      <Link href="/messages"><MessagesSquareIcon />{unread > 0 && <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-white">{unread > 99 ? "99+" : unread}</span>}</Link>
    </Button>
  );
}
