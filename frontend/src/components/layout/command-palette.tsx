"use client";

import { useQuery } from "@tanstack/react-query";
import { BoxIcon, FilePlus2Icon, MoonIcon, PackagePlusIcon, ReceiptTextIcon, SunIcon, UserPlusIcon, UserIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useState } from "react";

import { visibleNav } from "@/components/layout/nav";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandShortcut } from "@/components/ui/command";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useBusiness } from "@/hooks/use-business";
import { useDebounced } from "@/hooks/use-debounce";
import { api, type Page } from "@/lib/api";
import { money } from "@/lib/format";
import { DOC_LABEL, type DocumentSummary, type Party, type Product } from "@/lib/types";

/** Ctrl/⌘+K: jump anywhere, run an action, or find a product, customer or document. */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const router = useRouter();
  const { base, id, can, canAny, isOwner } = useBusiness();
  const { resolvedTheme, setTheme } = useTheme();
  const [q, setQ] = useState("");
  const term = useDebounced(q.trim(), 200);
  const enabled = open && !!id && term.length >= 2;

  const products = useQuery({
    queryKey: ["biz", id, "search-products", term], enabled: enabled && (isOwner || can("products.view")),
    queryFn: () => api<Page<Product>>(`${base}/products`, { query: { q: term, limit: 5 } }),
  });
  const customers = useQuery({
    queryKey: ["biz", id, "search-customers", term], enabled: enabled && (isOwner || can("customers.view")),
    queryFn: () => api<Page<Party>>(`${base}/customers`, { query: { q: term, limit: 5 } }),
  });
  const docs = useQuery({
    queryKey: ["biz", id, "search-docs", term], enabled: enabled && (isOwner || canAny("invoices.view", "purchases.view")),
    queryFn: () => api<Page<DocumentSummary>>(`${base}/documents`, { query: { q: term, limit: 5 } }),
  });

  const go = (href: string) => { onOpenChange(false); setQ(""); router.push(href); };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl overflow-hidden p-0">
        <DialogTitle className="sr-only">Search</DialogTitle>
        <Command shouldFilter={!term}>
          <CommandInput value={q} onValueChange={setQ} placeholder="Search products, customers, invoices or jump to a page…" />
          <CommandList>
            <CommandEmpty>{term ? "No matches." : "Type to search."}</CommandEmpty>
            {term && (products.data?.items.length ?? 0) > 0 && (
              <CommandGroup heading="Products">
                {products.data!.items.map((p) => (
                  <CommandItem key={p.id} value={`p-${p.id}`} onSelect={() => go(`/inventory/products/${p.id}`)}>
                    <BoxIcon /><span className="flex-1 truncate">{p.name}</span><span className="text-xs text-muted-foreground">{p.sku}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            {term && (customers.data?.items.length ?? 0) > 0 && (
              <CommandGroup heading="Customers">
                {customers.data!.items.map((c) => (
                  <CommandItem key={c.id} value={`c-${c.id}`} onSelect={() => go(`/customers/${c.id}`)}>
                    <UserIcon /><span className="flex-1 truncate">{c.name}</span><span className="text-xs text-muted-foreground">{c.phone}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            {term && (docs.data?.items.length ?? 0) > 0 && (
              <CommandGroup heading="Documents">
                {docs.data!.items.map((d) => (
                  <CommandItem key={d.id} value={`d-${d.id}`} onSelect={() => go(`/documents/${d.id}`)}>
                    <ReceiptTextIcon /><span className="flex-1 truncate">{d.number ?? "Draft"} · {d.party_name ?? "Walk-in"}</span>
                    <span className="num text-xs text-muted-foreground">{DOC_LABEL[d.doc_type]} · {money(d.grand_total)}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            {!term && (
              <>
                <CommandGroup heading="Actions">
                  {(isOwner || can("invoices.create")) && <CommandItem onSelect={() => go("/sales/new")}><FilePlus2Icon /> New invoice<CommandShortcut>Alt N</CommandShortcut></CommandItem>}
                  {(isOwner || can("purchases.create")) && <CommandItem onSelect={() => go("/purchases/new")}><FilePlus2Icon /> New purchase bill</CommandItem>}
                  {(isOwner || can("products.manage")) && <CommandItem onSelect={() => go("/inventory?new=1")}><PackagePlusIcon /> New product</CommandItem>}
                  {(isOwner || can("customers.manage")) && <CommandItem onSelect={() => go("/customers?new=1")}><UserPlusIcon /> New customer</CommandItem>}
                  <CommandItem onSelect={() => { setTheme(resolvedTheme === "dark" ? "light" : "dark"); onOpenChange(false); }}>
                    {resolvedTheme === "dark" ? <SunIcon /> : <MoonIcon />} Toggle dark mode
                  </CommandItem>
                </CommandGroup>
                <CommandGroup heading="Go to">
                  {visibleNav(can, isOwner).map((n) => (
                    <CommandItem key={n.href} onSelect={() => go(n.href)}>
                      <n.icon /> {n.label}{n.shortcut && <CommandShortcut>G {n.shortcut.toUpperCase()}</CommandShortcut>}
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
