"use client";

import { CheckCircle2Icon, FileUpIcon, Loader2Icon, SparklesIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/data/page-header";
import { PartyPicker } from "@/components/pickers/party-picker";
import { ProductSearch } from "@/components/pickers/product-search";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";
import { date, money, qty } from "@/lib/format";
import type { Party } from "@/lib/types";

interface Row {
  row: number; supplier_text: string; supplier_key: string; name: string; code: string | null; quantity: string | null; free: string;
  rate: string | null; discount_percent: string; gst_rate: string | null; batch_no: string | null; expiry_date: string | null;
  mrp: string | null; product_id: string | null; product_name: string | null; match: string | null;
  suggestions: { id: string; name: string; sku: string }[]; problems: string[]; skip?: boolean;
}
const MATCH: Record<string, string> = { remembered: "Remembered", barcode: "Barcode", code: "Item code", name: "Same name" };

export default function ImportBillPage() {
  const router = useRouter();
  const { base } = useBusiness();
  const [party, setParty] = useState<Party | null>(null);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [cols, setCols] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [head, setHead] = useState({ supplier_invoice_no: "", supplier_invoice_date: "" });
  const [picking, setPicking] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function read(file: File) {
    if (!party) return toast.error("Choose the supplier first");
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("party_id", party.id);
      fd.append("file", file);
      const out = await api<{ rows: Row[]; columns: Record<string, string> }>(`${base}/purchases/import/preview`, { body: fd });
      setRows(out.rows); setCols(out.columns);
    } catch (e) { toast.error((e as ApiError).message); } finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }
  const set = (i: number, patch: Partial<Row>) => setRows((rs) => rs!.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const used = (rows ?? []).filter((r) => !r.skip);
  const ready = used.length > 0 && used.every((r) => r.product_id && Number(r.quantity) > 0 && r.rate !== null);
  const total = used.reduce((s, r) => s + Number(r.quantity || 0) * Number(r.rate || 0) * (1 - Number(r.discount_percent || 0) / 100), 0);

  async function create() {
    setBusy(true);
    try {
      const doc = await api<{ id: string }>(`${base}/purchases/import`, { body: {
        party_id: party!.id, supplier_invoice_no: head.supplier_invoice_no || undefined, supplier_invoice_date: head.supplier_invoice_date || undefined,
        rows: used.map((r) => ({ product_id: r.product_id, quantity: r.quantity, free: r.free || "0", rate: r.rate, discount_percent: r.discount_percent || "0",
          gst_rate: r.gst_rate ?? undefined, batch_no: r.batch_no ?? undefined, expiry_date: r.expiry_date ?? undefined, mrp: r.mrp ?? undefined,
          supplier_text: r.supplier_text, supplier_key: r.supplier_key })),
      } });
      toast.success("Draft purchase bill created — check it and finalize");
      router.push(`/purchases/new?draft=${doc.id}`);
    } catch (e) { toast.error((e as ApiError).message); setBusy(false); }
  }

  return (
    <>
      <PageHeader title="Import supplier bill" description="Upload the bill your distributor sends as Excel or CSV. Items are matched to your products; anything new you match once is remembered for this supplier." />
      <Card className="mb-4">
        <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Supplier" className="sm:col-span-2"><PartyPicker kind="suppliers" value={party} onChange={(p) => { setParty(p); setRows(null); }} placeholder="Choose supplier" /></Field>
          <Field label="Their bill no." htmlFor="im-no"><Input id="im-no" value={head.supplier_invoice_no} onChange={(e) => setHead((h) => ({ ...h, supplier_invoice_no: e.target.value }))} /></Field>
          <Field label="Bill date" htmlFor="im-date"><Input id="im-date" type="date" value={head.supplier_invoice_date} onChange={(e) => setHead((h) => ({ ...h, supplier_invoice_date: e.target.value }))} /></Field>
          <div className="sm:col-span-2 lg:col-span-4">
            <input ref={fileRef} type="file" accept=".csv,.xlsx,.xlsm" className="hidden" aria-label="Bill file" onChange={(e) => e.target.files?.[0] && read(e.target.files[0])} />
            <Button type="button" variant={rows ? "outline" : "default"} disabled={!party || busy} onClick={() => fileRef.current?.click()}>
              {busy && !rows ? <Loader2Icon className="animate-spin" /> : <FileUpIcon />} {rows ? "Choose another file" : "Choose the bill file (Excel / CSV)"}</Button>
            <p className="mt-2 text-xs text-muted-foreground">Columns are recognised by their usual names: Item name / Product, Item code, Barcode, HSN, Qty, Free, Rate / PTR, Disc %, GST %, Batch, Expiry, MRP.</p>
          </div>
        </CardContent>
      </Card>

      {rows && (
        <Card className="overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
            <p className="text-sm"><b>{rows.filter((r) => r.product_id).length}</b> of {rows.length} lines matched
              {Object.keys(cols).length > 0 && <span className="text-muted-foreground"> · read columns: {Object.entries(cols).map(([k, v]) => `${v} → ${k}`).join(", ")}</span>}</p>
            <p className="num text-sm">≈ {money(total.toFixed(2))} before tax</p>
          </div>
          <ul className="divide-y">
            {rows.map((r, i) => (
              <li key={r.row} className={`grid gap-2 px-4 py-3 text-sm md:grid-cols-[1.4fr_1.4fr_auto] md:items-center ${r.skip ? "opacity-50" : ""}`}>
                <div className="min-w-0">
                  <p className="truncate font-medium">{r.supplier_text}</p>
                  <p className="text-xs text-muted-foreground">
                    {qty(r.quantity ?? "0")}{Number(r.free) > 0 && ` + ${qty(r.free)} free`} × {r.rate ? money(r.rate) : "—"}
                    {Number(r.discount_percent) > 0 && ` · ${Number(r.discount_percent)}% off`}{r.gst_rate && ` · GST ${Number(r.gst_rate)}%`}
                    {r.batch_no && ` · Batch ${r.batch_no}`}{r.expiry_date && ` · Exp ${date(r.expiry_date)}`}{r.mrp && ` · MRP ${money(r.mrp)}`}
                  </p>
                  {r.problems.length > 0 && <p className="text-xs text-destructive">{r.problems.join("; ")}</p>}
                </div>
                <div className="min-w-0">
                  {r.product_id ? (
                    <p className="flex flex-wrap items-center gap-2"><CheckCircle2Icon className="size-4 text-primary" /><span className="truncate">{r.product_name}</span>
                      {r.match && <Badge variant="secondary">{MATCH[r.match] ?? r.match}</Badge>}
                      <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={() => setPicking(i)}>change</Button></p>
                  ) : picking === i || !r.suggestions.length ? (
                    <ProductSearch placeholder="Find your product for this line" onPick={(p) => { set(i, { product_id: p.id, product_name: p.name, match: null }); setPicking(null); }} />
                  ) : (
                    <div className="flex gap-2">
                      <NativeSelect value="" onChange={(e) => { const s = r.suggestions.find((x) => x.id === e.target.value); if (s) set(i, { product_id: s.id, product_name: s.name }); else setPicking(i); }} aria-label={`Product for ${r.supplier_text}`}>
                        <option value="">Choose product…</option>
                        {r.suggestions.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.sku})</option>)}
                        <option value="__search">Search all products…</option>
                      </NativeSelect>
                    </div>
                  )}
                  {picking === i && r.product_id && <ProductSearch placeholder="Find another product" onPick={(p) => { set(i, { product_id: p.id, product_name: p.name, match: null }); setPicking(null); }} />}
                </div>
                <label className="flex items-center gap-2 text-xs text-muted-foreground md:justify-end"><input type="checkbox" className="size-4" checked={!!r.skip} onChange={(e) => set(i, { skip: e.target.checked })} /> Skip</label>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3">
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><SparklesIcon className="size-3.5" /> Products you choose are remembered for {party?.name}’s future bills.</p>
            <Button disabled={!ready || busy} onClick={create}>{busy ? <Loader2Icon className="animate-spin" /> : <CheckCircle2Icon />} Create draft bill ({used.length} lines)</Button>
          </div>
        </Card>
      )}
    </>
  );
}
