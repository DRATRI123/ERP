"use client";

import { Loader2Icon, ShoppingCartIcon, TrendingDownIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/switch";
import { Input, NativeSelect } from "@/components/ui/input";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { money, qty } from "@/lib/format";

interface Suggestion {
  product_id: string; sku: string; name: string; on_hand: string; on_order: string; daily_sales: string; days_of_stock: number | null;
  reorder_level: string | null; suggested_quantity: string; supplier_id: string | null; supplier_name: string | null; last_price: string | null;
}

export function ReorderPanel() {
  const router = useRouter();
  const { can, isOwner } = useBusiness();
  const [days, setDays] = useState(30);
  const [lead, setLead] = useState(7);
  const [cover, setCover] = useState(30);
  const [picked, setPicked] = useState<Record<string, string>>({}); // product id → quantity typed
  const [unpicked, setUnpicked] = useState<Set<string>>(new Set());
  const [suppliers, setSuppliers] = useState<Record<string, string>>({}); // for products never bought
  const supplierList = useBizQuery<{ items: { id: string; name: string }[] }>(["suppliers", "pick"], "/suppliers", { limit: 200 });
  const q = useBizQuery<Suggestion[]>(["reorder", days, lead, cover], "/inventory/reorder", { days, lead_days: lead, cover_days: cover });
  const canOrder = isOwner || can("purchases.create");
  const order = useBizMutation<{ items: { product_id: string; quantity: string; supplier_id?: string }[] }, { id: string; party_name: string }[]>("POST", "/inventory/reorder", {
    invalidate: [["reorder"]], success: (out) => `${out.length} draft purchase order${out.length === 1 ? "" : "s"} created`,
    onSuccess: (out) => router.push(out.length === 1 ? `/documents/${out[0]!.id}` : "/purchases"),
  });
  const rows = q.data ?? [];
  const supplierOf = (r: Suggestion) => r.supplier_id ?? suppliers[r.product_id] ?? null;
  const chosen = rows.filter((r) => !unpicked.has(r.product_id) && supplierOf(r));
  const total = chosen.reduce((s, r) => s + Number(picked[r.product_id] ?? r.suggested_quantity) * Number(r.last_price ?? 0), 0);

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-col gap-3 border-b p-4 lg:flex-row lg:items-center lg:justify-between">
        <div><p className="font-semibold">What to buy</p>
          <p className="text-sm text-muted-foreground">From the last {days} days of sales, enough for the supplier’s lead time plus the days you want covered.</p></div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-1.5">Sales of last <NativeSelect className="h-8 w-20" value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Sales window">
            {[14, 30, 60, 90].map((n) => <option key={n} value={n}>{n}</option>)}</NativeSelect> days</label>
          <label className="flex items-center gap-1.5">Lead <Input className="h-8 w-14" inputMode="numeric" value={lead} onChange={(e) => setLead(Math.max(0, Number(e.target.value.replace(/\D/g, "")) || 0))} aria-label="Lead days" /> d</label>
          <label className="flex items-center gap-1.5">Cover <Input className="h-8 w-14" inputMode="numeric" value={cover} onChange={(e) => setCover(Math.max(1, Number(e.target.value.replace(/\D/g, "")) || 1))} aria-label="Cover days" /> d</label>
        </div>
      </div>
      {q.isLoading ? <TableSkeleton rows={5} cols={6} /> : q.error ? <ErrorState error={q.error} retry={() => q.refetch()} /> : rows.length === 0 ? (
        <EmptyState icon={TrendingDownIcon} title="Nothing to reorder" description="Stock covers the expected sales. Products appear here as they run low or sell faster." />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-xs text-muted-foreground uppercase">
                <tr><th className="w-10 px-3 py-2" /><th className="px-3 py-2 text-left">Product</th><th className="px-3 py-2 text-right">On hand</th>
                  <th className="hidden px-3 py-2 text-right md:table-cell">Sells / day</th><th className="px-3 py-2 text-right">Lasts</th>
                  <th className="hidden px-3 py-2 text-left lg:table-cell">Supplier</th><th className="w-28 px-3 py-2 text-right">Order</th></tr>
              </thead>
              <tbody className="divide-y">
                {rows.map((r) => {
                  const on = !unpicked.has(r.product_id);
                  return (
                    <tr key={r.product_id} className={on ? "" : "opacity-60"}>
                      <td className="px-3 py-2"><Checkbox checked={on && !!supplierOf(r)} disabled={!supplierOf(r)} aria-label={`Order ${r.name}`}
                        onCheckedChange={(v) => setUnpicked((s) => { const n = new Set(s); if (v) n.delete(r.product_id); else n.add(r.product_id); return n; })} /></td>
                      <td className="px-3 py-2"><p className="font-medium">{r.name}</p><p className="text-xs text-muted-foreground">{r.sku}{Number(r.on_order) > 0 && ` · ${qty(r.on_order)} on order`}</p></td>
                      <td className="num px-3 py-2 text-right">{qty(r.on_hand)}</td>
                      <td className="num hidden px-3 py-2 text-right md:table-cell">{Number(r.daily_sales) ? Number(r.daily_sales).toFixed(1) : "—"}</td>
                      <td className="px-3 py-2 text-right">{r.days_of_stock === null ? <span className="text-muted-foreground">—</span>
                        : <Badge variant={r.days_of_stock <= lead ? "destructive" : r.days_of_stock <= lead + 7 ? "warning" : "secondary"}>{r.days_of_stock} days</Badge>}</td>
                      <td className="hidden px-3 py-2 lg:table-cell">{r.supplier_name ? <span>{r.supplier_name}<span className="text-xs text-muted-foreground"> · {money(r.last_price)}</span></span>
                        : <NativeSelect className="h-8 max-w-56 text-xs" aria-label={`Supplier for ${r.name}`} value={suppliers[r.product_id] ?? ""}
                            onChange={(e) => setSuppliers((s) => ({ ...s, [r.product_id]: e.target.value }))}>
                            <option value="">Choose supplier…</option>{supplierList.data?.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</NativeSelect>}</td>
                      <td className="px-3 py-2"><Input className="num h-8 text-right" inputMode="decimal" aria-label={`Quantity of ${r.name}`} disabled={!on || !supplierOf(r)}
                        value={picked[r.product_id] ?? String(Number(r.suggested_quantity))}
                        onChange={(e) => setPicked((p) => ({ ...p, [r.product_id]: e.target.value.replace(/[^\d.]/g, "") }))} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="flex flex-col gap-2 border-t p-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted-foreground">{chosen.length} product{chosen.length === 1 ? "" : "s"} · about {money(total)} at last prices · one draft purchase order per supplier</p>
            {canOrder && <Button disabled={!chosen.length || order.isPending} onClick={() => order.mutate({ items: chosen
              .map((r) => ({ product_id: r.product_id, quantity: picked[r.product_id] ?? r.suggested_quantity, supplier_id: supplierOf(r) ?? undefined }))
              .filter((i) => Number(i.quantity) > 0) })}>
              {order.isPending ? <Loader2Icon className="animate-spin" /> : <ShoppingCartIcon />} Create purchase orders</Button>}
          </div>
        </>
      )}
    </Card>
  );
}
