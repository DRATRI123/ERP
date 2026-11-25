"use client";

import { ArrowRightLeftIcon, BoxesIcon, PackagePlusIcon, SlidersHorizontalIcon, WarehouseIcon } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { DataTable, StaticTable, type Column } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ProductDialog } from "@/features/inventory/product-form";
import { StockDialog, type StockMode } from "@/features/inventory/stock-dialog";
import { ExpiryPanel } from "@/features/inventory/expiry-panel";
import { ReorderPanel } from "@/features/inventory/reorder-panel";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money, qty, titleCase } from "@/lib/format";
import type { Product } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Balance { product_id: string; sku: string; product_name: string; warehouse_id: string; warehouse_code: string; batch_no: string | null; expiry_date: string | null; quantity: string; value: string; average_cost: string }
interface StockDoc { id: string; kind: string; number: string; doc_date: string; status: string; reference: string | null; warehouse_id: string; to_warehouse_id: string | null }
interface Low { product_id: string; sku: string; name: string; on_hand: string; reorder_level: string; reorder_quantity: string | null }

function InventoryPage() {
  const router = useRouter();
  const params = useSearchParams();
  const { can, isOwner } = useBusiness();
  const [tab, setTab] = useState(params.get("tab") ?? "products");
  const [newProductState, setNewProduct] = useState(false);
  const newProduct = newProductState || params.get("new") === "1";
  const [stockMode, setStockMode] = useState<StockMode | null>(null);
  const [warehouse, setWarehouse] = useState("");
  const whs = useBizQuery<{ id: string; code: string; name: string }[]>(["warehouses"], "/warehouses");
  const valuation = useBizQuery<{ method: string; total_value: string; warehouses: { stock_lines: number }[] }>(["valuation"], "/inventory/valuation", undefined, { enabled: isOwner || can("stock_value.view") });
  const low = useBizQuery<Low[]>(["low-stock"], "/inventory/low-stock");
  const expiring = useBizQuery<Balance[]>(["expiring"], "/inventory/expiring", { within_days: 60 });
  const manage = isOwner || can("products.manage");
  const editStock = isOwner || can("stock.edit");

  const productCols: Column<Product>[] = [
    { key: "name", header: "Product", cell: (p) => <div><p className="font-medium">{p.name}</p><p className="text-xs text-muted-foreground">{p.sku}{p.barcode && ` · ${p.barcode}`}</p></div>, sortable: true, sortValue: (p) => p.name },
    { key: "hsn", header: "HSN", cell: (p) => p.hsn_sac ?? "—", hideOnMobile: true },
    { key: "gst", header: "GST", cell: (p) => `${Number(p.gst_rate)}%`, hideOnMobile: true },
    { key: "price", header: "Price", align: "right", cell: (p) => money(p.selling_price), sortable: true, sortValue: (p) => Number(p.selling_price ?? 0) },
    { key: "stock", header: "In stock", align: "right", sortable: true, sortValue: (p) => Number(p.on_hand ?? 0), cell: (p) => {
      if (!p.track_inventory) return <span className="text-muted-foreground">—</span>;
      const n = Number(p.on_hand ?? 0), lowp = p.reorder_level !== null && n <= Number(p.reorder_level);
      return <span className={cn("font-medium", n <= 0 ? "text-destructive" : lowp && "text-amber-600 dark:text-warning")}>{qty(p.on_hand as string)}</span>;
    } },
    { key: "flags", header: "", hideOnMobile: true, cell: (p) => <div className="flex gap-1">{p.batch_tracking && <Badge variant="blue">Batches</Badge>}{p.serial_tracking && <Badge variant="blue">Serials</Badge>}{!p.is_active && <Badge variant="secondary">Archived</Badge>}</div> },
  ];
  const balanceCols: Column<Balance>[] = [
    { key: "p", header: "Product", cell: (b) => <div><p className="font-medium">{b.product_name}</p><p className="text-xs text-muted-foreground">{b.sku}</p></div> },
    { key: "w", header: "Warehouse", cell: (b) => <Badge variant="outline">{b.warehouse_code}</Badge> },
    { key: "b", header: "Batch", cell: (b) => b.batch_no ?? "—", hideOnMobile: true },
    { key: "e", header: "Expiry", cell: (b) => b.expiry_date ? date(b.expiry_date) : "—", hideOnMobile: true },
    { key: "q", header: "Quantity", align: "right", cell: (b) => qty(b.quantity) },
    { key: "c", header: "Avg cost", align: "right", cell: (b) => money(b.average_cost), hideOnMobile: true },
    { key: "v", header: "Value", align: "right", cell: (b) => money(b.value) },
  ];
  const docCols: Column<StockDoc>[] = [
    { key: "n", header: "Number", cell: (d) => <span className="font-medium">{d.number}</span> },
    { key: "k", header: "Type", cell: (d) => titleCase(d.kind) },
    { key: "d", header: "Date", cell: (d) => date(d.doc_date) },
    { key: "r", header: "Reference", cell: (d) => d.reference ?? "—", hideOnMobile: true },
    { key: "s", header: "Status", cell: (d) => <Badge variant={d.status === "cancelled" ? "destructive" : "default"}>{titleCase(d.status)}</Badge> },
  ];

  return (
    <>
      <PageHeader title="Products & inventory" description="Your catalogue, live stock by warehouse and batch, and every movement."
        actions={<>
          {editStock && <>
            <Button variant="outline" onClick={() => setStockMode("adjustment")}><SlidersHorizontalIcon /> Adjust</Button>
            <Button variant="outline" onClick={() => setStockMode("transfer")}><ArrowRightLeftIcon /> Transfer</Button>
          </>}
          {manage && <Button onClick={() => setNewProduct(true)}><PackagePlusIcon /> New product</Button>}
        </>} />
      <div className="mb-4 sm:mb-6 grid grid-cols-2 gap-3 sm:gap-4 sm:grid-cols-3">
        <StatCard label="Stock value" icon={WarehouseIcon} loading={valuation.isLoading} value={valuation.data ? money(valuation.data.total_value) : "—"} sub={valuation.data && `Valued at ${titleCase(valuation.data.method)}`} />
        <StatCard label="Below reorder level" icon={BoxesIcon} tone={low.data?.length ? "red" : "green"} loading={low.isLoading} value={low.data?.length ?? "—"} />
        <StatCard label="Expiring in 60 days" icon={BoxesIcon} tone={expiring.data?.length ? "amber" : "blue"} loading={expiring.isLoading} value={expiring.data?.length ?? "—"} />
      </div>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="products">Products</TabsTrigger><TabsTrigger value="stock">Stock</TabsTrigger>
          <TabsTrigger value="alerts">Alerts{low.data?.length ? <Badge variant="destructive" className="ml-1 px-1.5">{low.data.length}</Badge> : null}</TabsTrigger>
          <TabsTrigger value="reorder">Reorder</TabsTrigger>
          <TabsTrigger value="expiry">Expiry</TabsTrigger>
          <TabsTrigger value="movements">Movements</TabsTrigger>
        </TabsList>
        <TabsContent value="products">
          <DataTable<Product> queryKey={["products"]} path="/products" columns={productCols} searchPlaceholder="Name, SKU, barcode or HSN…"
            onRowClick={(p) => router.push(`/inventory/products/${p.id}`)}
            empty={<EmptyState icon={PackagePlusIcon} title="No products yet" description="Add your first product, or import a CSV/Excel file from Settings → Data." action={manage && <Button onClick={() => setNewProduct(true)}>New product</Button>} />} />
        </TabsContent>
        <TabsContent value="stock">
          <DataTable<Balance> queryKey={["balances"]} path="/inventory/balances" columns={balanceCols} rowKey={(b, i) => `${b.product_id}-${b.warehouse_id}-${b.batch_no}-${i}`}
            filters={{ warehouse_id: warehouse || undefined }}
            toolbar={<NativeSelect className="w-44" value={warehouse} onChange={(e) => setWarehouse(e.target.value)} aria-label="Warehouse">
              <option value="">All warehouses</option>{whs.data?.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}</NativeSelect>} />
        </TabsContent>
        <TabsContent value="alerts" className="grid gap-4 lg:grid-cols-2">
          <Card className="overflow-hidden">
            <CardHeader className="pb-3"><CardTitle>Below reorder level</CardTitle></CardHeader>
            {low.isLoading ? <TableSkeleton rows={4} cols={3} /> : low.error ? <ErrorState error={low.error} /> : !low.data?.length ? <EmptyState title="Nothing to reorder" /> : (
              <StaticTable rows={low.data} rowKey={(r) => r.product_id} columns={[
                { key: "n", header: "Product", cell: (r) => <div><p className="font-medium">{r.name}</p><p className="text-xs text-muted-foreground">{r.sku}</p></div> },
                { key: "o", header: "On hand", align: "right", cell: (r) => <span className="font-medium text-destructive">{qty(r.on_hand)}</span> },
                { key: "r", header: "Reorder at", align: "right", cell: (r) => qty(r.reorder_level) },
                { key: "q", header: "Suggested", align: "right", cell: (r) => qty(r.reorder_quantity ?? String(Math.max(Number(r.reorder_level) * 2 - Number(r.on_hand), 0))) },
              ]} />
            )}
          </Card>
          <Card className="overflow-hidden">
            <CardHeader className="pb-3"><CardTitle>Batches expiring within 60 days</CardTitle></CardHeader>
            {expiring.isLoading ? <TableSkeleton rows={4} cols={3} /> : !expiring.data?.length ? <EmptyState title="No batches expiring soon" /> : (
              <StaticTable rows={expiring.data} rowKey={(r, i) => `${r.product_id}-${r.batch_no}-${i}`} columns={[
                { key: "n", header: "Product", cell: (r) => r.product_name }, { key: "b", header: "Batch", cell: (r) => r.batch_no ?? "—" },
                { key: "e", header: "Expiry", cell: (r) => <span className={cn(r.expiry_date && r.expiry_date < new Date().toISOString().slice(0, 10) && "text-destructive")}>{date(r.expiry_date)}</span> },
                { key: "q", header: "Qty", align: "right", cell: (r) => qty(r.quantity) },
              ]} />
            )}
          </Card>
        </TabsContent>
        <TabsContent value="reorder"><ReorderPanel /></TabsContent>
        <TabsContent value="expiry"><ExpiryPanel /></TabsContent>
        <TabsContent value="movements">
          <DataTable<StockDoc> queryKey={["stock-docs"]} path="/inventory/documents" columns={docCols} searchParam={null}
            toolbar={editStock && <Button variant="outline" size="sm" onClick={() => setStockMode("opening")}>Opening stock</Button>} />
        </TabsContent>
      </Tabs>
      <ProductDialog open={newProduct} onOpenChange={(v) => { setNewProduct(v); if (!v && params.get("new")) router.replace("/inventory"); }} />
      {stockMode && <StockDialog mode={stockMode} open onOpenChange={(v) => !v && setStockMode(null)} />}
    </>
  );
}

export default function Page() {
  return <Suspense><InventoryPage /></Suspense>;
}
