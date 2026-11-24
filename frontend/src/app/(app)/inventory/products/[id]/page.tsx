"use client";

import { ArchiveIcon, ArrowLeftIcon, PencilIcon, PrinterIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { DataTable, type Column } from "@/components/data/data-table";
import { ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Barcode } from "@/features/inventory/barcode";
import { ProductDialog } from "@/features/inventory/product-form";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money, qty, titleCase } from "@/lib/format";
import type { Product } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Entry { id: string; entry_date: string; source_type: string; quantity: string; balance_quantity: string; unit_cost: string; value: string; warehouse_id: string; reverses_entry_id: string | null }

export default function ProductPage() {
  const { id } = useParams<{ id: string }>();
  const { can, isOwner } = useBusiness();
  const [edit, setEdit] = useState(false);
  const { data: p, isLoading, error, refetch } = useBizQuery<Product>(["products", id], `/products/${id}`);
  const archive = useBizMutation("POST", `/products/${id}/${p?.is_active ? "archive" : "restore"}`, { invalidate: [["products"]], success: p?.is_active ? "Archived" : "Restored" });
  if (isLoading) return <Skeleton className="h-96" />;
  if (error || !p) return <Card><ErrorState error={error} retry={() => refetch()} /></Card>;
  const manage = isOwner || can("products.manage");
  const cols: Column<Entry>[] = [
    { key: "d", header: "Date", cell: (e) => date(e.entry_date) },
    { key: "s", header: "Source", cell: (e) => <span>{titleCase(e.source_type)}{e.reverses_entry_id && <Badge variant="secondary" className="ml-2">Reversal</Badge>}</span> },
    { key: "q", header: "In / out", align: "right", cell: (e) => <span className={cn("font-medium", Number(e.quantity) < 0 ? "text-destructive" : "text-primary")}>{Number(e.quantity) > 0 ? "+" : ""}{qty(e.quantity)}</span> },
    { key: "c", header: "Cost", align: "right", cell: (e) => money(e.unit_cost), hideOnMobile: true },
    { key: "b", header: "Balance", align: "right", cell: (e) => qty(e.balance_quantity) },
  ];
  const code = p.barcode || p.sku;
  return (
    <>
      <Link href="/inventory" className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeftIcon className="size-4" /> Inventory</Link>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">{p.name}{!p.is_active && <Badge variant="secondary">Archived</Badge>}</h1>
          <p className="text-sm text-muted-foreground">{p.sku} · {titleCase(p.kind)} · GST {Number(p.gst_rate)}%{p.hsn_sac && ` · HSN ${p.hsn_sac}`}</p>
        </div>
        {manage && <div className="flex gap-2">
          <Button variant="outline" onClick={() => archive.mutate(undefined)}><ArchiveIcon /> {p.is_active ? "Archive" : "Restore"}</Button>
          <Button onClick={() => setEdit(true)}><PencilIcon /> Edit</Button>
        </div>}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card><CardHeader><CardTitle>Pricing</CardTitle></CardHeader><CardContent className="space-y-2 text-sm">
          {[["Selling price", p.selling_price], ["Purchase price", p.purchase_price], ["MRP", p.mrp]].map(([k, v]) => (
            <div key={k as string} className="flex justify-between"><span className="text-muted-foreground">{k}</span><span className="num font-medium">{money(v as string)}</span></div>))}
          <p className="pt-1 text-xs text-muted-foreground">{p.prices_include_tax ? "Prices include GST" : "Prices exclude GST"}</p>
        </CardContent></Card>
        <Card><CardHeader><CardTitle>Stock</CardTitle></CardHeader><CardContent className="space-y-2 text-sm">
          <p className="num text-3xl font-semibold">{p.track_inventory ? qty(p.on_hand as string) : "Not tracked"}</p>
          {p.reorder_level && <p className="text-muted-foreground">Reorder at {qty(p.reorder_level)}</p>}
          <div className="flex gap-1 pt-1">{p.batch_tracking && <Badge variant="blue">Batches & expiry</Badge>}{p.serial_tracking && <Badge variant="blue">Serials</Badge>}</div>
        </CardContent></Card>
        <Card><CardHeader className="flex-row items-center justify-between"><CardTitle>Barcode label</CardTitle>
          <Button variant="ghost" size="sm" onClick={() => {
            const svg = document.getElementById("barcode-label")?.innerHTML ?? "";
            const w = window.open("", "_blank", "width=420,height=300");
            if (w) { w.document.write(`<title>${code}</title><body style="margin:16px;font-family:sans-serif"><div style="font-size:12px;margin-bottom:4px">${p.name.replace(/[<>&]/g, "")}</div>${svg}<div style="font-size:13px;font-weight:600;margin-top:4px">${money(p.selling_price)}</div></body>`); w.document.close(); w.print(); }
          }}><PrinterIcon /> Print</Button></CardHeader>
          <CardContent><div id="barcode-label" className="overflow-x-auto rounded-md border bg-white p-3"><Barcode value={code} /></div>
            {!p.barcode && <p className="mt-2 text-xs text-muted-foreground">No barcode saved — showing the SKU. Add the maker&apos;s barcode in Edit.</p>}</CardContent></Card>
      </div>
      {p.track_inventory && <div className="mt-6"><h2 className="mb-3 text-lg font-semibold">Movements</h2>
        <DataTable<Entry> queryKey={["ledger", id]} path="/inventory/ledger" filters={{ product_id: id }} columns={cols} searchParam={null} /></div>}
      <ProductDialog open={edit} onOpenChange={setEdit} product={p} />
    </>
  );
}
