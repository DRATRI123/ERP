"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon } from "lucide-react";
import { useEffect } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { useModules } from "@/hooks/use-modules";
import { api, ApiError } from "@/lib/api";
import type { Product } from "@/lib/types";

const money = z.string().regex(/^\d*(\.\d{0,4})?$/, "Enter an amount").optional();
const schema = z.object({
  name: z.string().trim().min(2, "Required"),
  sku: z.string().trim().min(1, "Required").max(40),
  unit_id: z.string().min(1, "Choose a unit"),
  kind: z.enum(["goods", "service"]),
  hsn_sac: z.string().regex(/^(\d{4}|\d{6}|\d{8})?$/, "4, 6 or 8 digits").optional(),
  barcode: z.string().max(64).optional(),
  category_id: z.string().optional(),
  gst_rate: z.string(),
  selling_price: money, purchase_price: money, mrp: money,
  reorder_level: z.string().regex(/^\d*(\.\d{0,3})?$/).optional(),
  ptr: money, pts: money,
  composition: z.string().max(300).optional(),
  rack: z.string().max(30).optional(),
  drug_schedule: z.enum(["none", "g", "h", "h1", "x", "narcotic"]),
  pack_size: z.string().regex(/^\d*(\.\d{0,3})?$/, "Enter a number").optional(),
  pack_label: z.string().max(16).optional(),
  prices_include_tax: z.boolean(),
  track_inventory: z.boolean(), batch_tracking: z.boolean(), serial_tracking: z.boolean(),
});
type Values = z.infer<typeof schema>;
const GST = ["0", "0.25", "3", "5", "12", "18", "28"];
export const SCHEDULES = [
  ["none", "None (OTC)"], ["g", "Schedule G"], ["h", "Schedule H — prescription"], ["h1", "Schedule H1 — Rx + register"],
  ["x", "Schedule X — Rx + register"], ["narcotic", "Narcotic (NDPS) — Rx + register"],
] as const;

