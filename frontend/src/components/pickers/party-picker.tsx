"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckIcon, ChevronsUpDownIcon, PlusIcon, UserIcon, XIcon } from "lucide-react";
import { forwardRef, useState } from "react";

import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useBusiness } from "@/hooks/use-business";
import { useDebounced } from "@/hooks/use-debounce";
import { api, type Page } from "@/lib/api";
import type { Party } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Searchable customer/supplier picker (server-side search). */
export const PartyPicker = forwardRef<HTMLButtonElement, {
  kind: "customers" | "suppliers"; value: Party | null; onChange: (p: Party | null) => void;
  placeholder?: string; onCreate?: (name: string) => void; className?: string;
}>(function PartyPicker({ kind, value, onChange, placeholder, onCreate, className }, ref) {
  const { base, id } = useBusiness();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const term = useDebounced(q, 200);
  const { data, isFetching } = useQuery({
    queryKey: ["biz", id, kind, "picker", term], enabled: open,
    queryFn: () => api<Page<Party>>(`${base}/${kind}`, { query: { q: term || undefined, limit: 20 } }),
  });
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button ref={ref} type="button" className={cn("flex h-9 w-full items-center gap-2 rounded-md border border-input bg-card px-3 text-left text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25", className)}>
          <UserIcon className="size-4 text-muted-foreground" />
          <span className={cn("flex-1 truncate", !value && "text-muted-foreground")}>{value?.name ?? placeholder ?? "Select…"}</span>
          {value ? (
            <span role="button" tabIndex={-1} aria-label="Clear" onClick={(e) => { e.stopPropagation(); onChange(null); }} className="rounded p-0.5 hover:bg-accent"><XIcon className="size-3.5" /></span>
          ) : <ChevronsUpDownIcon className="size-4 text-muted-foreground" />}
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] min-w-72 p-0">
        <Command shouldFilter={false}>
          <CommandInput value={q} onValueChange={setQ} placeholder={`Search ${kind}…`} />
          <CommandList>
            <CommandEmpty>{isFetching ? "Searching…" : "No match."}</CommandEmpty>
            <CommandGroup>
              {data?.items.map((p) => (
                <CommandItem key={p.id} value={p.id} onSelect={() => { onChange(p); setOpen(false); setQ(""); }}>
                  <span className="flex-1 truncate">{p.name}</span>
                  <span className="text-xs text-muted-foreground">{p.gstin ?? p.phone ?? ""}</span>
                  {value?.id === p.id && <CheckIcon className="text-primary!" />}
                </CommandItem>
              ))}
            </CommandGroup>
            {onCreate && q.trim().length > 1 && (
              <CommandGroup>
                <CommandItem value="__create" onSelect={() => { onCreate(q.trim()); setOpen(false); setQ(""); }}>
                  <PlusIcon /> Create “{q.trim()}”
                </CommandItem>
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
});
