"use client";

import { CheckCircle2Icon, FactoryIcon, FlaskConicalIcon, Loader2Icon, PencilIcon, PlusIcon, Trash2Icon, XCircleIcon, XIcon } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "@/components/data/confirm";
import { type Column, DataTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState } from "@/components/data/states";
import { ProductSearch } from "@/components/pickers/product-search";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, Sheet } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { useDebounced } from "@/hooks/use-debounce";
import { date, money, qty, todayISO } from "@/lib/format";
import { cn } from "@/lib/utils";

interface RecipeLine { product_id: string; product_name?: string | null; quantity: string; wastage_percent: string }
interface Recipe {
  id: string; name: string; product_id: string; product_name: string | null; output_quantity: string; overhead: string;
  requires_qc: boolean; shelf_life_days: number | null; is_active: boolean; notes: string | null; lines: RecipeLine[];
}
interface Run {
  id: string; number: string; recipe_id: string; product_name: string | null; doc_date: string; quantity: string; batch_no: string | null;
  expiry_date: string | null; material_cost: string | null; overhead: string | null; total_cost: string | null; unit_cost: string | null;
  status: "completed" | "cancelled"; qc_status: "not_required" | "pending" | "released" | "rejected"; qc_notes: string | null;
}
interface Plan {
  materials: { product_id: string; name: string; sku: string; required: string; available: string; shortage: string; estimated_cost: string | null }[];
  material_cost: string | null; overhead: string | null; total_cost: string | null; unit_cost: string | null; can_produce: boolean;
}

const QC: Record<Run["qc_status"], ["default" | "warning" | "destructive" | "secondary", string]> = {
  not_required: ["secondary", "No QC"], pending: ["warning", "QC pending"], released: ["default", "Released"], rejected: ["destructive", "Rejected"],
};

export default function ManufacturingPage() {
  const { can, isOwner } = useBusiness();
  const manage = isOwner || can("production.manage");
  const [tab, setTab] = useState("runs");
  const [producing, setProducing] = useState(false);
  const [openRun, setOpenRun] = useState<Run | null>(null);
  const recipes = useBizQuery<Recipe[]>(["recipes"], "/recipes");
  const pending = useBizQuery<{ total: number }>(["production", "pending"], "/production", { qc_status: "pending", limit: 1 });
  const cols: Column<Run>[] = [
    { key: "n", header: "Run", cell: (r) => <div><p className="font-medium">{r.number}</p><p className="text-xs text-muted-foreground">{date(r.doc_date)}</p></div> },
    { key: "p", header: "Product", cell: (r) => <div><p>{r.product_name}</p>{r.batch_no && <p className="text-xs text-muted-foreground">Batch {r.batch_no}{r.expiry_date ? ` · exp ${date(r.expiry_date)}` : ""}</p>}</div> },
    { key: "q", header: "Quantity", align: "right", cell: (r) => qty(r.quantity) },
    { key: "c", header: "Cost / unit", align: "right", hideOnMobile: true, cell: (r) => r.unit_cost !== null ? money(r.unit_cost) : "—" },
    { key: "s", header: "Status", cell: (r) => r.status === "cancelled" ? <Badge variant="secondary">Cancelled</Badge> : <Badge variant={QC[r.qc_status][0]}>{QC[r.qc_status][1]}</Badge> },
  ];
  return (
    <>
      <PageHeader title="Manufacturing" description="Recipes, production runs with real material costs, and quality-control release of finished batches."
        actions={manage && <Button onClick={() => setProducing(true)} disabled={!recipes.data?.some((r) => r.is_active)}><FactoryIcon /> Record production</Button>} />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="runs">Production{pending.data?.total ? <Badge variant="warning" className="ml-1 px-1.5">{pending.data.total} QC</Badge> : null}</TabsTrigger>
          <TabsTrigger value="recipes">Recipes{recipes.data?.length ? ` · ${recipes.data.length}` : ""}</TabsTrigger>
        </TabsList>
        <TabsContent value="runs">
          <DataTable<Run> queryKey={["production"]} path="/production" columns={cols} searchParam={null} onRowClick={setOpenRun}
            empty={<EmptyState icon={FactoryIcon} title="No production yet" description={recipes.data?.length ? "Record a production run: materials leave stock, the finished batch arrives at its true cost." : "Create a recipe first, then record production against it."}
              action={manage && !recipes.data?.length && <Button onClick={() => setTab("recipes")}>Create a recipe</Button>} />} />
        </TabsContent>
        <TabsContent value="recipes"><Recipes manage={manage} recipes={recipes} /></TabsContent>
      </Tabs>
      {producing && recipes.data && <ProduceDialog recipes={recipes.data.filter((r) => r.is_active)} onClose={() => setProducing(false)} />}
      <Sheet open={!!openRun} onOpenChange={(v) => !v && setOpenRun(null)} side="right" className="w-[min(520px,100vw)] max-w-none">
        {openRun && <RunPanel key={openRun.id} id={openRun.id} manage={manage} onClose={() => setOpenRun(null)} />}
      </Sheet>
    </>
  );
}

