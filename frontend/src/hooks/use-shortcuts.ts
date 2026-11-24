"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { NAV } from "@/components/layout/nav";
import { useWorkspace } from "@/stores/business";

function typing(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null;
  return !!t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
}

/**
 * Global shortcuts: Ctrl/⌘+K search · Alt+N new invoice · G then a letter to navigate
 * (G D dashboard, G S sales…) · [ toggles the sidebar · ? shows the list.
 */
export function useGlobalShortcuts({ openSearch, openHelp }: { openSearch: () => void; openHelp: () => void }) {
  const router = useRouter();
  const toggle = useWorkspace((s) => s.toggleSidebar);
  const pendingG = useRef<number | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearch(); return; }
      if (e.altKey && e.key.toLowerCase() === "n") { e.preventDefault(); router.push("/sales/new"); return; }
      if (typing(e) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") { e.preventDefault(); openSearch(); return; }
      if (e.key === "?") { e.preventDefault(); openHelp(); return; }
      if (e.key === "[") { toggle(); return; }
      if (pendingG.current) {
        const item = NAV.find((n) => n.shortcut === e.key.toLowerCase());
        window.clearTimeout(pendingG.current);
        pendingG.current = null;
        if (item) { e.preventDefault(); router.push(item.href); }
        return;
      }
      if (e.key.toLowerCase() === "g") pendingG.current = window.setTimeout(() => (pendingG.current = null), 900);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router, toggle, openSearch, openHelp]);
}
