"use client";

import { useQuery } from "@tanstack/react-query";
import { BarcodeIcon, Loader2Icon, SearchIcon } from "lucide-react";
import { forwardRef, useImperativeHandle, useRef, useState } from "react";
import { toast } from "sonner";

import { useBusiness } from "@/hooks/use-business";
import { useDebounced } from "@/hooks/use-debounce";
import { api, ApiError, type Page } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { isNetworkError, isOnline, lookupCached, searchCached } from "@/lib/offline";
import { money, qty } from "@/lib/format";
import type { Product } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Thumb, thumbOf } from "@/components/data/thumb";

export interface ProductSearchHandle { focus: () => void }

/**
 * Billing search box: type to search, ↑/↓ to choose, Enter to add. A scanned barcode (or an
 * exact SKU) followed by Enter is looked up directly and added at once.
 */
export const ProductSearch = forwardRef<ProductSearchHandle, { onPick: (p: Product) => void; placeholder?: string }>(
  function ProductSearch({ onPick, placeholder }, ref) {
    const { base, id } = useBusiness();
    const input = useRef<HTMLInputElement>(null);
    const t = useT();
    const [q, setQ] = useState("");
    const [open, setOpen] = useState(false);
    const [active, setActive] = useState(0);
    const [looking, setLooking] = useState(false);
    const term = useDebounced(q.trim(), 120);
    useImperativeHandle(ref, () => ({ focus: () => input.current?.focus() }));

    const { data, isFetching } = useQuery({
      queryKey: ["biz", id, "products", "billing-search", term], enabled: term.length >= 1,
      queryFn: async () => {
        if (!isOnline()) return { items: searchCached(id, term), total: 0, limit: 8, offset: 0 } as Page<Product>;
        try {
          return await api<Page<Product>>(`${base}/products`, { query: { q: term, limit: 8, with_stock: true } });
        } catch (e) {
          if (isNetworkError(e)) return { items: searchCached(id, term), total: 0, limit: 8, offset: 0 } as Page<Product>;
          throw e;
        }
      },
      staleTime: 10_000, networkMode: "always", // offline it searches the saved copy
    });
    const items = term ? (data?.items ?? []) : [];

    function pick(p: Product) {
      onPick(p);
      setQ("");
      setOpen(false);
      setActive(0);
      input.current?.focus();
    }

    async function onEnter() {
      const code = q.trim();
      if (!code) return;
      // Exact barcode / SKU first — what a scanner sends.
      setLooking(true);
      try {
        const cached = !isOnline() ? lookupCached(id, code) : undefined;
        if (cached) { pick(cached); return; }
        const p = await api<Product>(`${base}/products/lookup`, { query: { code } });
        pick(p);
      } catch (e) {
        const cached = isNetworkError(e) ? lookupCached(id, code) : undefined;
        if (cached) { pick(cached); return; }
        if (items[active]) pick(items[active]);
        else if (e instanceof ApiError && e.status === 404) toast.error(`No product with code “${code}”`);
      } finally {
        setLooking(false);
      }
    }

    return (
      <div className="relative">
        <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          ref={input} value={q} placeholder={placeholder ?? t("Scan barcode or search product (F2)")}
          className="h-11 w-full rounded-lg border border-input bg-card pr-10 pl-9 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25"
          onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(0); }}
          onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            else if (e.key === "Enter") { e.preventDefault(); void onEnter(); }
            else if (e.key === "Escape") { setOpen(false); }
          }}
          aria-label="Search products" autoComplete="off"
        />
        <span className="absolute top-1/2 right-3 -translate-y-1/2 text-muted-foreground">
          {looking || isFetching ? <Loader2Icon className="size-4 animate-spin" /> : <BarcodeIcon className="size-4" />}
        </span>
        {open && items.length > 0 && (
          <ul className="absolute z-40 mt-1 max-h-80 w-full overflow-y-auto rounded-lg border bg-popover p-1 shadow-lg" role="listbox">
            {items.map((p, i) => (
              <li key={p.id} role="option" aria-selected={i === active}>
                <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(p)} onMouseEnter={() => setActive(i)}
                  className={cn("flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-left text-sm", i === active && "bg-accent")}>
                  {thumbOf(p) && <Thumb src={thumbOf(p)} className="size-9" />}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{p.name}</span>
                    <span className="block truncate text-xs text-muted-foreground">{p.sku}{p.composition ? ` · ${p.composition as string}` : ""}{p.rack ? ` · Rack ${p.rack as string}` : ""}{p.barcode ? ` · ${p.barcode}` : ""}{p.hsn_sac ? ` · HSN ${p.hsn_sac}` : ""}</span>
                  </span>
                  {p.on_hand !== undefined && p.on_hand !== null && (
                    <span className={cn("num text-xs", Number(p.on_hand) <= 0 ? "text-destructive" : "text-muted-foreground")}>{qty(p.on_hand as string)} in stock</span>
                  )}
                  <span className="num w-24 text-right font-medium">{money(p.selling_price)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  },
);