// ---------------------------------------------------------------------------- recipes

function Recipes({ manage, recipes }: { manage: boolean; recipes: ReturnType<typeof useBizQuery<Recipe[]>> }) {
  const [editing, setEditing] = useState<{ r?: Recipe; key: string } | null>(null);
  const [deleting, setDeleting] = useState<Recipe | null>(null);
  const remove = useBizMutation("DELETE", () => `/recipes/${deleting?.id}`, { invalidate: [["recipes"]], success: "Recipe deleted" });
  if (recipes.error) return <Card><ErrorState error={recipes.error} retry={() => recipes.refetch()} /></Card>;
  return (
    <div className="space-y-4">
      {manage && <div className="flex justify-end"><Button onClick={() => setEditing({ key: `new-${Date.now()}` })}><PlusIcon /> New recipe</Button></div>}
      {recipes.isLoading ? <Skeleton className="h-40" /> : !recipes.data?.length ? (
        <Card><EmptyState icon={FlaskConicalIcon} title="No recipes" description="A recipe says what goes into a batch: e.g. 100 jars of churna = 25 kg herb (+4 % wastage) + 100 jars + ₹1,500 labour and packing."
          action={manage && <Button onClick={() => setEditing({ key: `new-${Date.now()}` })}><PlusIcon /> New recipe</Button>} /></Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {recipes.data.map((r) => (
            <Card key={r.id} className={r.is_active ? "" : "opacity-70"}>
              <CardContent className="space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <div><p className="font-semibold">{r.name}</p><p className="text-sm text-muted-foreground">Makes {qty(r.output_quantity)} × {r.product_name}</p></div>
                  <div className="flex gap-1">{r.requires_qc && <Badge variant="blue">QC</Badge>}{!r.is_active && <Badge variant="secondary">Off</Badge>}</div>
                </div>
                <ul className="space-y-0.5 text-sm">{r.lines.map((l) => (
                  <li key={l.product_id} className="flex justify-between"><span className="truncate">{l.product_name}</span>
                    <span className="num text-muted-foreground">{qty(l.quantity)}{Number(l.wastage_percent) ? ` + ${Number(l.wastage_percent)} %` : ""}</span></li>))}</ul>
                <div className="flex items-center justify-between border-t pt-2 text-sm">
                  <span className="text-muted-foreground">Overhead {money(r.overhead)}{r.shelf_life_days ? ` · shelf life ${r.shelf_life_days} days` : ""}</span>
                  {manage && <div><Button size="icon" variant="ghost" aria-label="Edit recipe" onClick={() => setEditing({ r, key: `${r.id}-${Date.now()}` })}><PencilIcon /></Button>
                    <Button size="icon" variant="ghost" aria-label="Delete recipe" onClick={() => setDeleting(r)}><Trash2Icon /></Button></div>}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      {editing && <RecipeDialog key={editing.key} recipe={editing.r} onClose={() => setEditing(null)} />}
      <ConfirmDialog open={!!deleting} onOpenChange={(v) => !v && setDeleting(null)} destructive confirm="Delete" title={`Delete recipe “${deleting?.name}”?`}
        description="Recipes with production history cannot be deleted; switch them off instead." onConfirm={() => remove.mutateAsync(undefined)} />
    </div>
  );
}

function RecipeDialog({ recipe, onClose }: { recipe?: Recipe; onClose: () => void }) {
  const [name, setName] = useState(recipe?.name ?? "");
  const [product, setProduct] = useState<{ id: string; name: string } | null>(recipe ? { id: recipe.product_id, name: recipe.product_name ?? "" } : null);
  const [output, setOutput] = useState(recipe ? String(Number(recipe.output_quantity)) : "100");
  const [overhead, setOverhead] = useState(recipe ? String(Number(recipe.overhead)) : "0");
  const [shelf, setShelf] = useState(recipe?.shelf_life_days ? String(recipe.shelf_life_days) : "");
  const [qc, setQc] = useState(recipe?.requires_qc ?? false);
  const [active, setActive] = useState(recipe?.is_active ?? true);
  const [lines, setLines] = useState<RecipeLine[]>(recipe?.lines.map((l) => ({ ...l, quantity: String(Number(l.quantity)), wastage_percent: String(Number(l.wastage_percent)) })) ?? []);
  const save = useBizMutation<Record<string, unknown>>(recipe ? "PUT" : "POST", recipe ? `/recipes/${recipe.id}` : "/recipes",
    { invalidate: [["recipes"]], success: "Recipe saved", onSuccess: onClose });
  const setLine = (i: number, patch: Partial<RecipeLine>) => setLines((xs) => xs.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const ready = name.trim() && product && Number(output) > 0 && lines.length > 0 && lines.every((l) => Number(l.quantity) > 0);
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide className="max-h-[92vh] overflow-y-auto">
        <DialogHeader><DialogTitle>{recipe ? "Edit recipe" : "New recipe"}</DialogTitle>
          <DialogDescription>Quantities are for one batch of the output quantity; production scales them.</DialogDescription></DialogHeader>
        <form className="grid gap-4" onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ name, product_id: product!.id, output_quantity: output, overhead: overhead || "0", requires_qc: qc, is_active: active,
            shelf_life_days: shelf ? Number(shelf) : null, notes: recipe?.notes ?? null,
            lines: lines.map((l) => ({ product_id: l.product_id, quantity: l.quantity, wastage_percent: l.wastage_percent || "0" })) });
        }}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name" htmlFor="rc-name" className="sm:col-span-2"><Input id="rc-name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder="Ashwagandha Churna 250 g" /></Field>
            <Field label="Finished product" className="sm:col-span-2">
              {product ? <div className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">{product.name}<Button type="button" size="sm" variant="ghost" onClick={() => setProduct(null)}>Change</Button></div>
                : <ProductSearch placeholder="Search the product you make…" onPick={(p) => setProduct({ id: p.id, name: p.name })} />}
            </Field>
            <Field label="Makes (quantity per batch)" htmlFor="rc-out"><Input id="rc-out" inputMode="decimal" value={output} onChange={(e) => setOutput(e.target.value.replace(/[^\d.]/g, ""))} /></Field>
            <Field label="Overhead per batch (₹)" htmlFor="rc-oh" hint="Labour, fuel, packing — added to the cost"><Input id="rc-oh" inputMode="decimal" value={overhead} onChange={(e) => setOverhead(e.target.value.replace(/[^\d.]/g, ""))} /></Field>
            <Field label="Shelf life (days)" htmlFor="rc-shelf" hint="Sets the batch expiry automatically"><Input id="rc-shelf" inputMode="numeric" value={shelf} onChange={(e) => setShelf(e.target.value.replace(/\D/g, ""))} placeholder="730" /></Field>
            <div className="flex flex-col justify-end gap-2 pb-1">
              <label className="flex items-center gap-2 text-sm"><Switch checked={qc} onCheckedChange={setQc} /> Hold batches for quality control</label>
              <label className="flex items-center gap-2 text-sm"><Switch checked={active} onCheckedChange={setActive} /> Active</label>
            </div>
          </div>
          <div>
            <p className="mb-2 text-sm font-medium">Materials</p>
            {lines.length > 0 && <div className="mb-2 divide-y rounded-lg border">
              <div className="grid grid-cols-[1fr_100px_90px_auto] gap-2 px-3 py-1.5 text-xs text-muted-foreground uppercase"><span>Material</span><span className="text-right">Quantity</span><span className="text-right">Wastage %</span><span className="w-9" /></div>
              {lines.map((l, i) => (
                <div key={l.product_id} className="grid grid-cols-[1fr_100px_90px_auto] items-center gap-2 px-3 py-1.5">
                  <span className="truncate text-sm">{l.product_name}</span>
                  <Input className="num h-8 text-right" inputMode="decimal" aria-label={`Quantity of ${l.product_name}`} value={l.quantity} onChange={(e) => setLine(i, { quantity: e.target.value.replace(/[^\d.]/g, "") })} />
                  <Input className="num h-8 text-right" inputMode="decimal" aria-label={`Wastage of ${l.product_name}`} value={l.wastage_percent} onChange={(e) => setLine(i, { wastage_percent: e.target.value.replace(/[^\d.]/g, "") })} />
                  <Button type="button" size="icon" variant="ghost" aria-label="Remove material" onClick={() => setLines((xs) => xs.filter((_, j) => j !== i))}><XIcon /></Button>
                </div>
              ))}
            </div>}
            <ProductSearch placeholder="Add a material (herb, oil, jar, label…)" onPick={(p) => setLines((xs) => xs.some((x) => x.product_id === p.id) ? xs
              : [...xs, { product_id: p.id, product_name: p.name, quantity: "1", wastage_percent: "0" }])} />
          </div>
          <DialogFooter><Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!ready || save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />}Save recipe</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------- production

function ProduceDialog({ recipes, onClose }: { recipes: Recipe[]; onClose: () => void }) {
  const whs = useBizQuery<{ id: string; code: string; name: string }[]>(["warehouses"], "/warehouses");
  const [recipeId, setRecipeId] = useState(recipes[0]?.id ?? "");
  const recipe = recipes.find((r) => r.id === recipeId);
  const [quantity, setQuantity] = useState(recipe ? String(Number(recipe.output_quantity)) : "");
  const [warehouse, setWarehouse] = useState("");
  const [batch, setBatch] = useState("");
  const [mrp, setMrp] = useState("");
  const [docDate, setDocDate] = useState(todayISO());
  const [notes, setNotes] = useState("");
  const wh = warehouse || whs.data?.[0]?.id || "";
  const q = useDebounced(quantity, 300);
  const plan = useBizQuery<Plan>(["plan", recipeId, q, wh], `/recipes/${recipeId}/plan`, { quantity: q, warehouse_id: wh },
    { enabled: !!recipeId && Number(q) > 0 && !!wh, retry: false });
  const save = useBizMutation<Record<string, unknown>, Run>("POST", "/production", {
    invalidate: [["production"], ["balances"], ["reorder"]], success: (r) => `${r.number}: ${qty(r.quantity)} × ${r.product_name} added to stock${r.qc_status === "pending" ? " (on QC hold)" : ""}`,
    onSuccess: onClose,
  });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide className="max-h-[92vh] overflow-y-auto">
        <DialogHeader><DialogTitle>Record production</DialogTitle>
          <DialogDescription>Materials leave stock now (earliest expiry first) and the finished batch arrives at material cost plus overhead.</DialogDescription></DialogHeader>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ recipe_id: recipeId, quantity, warehouse_id: wh, doc_date: docDate, batch_no: batch || undefined, mrp: mrp || undefined, notes: notes || undefined });
        }}>
          <Field label="Recipe" htmlFor="pr-recipe" className="sm:col-span-2"><NativeSelect id="pr-recipe" value={recipeId} onChange={(e) => {
            setRecipeId(e.target.value); const r = recipes.find((x) => x.id === e.target.value); if (r) setQuantity(String(Number(r.output_quantity)));
          }}>{recipes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</NativeSelect></Field>
          <Field label={`Quantity of ${recipe?.product_name ?? "product"}`} htmlFor="pr-qty"><Input id="pr-qty" inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value.replace(/[^\d.]/g, ""))} /></Field>
          <Field label="Warehouse" htmlFor="pr-wh"><NativeSelect id="pr-wh" value={wh} onChange={(e) => setWarehouse(e.target.value)}>
            {whs.data?.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}</NativeSelect></Field>
          <Field label="Batch number" htmlFor="pr-batch" hint="Required for batch-tracked products"><Input id="pr-batch" value={batch} maxLength={60} onChange={(e) => setBatch(e.target.value)} placeholder="AC-2611-01" /></Field>
          <Field label="MRP of this batch" htmlFor="pr-mrp"><Input id="pr-mrp" inputMode="decimal" value={mrp} onChange={(e) => setMrp(e.target.value.replace(/[^\d.]/g, ""))} /></Field>
          <Field label="Date" htmlFor="pr-date"><Input id="pr-date" type="date" value={docDate} onChange={(e) => setDocDate(e.target.value)} /></Field>
          <Field label="Notes" htmlFor="pr-notes"><Input id="pr-notes" value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>

          <div className="rounded-lg border sm:col-span-2">
            <div className="grid grid-cols-[1fr_90px_90px_90px] gap-2 border-b bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground uppercase">
              <span>Material</span><span className="text-right">Needed</span><span className="text-right">In stock</span><span className="text-right">Cost</span></div>
            {plan.isLoading ? <Skeleton className="m-3 h-16" /> : plan.data?.materials.map((m) => (
              <div key={m.product_id} className={cn("grid grid-cols-[1fr_90px_90px_90px] gap-2 px-3 py-1.5 text-sm", Number(m.shortage) > 0 && "bg-destructive/5")}>
                <span className="truncate">{m.name}</span><span className="num text-right">{qty(m.required)}</span>
                <span className={cn("num text-right", Number(m.shortage) > 0 && "font-medium text-destructive")}>{qty(m.available)}</span>
                <span className="num text-right text-muted-foreground">{m.estimated_cost !== null ? money(m.estimated_cost) : "—"}</span>
              </div>
            ))}
            {plan.data && <div className="flex flex-wrap justify-between gap-2 border-t px-3 py-2 text-sm">
              <span className={plan.data.can_produce ? "text-success" : "text-destructive"}>{plan.data.can_produce ? "All materials in stock" : "Short of materials — buy them first"}</span>
              {plan.data.total_cost !== null && <span>Estimated {money(plan.data.total_cost)} · <b>{money(plan.data.unit_cost)}</b> a unit (overhead {money(plan.data.overhead)})</span>}
            </div>}
          </div>
          <DialogFooter className="sm:col-span-2"><Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!recipeId || !(Number(quantity) > 0) || !plan.data?.can_produce || save.isPending}>
              {save.isPending ? <Loader2Icon className="animate-spin" /> : <FactoryIcon />} Record production</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RunPanel({ id, manage, onClose }: { id: string; manage: boolean; onClose: () => void }) {
  const run = useBizQuery<Run & { materials: { product_id: string; name: string; batch_no: string | null; quantity: string; value: string | null }[] }>(["production", id], `/production/${id}`);
  const [notes, setNotes] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const qc = useBizMutation<{ release: boolean; notes?: string }>("POST", `/production/${id}/qc`, {
    invalidate: [["production"], ["balances"]], success: "Quality decision recorded" });
  const cancel = useBizMutation<{ reason: string }>("POST", `/production/${id}/cancel`, {
    invalidate: [["production"], ["balances"]], success: "Production cancelled; materials returned to stock", onSuccess: onClose });
  if (run.error) return <div className="p-6"><ErrorState error={run.error} /></div>;
  if (!run.data) return <div className="space-y-3 p-6"><Skeleton className="h-8 w-1/2" /><Skeleton className="h-40" /></div>;
  const r = run.data;
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between border-b p-4">
        <div><p className="font-semibold">{r.number}</p><p className="text-sm text-muted-foreground">{qty(r.quantity)} × {r.product_name} · {date(r.doc_date)}</p></div>
        <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close"><XIcon /></Button>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        <div className="flex flex-wrap gap-2">
          {r.status === "cancelled" ? <Badge variant="secondary">Cancelled</Badge> : <Badge variant={QC[r.qc_status][0]}>{QC[r.qc_status][1]}</Badge>}
          {r.batch_no && <Badge variant="blue">Batch {r.batch_no}</Badge>}{r.expiry_date && <Badge variant="outline">Expires {date(r.expiry_date)}</Badge>}
        </div>
        {r.total_cost !== null && <div className="grid grid-cols-2 gap-3 text-sm">
          {[["Materials", r.material_cost], ["Overhead", r.overhead], ["Total cost", r.total_cost], ["Cost per unit", r.unit_cost]].map(([k, v]) => (
            <div key={k} className="rounded-lg bg-muted/50 p-3"><p className="text-xs text-muted-foreground">{k}</p><p className="num font-semibold">{money(v)}</p></div>))}
        </div>}
        <div><h3 className="mb-2 text-sm font-semibold">Materials used</h3>
          <ul className="divide-y rounded-lg border text-sm">{r.materials.map((m, i) => (
            <li key={i} className="flex justify-between gap-2 px-3 py-2"><span>{m.name}{m.batch_no && <span className="text-muted-foreground"> · {m.batch_no}</span>}</span>
              <span className="num">{qty(m.quantity)}{m.value !== null && <span className="text-muted-foreground"> · {money(m.value)}</span>}</span></li>))}</ul></div>
        {r.qc_notes && <p className="rounded-md bg-muted/50 p-3 text-sm"><b>QC:</b> {r.qc_notes}</p>}
        {manage && r.status === "completed" && r.qc_status === "pending" && (
          <div className="space-y-2 rounded-lg border border-warning/50 p-3">
            <p className="text-sm font-medium">Quality control — the batch cannot be sold until released.</p>
            <Textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Test results, e.g. moisture 4 %, microbial count within limits" aria-label="QC notes" />
            <div className="flex gap-2">
              <Button disabled={qc.isPending} onClick={() => qc.mutate({ release: true, notes: notes || undefined })}><CheckCircle2Icon /> Release</Button>
              <Button variant="destructive" disabled={qc.isPending} onClick={() => qc.mutate({ release: false, notes: notes || undefined })}><XCircleIcon /> Reject & write off</Button>
            </div>
          </div>
        )}
        {manage && r.status === "completed" && r.qc_status !== "rejected" && <Button variant="ghost" className="text-destructive" onClick={() => setCancelling(true)}>Cancel this production…</Button>}
      </div>
      <ConfirmDialog open={cancelling} onOpenChange={setCancelling} destructive reason confirm="Cancel production" title={`Cancel ${r.number}?`}
        description="Materials go back into stock and the finished batch is removed (it must still be fully in stock)." onConfirm={(reason) => cancel.mutateAsync({ reason })} />
    </div>
  );
}
