"use client";

import { ChevronDownIcon, PanelLeftCloseIcon, PanelLeftOpenIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { BusinessSwitcher } from "@/components/layout/business-switcher";
import { Logo } from "@/components/layout/logo";
import { GROUP_LABEL, visibleNav, type NavItem } from "@/components/layout/nav";
import { Tooltip } from "@/components/ui/switch";
import { useBusiness } from "@/hooks/use-business";
import { useModules } from "@/hooks/use-modules";
import { cn } from "@/lib/utils";
import { useWorkspace } from "@/stores/business";

function NavLink({ item, collapsed, onNavigate }: { item: NavItem; collapsed: boolean; onNavigate?: () => void }) {
  const path = usePathname();
  const active = path === item.href || path.startsWith(`${item.href}/`);
  const link = (
    <Link href={item.href} onClick={onNavigate} aria-current={active ? "page" : undefined}
      className={cn("group flex h-8 items-center gap-2.5 rounded-md px-2.5 text-[13px] font-medium text-sidebar-foreground/80 transition-colors hover:bg-accent hover:text-foreground",
        active && "bg-sidebar-active text-primary hover:bg-sidebar-active hover:text-primary", collapsed && "justify-center px-0")}>
      <item.icon className={cn("size-4 shrink-0", active ? "text-primary" : "text-muted-foreground group-hover:text-foreground")} />
      {!collapsed && <span className="truncate">{item.label}</span>}
    </Link>
  );
  return collapsed ? <Tooltip content={item.label}>{link}</Tooltip> : link;
}

export function SidebarContent({ collapsed = false, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const { can, isOwner } = useBusiness();
  const { enabled } = useModules();
  const items = visibleNav(can, isOwner, enabled);
  const groups = (["main", "money", "manage"] as const).map((g) => ({ g, items: items.filter((i) => i.group === g) }));
  const folded = useWorkspace((s) => s.foldedGroups);
  const toggleGroup = useWorkspace((s) => s.toggleGroup);
  const path = usePathname();
  return (
    <div className="flex h-full flex-col">
      <div className={cn("flex h-14 items-center px-4", collapsed && "justify-center px-2")}>
        <Link href="/dashboard" onClick={onNavigate}><Logo collapsed={collapsed} /></Link>
      </div>
      <div className={cn("px-3 pb-3", collapsed && "px-2")}><BusinessSwitcher collapsed={collapsed} /></div>
      <nav className={cn("no-scrollbar flex-1 space-y-3 overflow-y-auto px-3 pb-4", collapsed && "px-2")} aria-label="Main">
        {groups.map(({ g, items: list }) => {
          if (!list.length) return null;
          // A folded group still shows the page you are on.
          const isFolded = !collapsed && folded.includes(g);
          const shown = isFolded ? list.filter((i) => path === i.href || path.startsWith(`${i.href}/`)) : list;
          return (
            <div key={g} className="space-y-0.5">
              {!collapsed && (
                <button type="button" onClick={() => toggleGroup(g)} aria-expanded={!isFolded}
                  className="flex w-full items-center justify-between px-2.5 pb-1 text-[11px] font-semibold tracking-wider text-muted-foreground/80 uppercase hover:text-foreground">
                  {GROUP_LABEL[g]}<ChevronDownIcon className={cn("size-3.5 transition-transform", isFolded && "-rotate-90")} />
                </button>
              )}
              {shown.map((item) => <NavLink key={item.href} item={item} collapsed={collapsed} onNavigate={onNavigate} />)}
            </div>
          );
        })}
      </nav>
    </div>
  );
}

export function Sidebar() {
  const collapsed = useWorkspace((s) => s.sidebarCollapsed);
  const toggle = useWorkspace((s) => s.toggleSidebar);
  return (
    <aside className={cn("sticky top-0 hidden h-dvh shrink-0 border-r bg-sidebar transition-[width] duration-200 lg:flex lg:flex-col",
      collapsed ? "w-[68px]" : "w-60")}>
      <SidebarContent collapsed={collapsed} />
      <button type="button" onClick={toggle} className="flex h-10 items-center justify-center gap-2 border-t text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
        {collapsed ? <PanelLeftOpenIcon className="size-4" /> : <><PanelLeftCloseIcon className="size-4" /> Collapse <kbd className="ml-1 rounded border px-1 text-[10px]">[</kbd></>}
      </button>
    </aside>
  );
}
