"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Building2Icon, CheckIcon, ChevronsUpDownIcon, PlusIcon } from "lucide-react";
import Link from "next/link";

import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useBusiness } from "@/hooks/use-business";
import { initials, titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useWorkspace } from "@/stores/business";

export function BusinessSwitcher({ collapsed }: { collapsed?: boolean }) {
  const { businesses, business, isOwner } = useBusiness();
  const setBusiness = useWorkspace((s) => s.setBusiness);
  const qc = useQueryClient();
  if (!business) {
    return isOwner ? (
      <Link href="/businesses" className="flex items-center gap-2 rounded-lg border border-dashed px-3 py-2 text-sm text-muted-foreground hover:bg-accent">
        <PlusIcon className="size-4" />{!collapsed && "Create your first business"}
      </Link>
    ) : null;
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className={cn("flex w-full items-center gap-2.5 rounded-lg border bg-card p-1.5 text-left shadow-xs outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/40", collapsed && "justify-center border-transparent bg-transparent shadow-none")}
        aria-label="Switch business">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-blue-soft text-xs font-semibold text-blue">{initials(business.business_name)}</span>
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{business.business_name}</span>
              <span className="block truncate text-xs text-muted-foreground">{titleCase(business.role)}</span>
            </span>
            <ChevronsUpDownIcon className="size-4 text-muted-foreground" />
          </>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        <DropdownMenuLabel>Your businesses</DropdownMenuLabel>
        {businesses.map((b) => (
          <DropdownMenuItem key={b.business_id} onSelect={() => { setBusiness(b.business_id); qc.invalidateQueries({ queryKey: ["biz"] }); }}>
            <Building2Icon />
            <span className="flex-1 truncate">{b.business_name}</span>
            {b.business_id === business.business_id && <CheckIcon className="text-primary!" />}
          </DropdownMenuItem>
        ))}
        {isOwner && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild><Link href="/businesses"><PlusIcon /> Add or manage businesses</Link></DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