export function ProductDialog({ open, onOpenChange, product }: { open: boolean; onOpenChange: (v: boolean) => void; product?: Product }) {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const pharmacy = useModules().on("pharmacy");
  const units = useBizQuery<{ id: string; code: string; name: string }[]>(["units"], "/units", undefined, { enabled: open });
  const cats = useBizQuery<{ id: string; name: string }[]>(["categories"], "/categories", undefined, { enabled: open });
  const n = (v: unknown) => (v === null || v === undefined ? "" : String(Number(v)));
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    values: product ? {
      name: product.name, sku: product.sku, unit_id: product.unit_id, kind: product.kind as "goods", hsn_sac: product.hsn_sac ?? "",
      barcode: product.barcode ?? "", category_id: product.category_id ?? "", gst_rate: n(product.gst_rate),
      selling_price: n(product.selling_price), purchase_price: n(product.purchase_price), mrp: n(product.mrp),
      reorder_level: n(product.reorder_level), prices_include_tax: product.prices_include_tax,
      track_inventory: product.track_inventory, batch_tracking: product.batch_tracking, serial_tracking: product.serial_tracking,
      ptr: n(product.ptr), pts: n(product.pts), composition: (product.composition as string | null) ?? "", rack: (product.rack as string | null) ?? "",
      drug_schedule: ((product.drug_schedule as string) ?? "none") as Values["drug_schedule"], pack_size: n(product.pack_size), pack_label: (product.pack_label as string | null) ?? "",
    } : undefined,
    defaultValues: { name: "", sku: "", unit_id: "", kind: "goods", gst_rate: "18", prices_include_tax: false, track_inventory: true, batch_tracking: false, serial_tracking: false, drug_schedule: "none" },
  });
  const save = useMutation({
    mutationFn: (v: Values) => {
      const body = Object.fromEntries(Object.entries(v).map(([k, x]) => [k, x === "" ? null : x]));
      return product ? api<Product>(`${base}/products/${product.id}`, { method: "PATCH", body }) : api<Product>(`${base}/products`, { body });
    },
    onSuccess: (p) => { toast.success(`${p.name} saved`); qc.invalidateQueries({ queryKey: ["biz", id] }); onOpenChange(false); if (!product) form.reset(); },
    onError: (e: ApiError) => {
      for (const [k, msg] of Object.entries(e.fieldErrors)) form.setError(k as keyof Values, { message: msg });
      toast.error(e.message);
    },
  });
  const err = form.formState.errors;
  const FLAGS = ["prices_include_tax", "track_inventory", "batch_tracking", "serial_tracking"] as const;
  const flags = useWatch({ control: form.control, name: [...FLAGS] });
  const [packSize, packLabel, sp] = useWatch({ control: form.control, name: ["pack_size", "pack_label", "selling_price"] });
  const perPack = Number(packSize) > 1 && Number(sp) > 0 ? (Number(sp) * Number(packSize)).toFixed(2) : null;
  useEffect(() => {
    if (!product && !form.getValues("unit_id") && units.data?.[0]) form.setValue("unit_id", units.data.find((u) => u.code === "NOS")?.id ?? units.data[0].id);
  }, [units.data, product, form]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>{product ? "Edit product" : "New product"}</DialogTitle></DialogHeader>
        <form onSubmit={form.handleSubmit((v) => save.mutate(v))} className="grid gap-4 sm:grid-cols-6" noValidate>
          <Field label="Name" required error={err.name?.message} className="sm:col-span-4"><Input autoFocus {...form.register("name")} /></Field>
          <Field label="SKU / code" required error={err.sku?.message} className="sm:col-span-2"><Input {...form.register("sku")} /></Field>
          <Field label="Type" className="sm:col-span-2"><NativeSelect {...form.register("kind")}><option value="goods">Goods</option><option value="service">Service</option></NativeSelect></Field>
          <Field label="Unit" error={err.unit_id?.message} className="sm:col-span-2">
            <NativeSelect {...form.register("unit_id")}>{units.data?.map((u) => <option key={u.id} value={u.id}>{u.code} — {u.name}</option>)}</NativeSelect>
          </Field>
          <Field label="Category" className="sm:col-span-2">
            <NativeSelect {...form.register("category_id")}><option value="">—</option>{cats.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</NativeSelect>
          </Field>
          <Field label="HSN / SAC" error={err.hsn_sac?.message} className="sm:col-span-2"><Input inputMode="numeric" {...form.register("hsn_sac")} /></Field>
          <Field label="GST rate" className="sm:col-span-2"><NativeSelect {...form.register("gst_rate")}>{GST.map((g) => <option key={g} value={g}>{g}%</option>)}</NativeSelect></Field>
          <Field label="Barcode" className="sm:col-span-2" hint="Scan it here"><Input {...form.register("barcode")} /></Field>
          <Field label="Selling price" error={err.selling_price?.message} className="sm:col-span-2"><Input inputMode="decimal" {...form.register("selling_price")} /></Field>
          <Field label="Purchase price" error={err.purchase_price?.message} className="sm:col-span-2"><Input inputMode="decimal" {...form.register("purchase_price")} /></Field>
          <Field label="MRP" error={err.mrp?.message} className="sm:col-span-2"><Input inputMode="decimal" {...form.register("mrp")} /></Field>
          <Field label="Reorder level" hint="Alert when stock falls to this" className="sm:col-span-2"><Input inputMode="decimal" {...form.register("reorder_level")} /></Field>
          {pharmacy && <fieldset className="grid gap-4 rounded-lg border p-3 sm:col-span-6 sm:grid-cols-6">
            <legend className="px-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Pharmacy (optional)</legend>
            <Field label="Salt / composition" hint="Finds substitutes with the same salt" className="sm:col-span-4"><Input placeholder="Paracetamol 500 mg" {...form.register("composition")} /></Field>
            <Field label="Rack / shelf" className="sm:col-span-2"><Input placeholder="A-3" {...form.register("rack")} /></Field>
            <Field label="Drug schedule" className="sm:col-span-2"><NativeSelect {...form.register("drug_schedule")}>{SCHEDULES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</NativeSelect></Field>
            <Field label="PTR" hint="Price to retailer" error={err.ptr?.message} className="sm:col-span-2"><Input inputMode="decimal" {...form.register("ptr")} /></Field>
            <Field label="PTS" hint="Price to stockist" error={err.pts?.message} className="sm:col-span-2"><Input inputMode="decimal" {...form.register("pts")} /></Field>
          </fieldset>}
          <fieldset className="grid gap-4 rounded-lg border p-3 sm:col-span-6 sm:grid-cols-6">
            <legend className="px-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Bigger unit (optional)</legend>
            <Field label="Units in one pack" error={err.pack_size?.message} hint={pharmacy ? "e.g. 10 tablets in a strip" : "e.g. 12 pieces in a box, 25 kg in a bag"} className="sm:col-span-2"><Input inputMode="decimal" placeholder={pharmacy ? "10" : "12"} {...form.register("pack_size")} /></Field>
            <Field label="Pack name" className="sm:col-span-2"><Input placeholder={pharmacy ? "strip" : "box"} {...form.register("pack_label")} /></Field>
            <p className="self-end pb-2 text-xs text-muted-foreground sm:col-span-2">
              {perPack ? `One ${packLabel || "pack"} = ${Number(packSize)} units = ₹${perPack}. Bill in packs or loose; stock and prices stay per unit.` : "Stock and prices are per base unit. Add a pack to bill by box, case, strip or bag too."}
            </p>
          </fieldset>
          <div className="grid gap-3 rounded-lg border p-3 sm:col-span-4 sm:grid-cols-2">
            {([["prices_include_tax", "Prices include GST"], ["track_inventory", "Track stock"], ["batch_tracking", "Batches & expiry"], ["serial_tracking", "Serial numbers"]] as const).map(([k, label]) => (
              <label key={k} className="flex items-center justify-between gap-2 text-sm">{label}
                <Switch checked={!!flags[FLAGS.indexOf(k)]} onCheckedChange={(v) => form.setValue(k, v, { shouldDirty: true })} disabled={!!product && (k === "batch_tracking" || k === "serial_tracking")} />
              </label>
            ))}
          </div>
          <DialogFooter className="sm:col-span-6">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />} Save product</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
