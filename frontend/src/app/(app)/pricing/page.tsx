"use client";

import { GiftIcon, Loader2Icon, PencilIcon, PercentIcon, PlusIcon, TagsIcon, Trash2Icon, XIcon } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "@/components/data/confirm";
import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState } from "@/components/data/states";
import { ProductSearch } from "@/components/pickers/product-search";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money, qty } from "@/lib/format";
import type { Product } from "@/lib/types";

interface Item { product_id: string; product_name?: string | null; min_quantity: string; price: string }
interface PriceList { id: string; name: string; base: string; adjust_percent: string; prices_include_tax: boolean | null; is_active: boolean; items: Item[]; customers: number }
interface Scheme {
  id: string; name: string; kind: "free_goods" | "discount"; product_id: string | null; category_id: string | null; min_quantity: string;
  free_quantity: string; discount_percent: string; valid_from: string | null; valid_to: string | null; is_active: boolean; product_name?: string | null;
}
type SchemeDraft = Omit<Scheme, "id" | "product_name">;

const BASE_LABEL: Record<string, string> = { selling: "Selling price", wholesale: "Wholesale price", mrp: "MRP", purchase: "Purchase price" };

export default function PricingPage() {
  const { can, isOwner } = useBusiness();
  const manage = isOwner || can("products.manage");
  const [tab, setTab] = useState("lists");
  return (
    <>
      <PageHeader title="Pricing & schemes" description="Customer-type price lists (hospitals, doctors, stockists) with quantity breaks, and trade schemes like 10 + 1 free." />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList><TabsTrigger value="lists">Price lists</TabsTrigger><TabsTrigger value="schemes">Schemes</TabsTrigger></TabsList>
        <TabsContent value="lists"><PriceLists manage={manage} /></TabsContent>
        <TabsContent value="schemes"><Schemes manage={manage} /></TabsContent>
      </Tabs>
    </>
  );
}

// ---------------------------------------------------------------------------- price lists

function PriceLists({ manage }: { manage: boolean }) {
  const list = useBizQuery<PriceList[]>(["price-lists"], "/price-lists");
  const [editing, setEditing] = useState<{ pl?: PriceList; key: string } | null>(null);
  const [deleting, setDeleting] = useState<PriceList | null>(null);
  const remove = useBizMutation("DELETE", () => `/price-lists/${deleting?.id}`, { invalidate: [["price-lists"]], success: "Price list deleted" });
  if (list.error) return <Card><ErrorState error={list.error} retry={() => list.refetch()} /></Card>;
  return (
    <div className="space-y-4">
      {manage && <div className="flex justify-end"><Button onClick={() => setEditing({ key: `new-${Date.now()}` })}><PlusIcon /> New price list</Button></div>}
      {list.isLoading ? <Skeleton className="h-40" /> : !list.data?.length ? (
        <Card><EmptyState icon={TagsIcon} title="No price lists" description="Create “Hospitals: MRP − 15 %” or “Doctors: selling price − 10 %”, then choose it on the customer. Their bills price automatically."
          action={manage && <Button onClick={() => setEditing({ key: `new-${Date.now()}` })}><PlusIcon /> New price list</Button>} /></Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {list.data.map((pl) => (
            <Card key={pl.id} className={pl.is_active ? "" : "opacity-70"}>
              <CardContent className="space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-semibold">{pl.name}</p>
                  {!pl.is_active && <Badge variant="secondary">Off</Badge>}
                </div>
                <p className="text-sm text-muted-foreground">{BASE_LABEL[pl.base]} {Number(pl.adjust_percent) === 0 ? "" : `${Number(pl.adjust_percent) > 0 ? "+" : "−"} ${Math.abs(Number(pl.adjust_percent))} %`}
                  {pl.items.length > 0 && ` · ${new Set(pl.items.map((i) => i.product_id)).size} fixed prices`}</p>
                <div className="flex items-center justify-between border-t pt-2">
                  <span className="text-sm text-muted-foreground">{pl.customers} customer{pl.customers === 1 ? "" : "s"}</span>
                  {manage && <div><Button size="icon" variant="ghost" aria-label="Edit price list" onClick={() => setEditing({ pl, key: `${pl.id}-${Date.now()}` })}><PencilIcon /></Button>
                    <Button size="icon" variant="ghost" aria-label="Delete price list" onClick={() => setDeleting(pl)}><Trash2Icon /></Button></div>}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      {editing && <PriceListDialog key={editing.key} pl={editing.pl} onClose={() => setEditing(null)} />}
      <ConfirmDialog open={!!deleting} onOpenChange={(v) => !v && setDeleting(null)} destructive confirm="Delete"
        title={`Delete ${deleting?.name}?`} description="Customers using it must be moved to another list first. Invoices already made are not changed."
        onConfirm={() => remove.mutateAsync(undefined)} />
    </div>
  );
}

function PriceListDialog({ pl, onClose }: { pl?: PriceList; onClose: () => void }) {
  const [name, setName] = useState(pl?.name ?? "");
  const [base, setBase] = useState(pl?.base ?? "selling");
  const [adjust, setAdjust] = useState(pl ? String(Number(pl.adjust_percent)) : "0");
  const [incl, setIncl] = useState(pl?.prices_include_tax === null || !pl ? "" : String(pl.prices_include_tax));
  const [active, setActive] = useState(pl?.is_active ?? true);
  const [items, setItems] = useState<Item[]>(pl?.items.map((i) => ({ ...i, min_quantity: String(Number(i.min_quantity)), price: String(Number(i.price)) })) ?? []);
  const save = useBizMutation<Record<string, unknown>>(pl ? "PATCH" : "POST", pl ? `/price-lists/${pl.id}` : "/price-lists",
    { invalidate: [["price-lists"]], success: "Price list saved", onSuccess: onClose });
  const setItem = (i: number, patch: Partial<Item>) => setItems((xs) => xs.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const example = 100 * (1 + Number(adjust || 0) / 100);
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide className="max-h-[92vh] overflow-y-auto">
        <DialogHeader><DialogTitle>{pl ? "Edit price list" : "New price list"}</DialogTitle>
          <DialogDescription>Customers on this list are billed at these prices unless the counter types another rate.</DialogDescription></DialogHeader>
        <form className="grid gap-4" onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ name, base, adjust_percent: adjust || "0", prices_include_tax: incl === "" ? null : incl === "true", is_active: active,
            items: items.filter((i) => i.price !== "").map((i) => ({ product_id: i.product_id, min_quantity: i.min_quantity || "1", price: i.price })) });
        }}>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Name" htmlFor="pl-name" className="sm:col-span-3"><Input id="pl-name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Hospitals" /></Field>
            <Field label="Start from" htmlFor="pl-base"><NativeSelect id="pl-base" value={base} onChange={(e) => setBase(e.target.value)}>
              {Object.entries(BASE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</NativeSelect></Field>
            <Field label="Adjust by %" htmlFor="pl-adj" hint={`e.g. −15 → ₹100 becomes ${money(example)}`}><Input id="pl-adj" inputMode="decimal" value={adjust} onChange={(e) => setAdjust(e.target.value.replace(/[^\d.-]/g, ""))} /></Field>
            <Field label="Prices include GST?" htmlFor="pl-incl"><NativeSelect id="pl-incl" value={incl} onChange={(e) => setIncl(e.target.value)}>
              <option value="">As the product says</option><option value="true">Yes</option><option value="false">No</option></NativeSelect></Field>
          </div>
          <div>
            <p className="mb-2 text-sm font-medium">Fixed prices <span className="font-normal text-muted-foreground">— override the % for these products; add a higher minimum quantity for slab prices</span></p>
            {items.length > 0 && <div className="mb-2 divide-y rounded-lg border">
              {items.map((it, i) => (
                <div key={i} className="grid grid-cols-[1fr_90px_110px_auto] items-center gap-2 px-3 py-1.5">
                  <span className="truncate text-sm">{it.product_name ?? "Product"}</span>
                  <Input aria-label="Minimum quantity" className="h-8" inputMode="decimal" value={it.min_quantity} onChange={(e) => setItem(i, { min_quantity: e.target.value.replace(/[^\d.]/g, "") })} placeholder="Min qty" />
                  <Input aria-label="Price" className="num h-8 text-right" inputMode="decimal" value={it.price} onChange={(e) => setItem(i, { price: e.target.value.replace(/[^\d.]/g, "") })} placeholder="Price" />
                  <Button type="button" size="icon" variant="ghost" aria-label="Remove" onClick={() => setItems((xs) => xs.filter((_, j) => j !== i))}><XIcon /></Button>
                </div>
              ))}
            </div>}
            <ProductSearch placeholder="Add a product with a fixed price…" onPick={(p) => setItems((xs) => [...xs, { product_id: p.id, product_name: p.name, min_quantity: "1", price: p.selling_price ? String(Number(p.selling_price)) : "" }])} />
          </div>
          <DialogFooter className="items-center sm:justify-between">
            <label className="flex items-center gap-2 text-sm"><Switch checked={active} onCheckedChange={setActive} /> Active</label>
            <div className="flex gap-2"><Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={!name.trim() || save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />}Save</Button></div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------- schemes

function Schemes({ manage }: { manage: boolean }) {
  const list = useBizQuery<Scheme[]>(["schemes"], "/schemes");
  const cats = useBizQuery<{ id: string; name: string }[]>(["categories"], "/categories");
  const catName = new Map((cats.data ?? []).map((c) => [c.id, c.name]));
  const [editing, setEditing] = useState<{ id?: string; draft: SchemeDraft; name?: string; key: string } | null>(null);
  const [deleting, setDeleting] = useState<Scheme | null>(null);
  const remove = useBizMutation("DELETE", () => `/schemes/${deleting?.id}`, { invalidate: [["schemes"]], success: "Scheme deleted" });
  const empty: SchemeDraft = { name: "", kind: "free_goods", product_id: null, category_id: null, min_quantity: "10", free_quantity: "1",
    discount_percent: "0", valid_from: null, valid_to: null, is_active: true };
  const today = new Date().toISOString().slice(0, 10);
  if (list.error) return <Card><ErrorState error={list.error} retry={() => list.refetch()} /></Card>;
  return (
    <div className="space-y-4">
      {manage && <div className="flex justify-end"><Button onClick={() => setEditing({ draft: empty, key: `new-${Date.now()}` })}><PlusIcon /> New scheme</Button></div>}
      {list.isLoading ? <Skeleton className="h-40" /> : !list.data?.length ? (
        <Card><EmptyState icon={GiftIcon} title="No schemes" description="“Buy 10 get 1 free” and “5 % off on 20 or more” apply automatically on every sales bill while they run."
          action={manage && <Button onClick={() => setEditing({ draft: empty, key: `new-${Date.now()}` })}><PlusIcon /> New scheme</Button>} /></Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {list.data.map((s) => {
            const live = s.is_active && (!s.valid_from || s.valid_from <= today) && (!s.valid_to || s.valid_to >= today);
            return (
              <Card key={s.id} className={live ? "" : "opacity-70"}>
                <CardContent className="space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="flex size-8 items-center justify-center rounded-lg bg-primary-soft text-primary">{s.kind === "free_goods" ? <GiftIcon className="size-4" /> : <PercentIcon className="size-4" />}</span>
                      <p className="font-semibold">{s.name}</p></div>
                    <Badge variant={live ? "default" : "secondary"}>{live ? "Running" : s.is_active ? "Not in dates" : "Off"}</Badge>
                  </div>
                  <p className="text-sm">{s.kind === "free_goods" ? `Buy ${qty(s.min_quantity)}, get ${qty(s.free_quantity)} free` : `${Number(s.discount_percent)} % off on ${qty(s.min_quantity)} or more`}
                    <span className="text-muted-foreground"> · {s.product_id ? s.product_name ?? "product" : `category ${catName.get(s.category_id ?? "") ?? ""}`}</span></p>
                  {(s.valid_from || s.valid_to) && <p className="text-xs text-muted-foreground">{s.valid_from ? date(s.valid_from) : "now"} → {s.valid_to ? date(s.valid_to) : "no end"}</p>}
                  {manage && <div className="flex justify-end border-t pt-2">
                    <Button size="icon" variant="ghost" aria-label="Edit scheme" onClick={() => setEditing({ id: s.id, draft: { ...s }, name: s.product_name ?? "", key: `${s.id}-${Date.now()}` })}><PencilIcon /></Button>
                    <Button size="icon" variant="ghost" aria-label="Delete scheme" onClick={() => setDeleting(s)}><Trash2Icon /></Button></div>}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
      {editing && <SchemeDialog key={editing.key} id={editing.id} initial={editing.draft} categories={cats.data ?? []} productName={editing.name ?? ""} onClose={() => setEditing(null)} />}
      <ConfirmDialog open={!!deleting} onOpenChange={(v) => !v && setDeleting(null)} destructive confirm="Delete"
        title={`Delete scheme “${deleting?.name}”?`} description="New bills stop getting it. Bills already made keep their free goods and discounts."
        onConfirm={() => remove.mutateAsync(undefined)} />
    </div>
  );
}

function SchemeDialog({ id, initial, categories, productName, onClose }: {
  id?: string; initial: SchemeDraft; categories: { id: string; name: string }[]; productName: string; onClose: () => void;
}) {
  const [d, setD] = useState<SchemeDraft>({ ...initial, min_quantity: String(Number(initial.min_quantity)), free_quantity: String(Number(initial.free_quantity)),
    discount_percent: String(Number(initial.discount_percent)) });
  const [target, setTarget] = useState<"product" | "category">(initial.category_id ? "category" : "product");
  const [pickedName, setPickedName] = useState(productName);
  const set = (patch: Partial<SchemeDraft>) => setD((x) => ({ ...x, ...patch }));
  const save = useBizMutation<SchemeDraft>(id ? "PUT" : "POST", id ? `/schemes/${id}` : "/schemes",
    { invalidate: [["schemes"]], success: "Scheme saved", onSuccess: onClose });
  const ready = d.name.trim() && Number(d.min_quantity) > 0 && (target === "product" ? d.product_id : d.category_id)
    && (d.kind === "free_goods" ? Number(d.free_quantity) > 0 : Number(d.discount_percent) > 0);
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>{id ? "Edit scheme" : "New scheme"}</DialogTitle></DialogHeader>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ name: d.name, kind: d.kind, min_quantity: d.min_quantity, is_active: d.is_active,
            product_id: target === "product" ? d.product_id : null, category_id: target === "category" ? d.category_id : null,
            free_quantity: d.kind === "free_goods" ? d.free_quantity : "0", discount_percent: d.kind === "discount" ? d.discount_percent : "0",
            valid_from: d.valid_from || null, valid_to: d.valid_to || null });
        }}>
          <Field label="Name" htmlFor="sc-name" className="sm:col-span-2"><Input id="sc-name" value={d.name} maxLength={120} onChange={(e) => set({ name: e.target.value })} placeholder="Diwali 10 + 1" /></Field>
          <Field label="Type" htmlFor="sc-kind"><NativeSelect id="sc-kind" value={d.kind} onChange={(e) => set({ kind: e.target.value as Scheme["kind"] })}>
            <option value="free_goods">Free goods (buy X get Y)</option><option value="discount">Quantity discount %</option></NativeSelect></Field>
          <Field label="Applies to" htmlFor="sc-target"><NativeSelect id="sc-target" value={target} onChange={(e) => setTarget(e.target.value as "product" | "category")}>
            <option value="product">One product</option><option value="category">A whole category</option></NativeSelect></Field>
          {target === "product" ? (
            <Field label="Product" className="sm:col-span-2">
              {d.product_id ? <div className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">{pickedName || "Selected"}
                <Button type="button" size="sm" variant="ghost" onClick={() => set({ product_id: null })}>Change</Button></div>
                : <ProductSearch placeholder="Search the product…" onPick={(p) => { set({ product_id: p.id }); setPickedName(p.name); }} />}
            </Field>
          ) : (
            <Field label="Category" htmlFor="sc-cat" className="sm:col-span-2"><NativeSelect id="sc-cat" value={d.category_id ?? ""} onChange={(e) => set({ category_id: e.target.value || null })}>
              <option value="">Choose…</option>{categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</NativeSelect></Field>
          )}
          <Field label={d.kind === "free_goods" ? "Buy (quantity)" : "Minimum quantity"} htmlFor="sc-min"><Input id="sc-min" inputMode="decimal" value={d.min_quantity} onChange={(e) => set({ min_quantity: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
          {d.kind === "free_goods"
            ? <Field label="Get free" htmlFor="sc-free" hint="Repeats: 10 + 1 gives 2 free on 20"><Input id="sc-free" inputMode="decimal" value={d.free_quantity} onChange={(e) => set({ free_quantity: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
            : <Field label="Discount %" htmlFor="sc-disc"><Input id="sc-disc" inputMode="decimal" value={d.discount_percent} onChange={(e) => set({ discount_percent: e.target.value.replace(/[^\d.]/g, "") })} /></Field>}
          <Field label="From" htmlFor="sc-from" hint="Empty = from today"><Input id="sc-from" type="date" value={d.valid_from ?? ""} onChange={(e) => set({ valid_from: e.target.value || null })} /></Field>
          <Field label="Until" htmlFor="sc-to" hint="Empty = no end"><Input id="sc-to" type="date" value={d.valid_to ?? ""} onChange={(e) => set({ valid_to: e.target.value || null })} /></Field>
          <DialogFooter className="items-center sm:col-span-2 sm:justify-between">
            <label className="flex items-center gap-2 text-sm"><Switch checked={d.is_active} onCheckedChange={(v) => set({ is_active: v })} /> Active</label>
            <div className="flex gap-2"><Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={!ready || save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />}Save</Button></div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
