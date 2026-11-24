"use client";

import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftIcon, EyeIcon, FileDownIcon, GripVerticalIcon, ImageUpIcon, Loader2Icon, PlusIcon, SaveIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError, openFile, type Page } from "@/lib/api";
import type { DocumentSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

const ALL_COLUMNS: Record<string, string> = {
  sno: "#", description: "Item", hsn: "HSN/SAC", batch: "Batch", expiry: "Expiry", qty: "Qty", unit: "Unit", mrp: "MRP",
  rate: "Rate", discount: "Discount", taxable: "Taxable", gst_rate: "GST %", tax: "Tax", amount: "Amount",
};
interface Config {
  paper: string; margin_mm: number; font_family: string; font_size_pt: number; primary_color: string; text_color: string; background_color: string;
  title: string | null; header_text: string | null; footer_text: string | null; terms_text: string | null; bank_details: string | null;
  signature_label: string; watermark_text: string | null; columns: string[]; show_logo: boolean; show_tax_summary: boolean;
  show_amount_in_words: boolean; show_upi_qr: boolean; upi_id: string | null; upi_name: string | null; show_signature: boolean;
  logo_data_url: string | null; signature_data_url: string | null; [k: string]: unknown;
}
interface Template { id: string; name: string; doc_types: string[]; config: Config; is_default: boolean; is_active: boolean }
const DEFAULT: Config = {
  paper: "a4", margin_mm: 10, font_family: "Helvetica, Arial, sans-serif", font_size_pt: 9.5, primary_color: "#1f6f4a", text_color: "#111111",
  background_color: "#ffffff", title: null, header_text: null, footer_text: "Thank you for your business!", terms_text: null, bank_details: null,
  signature_label: "Authorised Signatory", watermark_text: null, columns: ["sno", "description", "hsn", "qty", "rate", "discount", "taxable", "gst_rate", "amount"],
  show_logo: true, show_tax_summary: true, show_amount_in_words: true, show_upi_qr: false, upi_id: null, upi_name: null, show_signature: true,
  logo_data_url: null, signature_data_url: null,
};
const FONTS = ["Helvetica, Arial, sans-serif", "Georgia, serif", "Verdana, sans-serif", "Courier New, monospace"];

function SortableColumn({ id, onRemove }: { id: string; onRemove: () => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  return (
    <li ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn("flex items-center gap-2 rounded-md border bg-card px-2 py-1.5 text-sm", isDragging && "z-10 shadow-lg ring-2 ring-ring/30")}>
      <button type="button" className="cursor-grab touch-none text-muted-foreground active:cursor-grabbing" aria-label={`Move ${ALL_COLUMNS[id]}`} {...attributes} {...listeners}><GripVerticalIcon className="size-4" /></button>
      <span className="flex-1">{ALL_COLUMNS[id] ?? id}</span>
      <button type="button" onClick={onRemove} className="text-muted-foreground hover:text-destructive" aria-label={`Remove ${ALL_COLUMNS[id]}`}><XIcon className="size-3.5" /></button>
    </li>
  );
}

function readImage(file: File, maxKb = 300): Promise<string> {
  return new Promise((resolve, reject) => {
    if (!/^image\/(png|jpeg|svg\+xml|webp)$/.test(file.type)) return reject(new Error("Use a PNG, JPEG, WebP or SVG image"));
    if (file.size > maxKb * 1024) return reject(new Error(`Keep images under ${maxKb} KB`));
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new Error("Could not read the file"));
    r.readAsDataURL(file);
  });
}

export default function TemplateDesignerPage() {
  const templates = useBizQuery<Template[]>(["templates"], "/invoice-templates");
  if (templates.isLoading) return <Skeleton className="h-[70vh]" />;
  if (templates.error) return <Card><ErrorState error={templates.error} /></Card>;
  return <Designer templates={templates.data ?? []} />;
}

function Designer({ templates }: { templates: Template[] }) {
  const { base, id, can, isOwner } = useBusiness();
  const qc = useQueryClient();
  const initial = templates.find((x) => x.is_default) ?? templates[0];
  const sample = useBizQuery<Page<DocumentSummary>>(["templates-sample"], "/documents", { doc_type: "sales_invoice", status_filter: "final", limit: 1 });
  const [selected, setSelected] = useState<string>(initial?.id ?? "new");
  const [name, setName] = useState(initial?.name ?? "My invoice");
  const [cfg, setCfg] = useState<Config>({ ...DEFAULT, ...initial?.config });
  const [isDefault, setIsDefault] = useState(initial ? initial.is_default : true);
  const sensors = useSensors(useSensor(PointerSensor), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));
  const manage = isOwner || can("templates.manage");

  const set = <K extends keyof Config>(k: K, v: Config[K]) => setCfg((c) => ({ ...c, [k]: v }));
  const nn = (v: string) => (v.trim() ? v : null);

  const [version, setVersion] = useState(0);
  const save = useMutation({
    mutationFn: () => selected !== "new"
      ? api<Template>(`${base}/invoice-templates/${selected}`, { method: "PATCH", body: { name, config: cfg, is_default: isDefault } })
      : api<Template>(`${base}/invoice-templates`, { body: { name, config: cfg, is_default: isDefault, doc_types: [] } }),
    onSuccess: (t) => { setSelected(t.id); setVersion((n) => n + 1); qc.invalidateQueries({ queryKey: ["biz", id, "templates"] }); toast.success("Template saved"); },
    onError: (e: ApiError) => toast.error(e.message, { description: Object.entries(e.fieldErrors).map(([k, v]) => `${k}: ${v}`)[0] }),
  });
  const docId = sample.data?.items[0]?.id;
  // The saved template rendered on a real invoice by the same engine that prints and makes PDFs.
  const previewQ = useQuery({
    // Before the first save, show the built-in layout so there is always something to look at.
    queryKey: ["biz", id, "tpl-preview", selected, docId, version], enabled: !!docId,
    queryFn: async () => (await api<Response>(`${base}/documents/${docId}/print`, {
      raw: true, query: { format: "html", template_id: selected === "new" ? undefined : selected } })).text(),
  });
  const preview = previewQ.data ?? null;

  function onDragEnd(e: DragEndEvent) {
    if (!e.over || e.active.id === e.over.id) return;
    set("columns", arrayMove(cfg.columns, cfg.columns.indexOf(String(e.active.id)), cfg.columns.indexOf(String(e.over.id))));
  }
  async function upload(key: "logo_data_url" | "signature_data_url", f?: File) {
    if (!f) return;
    try { set(key, await readImage(f)); } catch (e) { toast.error((e as Error).message); }
  }
  const unused = Object.keys(ALL_COLUMNS).filter((c) => !cfg.columns.includes(c));

  return (
    <>
      <Link href="/settings" className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeftIcon className="size-4" /> Settings</Link>
      <PageHeader title="Invoice designer" description="Changes apply to new prints; saved documents keep their numbers and values."
        actions={<>
          <NativeSelect className="w-52" value={selected ?? ""} onChange={(e) => {
            const v = e.target.value; setSelected(v);
            const t = templates.find((x) => x.id === v);
            if (t) { setName(t.name); setCfg({ ...DEFAULT, ...t.config }); setIsDefault(t.is_default); } else { setName("New template"); setCfg(DEFAULT); setIsDefault(false); }
          }} aria-label="Template">
            {templates.map((t) => <option key={t.id} value={t.id}>{t.name}{t.is_default ? " (default)" : ""}</option>)}
            <option value="new">+ New template</option>
          </NativeSelect>
          {manage && <Button onClick={() => save.mutate()} disabled={save.isPending}>{save.isPending ? <Loader2Icon className="animate-spin" /> : <SaveIcon />} Save & preview</Button>}
        </>} />
      <div className="grid gap-5 xl:grid-cols-[380px_1fr]">
        <fieldset disabled={!manage} className="space-y-4">
          <Card><CardHeader><CardTitle>Layout</CardTitle></CardHeader><CardContent className="grid grid-cols-2 gap-3">
            <Field label="Template name" className="col-span-2"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Paper"><NativeSelect value={cfg.paper} onChange={(e) => set("paper", e.target.value)}>
              <option value="a4">A4</option><option value="a5">A5</option><option value="a6">A6</option><option value="thermal_80">Thermal 80 mm</option><option value="thermal_58">Thermal 58 mm</option></NativeSelect></Field>
            <Field label="Margin (mm)"><Input type="number" min={0} max={40} value={cfg.margin_mm} onChange={(e) => set("margin_mm", Number(e.target.value))} /></Field>
            <Field label="Font"><NativeSelect value={cfg.font_family} onChange={(e) => set("font_family", e.target.value)}>{FONTS.map((f) => <option key={f} value={f}>{f.split(",")[0]}</option>)}</NativeSelect></Field>
            <Field label="Font size (pt)"><Input type="number" step={0.5} min={6} max={16} value={cfg.font_size_pt} onChange={(e) => set("font_size_pt", Number(e.target.value))} /></Field>
            <label className="col-span-2 flex items-center justify-between text-sm">Default for invoices <Switch checked={isDefault} onCheckedChange={setIsDefault} /></label>
          </CardContent></Card>

          <Card><CardHeader><CardTitle>Branding</CardTitle></CardHeader><CardContent className="space-y-3">
            <div className="grid grid-cols-3 gap-3">
              {([["primary_color", "Accent"], ["text_color", "Text"], ["background_color", "Paper"]] as const).map(([k, l]) => (
                <Field key={k} label={l}><div className="flex items-center gap-2"><input type="color" value={cfg[k]} onChange={(e) => set(k, e.target.value)} className="h-9 w-10 cursor-pointer rounded border bg-transparent" aria-label={l} />
                  <span className="font-mono text-xs text-muted-foreground">{cfg[k]}</span></div></Field>
              ))}
            </div>
            {([["logo_data_url", "Logo", "show_logo"], ["signature_data_url", "Signature", "show_signature"]] as const).map(([k, label, toggle]) => (
              <div key={k} className="flex items-center gap-3 rounded-lg border p-2">
                <div className="flex size-14 items-center justify-center overflow-hidden rounded bg-muted">{/* eslint-disable-next-line @next/next/no-img-element -- local data-URL preview */}
                  {cfg[k] ? <img src={cfg[k]!} alt={label} className="max-h-full max-w-full object-contain" /> : <ImageUpIcon className="size-5 text-muted-foreground" />}</div>
                <div className="flex-1 space-y-1"><p className="text-sm font-medium">{label}</p>
                  <div className="flex gap-2"><label className="cursor-pointer text-xs text-blue hover:underline">Upload<input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" className="sr-only" onChange={(e) => upload(k, e.target.files?.[0])} /></label>
                    {cfg[k] && <button type="button" className="text-xs text-destructive hover:underline" onClick={() => set(k, null)}>Remove</button>}</div></div>
                <Switch checked={cfg[toggle]} onCheckedChange={(v) => set(toggle, v)} aria-label={`Show ${label}`} />
              </div>
            ))}
            <Field label="Title" hint="Default: Tax Invoice / Bill of Supply"><Input value={cfg.title ?? ""} onChange={(e) => set("title", nn(e.target.value))} /></Field>
            <Field label="Watermark"><Input value={cfg.watermark_text ?? ""} onChange={(e) => set("watermark_text", nn(e.target.value))} placeholder="e.g. PAID" /></Field>
          </CardContent></Card>

          <Card><CardHeader><CardTitle>Columns</CardTitle><p className="text-xs text-muted-foreground">Drag to reorder (or focus the handle and use the arrow keys).</p></CardHeader><CardContent className="space-y-3">
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
              <SortableContext items={cfg.columns} strategy={verticalListSortingStrategy}>
                <ul className="space-y-1.5">{cfg.columns.map((c) => <SortableColumn key={c} id={c} onRemove={() => set("columns", cfg.columns.filter((x) => x !== c))} />)}</ul>
              </SortableContext>
            </DndContext>
            {unused.length > 0 && <div className="flex flex-wrap gap-1.5">{unused.map((c) => (
              <button key={c} type="button" onClick={() => set("columns", [...cfg.columns, c])} className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs hover:bg-accent"><PlusIcon className="size-3" />{ALL_COLUMNS[c]}</button>))}</div>}
          </CardContent></Card>

          <Card><CardHeader><CardTitle>Content</CardTitle></CardHeader><CardContent className="space-y-3">
            {([["show_tax_summary", "GST summary table"], ["show_amount_in_words", "Amount in words"], ["show_upi_qr", "UPI QR code for payment"]] as const).map(([k, l]) => (
              <label key={k} className="flex items-center justify-between text-sm">{l}<Switch checked={cfg[k]} onCheckedChange={(v) => set(k, v)} /></label>
            ))}
            {cfg.show_upi_qr && <div className="grid grid-cols-2 gap-3"><Field label="UPI ID"><Input value={cfg.upi_id ?? ""} onChange={(e) => set("upi_id", nn(e.target.value))} placeholder="shop@okbank" /></Field>
              <Field label="Payee name"><Input value={cfg.upi_name ?? ""} onChange={(e) => set("upi_name", nn(e.target.value))} /></Field></div>}
            <Field label="Header note"><Textarea className="min-h-12" value={cfg.header_text ?? ""} onChange={(e) => set("header_text", nn(e.target.value))} /></Field>
            <Field label="Bank details"><Textarea className="min-h-12" value={cfg.bank_details ?? ""} onChange={(e) => set("bank_details", nn(e.target.value))} placeholder="A/c 1234… · IFSC …" /></Field>
            <Field label="Terms & conditions"><Textarea value={cfg.terms_text ?? ""} onChange={(e) => set("terms_text", nn(e.target.value))} /></Field>
            <Field label="Footer"><Input value={cfg.footer_text ?? ""} onChange={(e) => set("footer_text", nn(e.target.value))} /></Field>
            <Field label="Signature label"><Input value={cfg.signature_label} onChange={(e) => set("signature_label", e.target.value)} /></Field>
          </CardContent></Card>
        </fieldset>

        <Card className="flex min-h-[70vh] flex-col overflow-hidden xl:sticky xl:top-20 xl:h-[calc(100vh-7rem)]">
          <div className="flex items-center justify-between border-b px-4 py-2.5">
            <p className="flex items-center gap-2 text-sm font-medium"><EyeIcon className="size-4" /> Live preview {docId && <Badge variant="secondary">{selected === "new" ? "built-in layout — save to apply yours" : "your latest invoice"}</Badge>}</p>
            {docId && selected !== "new" && <Button size="sm" variant="outline" onClick={() => openFile(`${base}/documents/${docId}/print`, { format: "pdf", template_id: selected }, "preview.pdf")}><FileDownIcon /> PDF</Button>}
          </div>
          <div className="flex-1 bg-muted/50 p-4">
            {!docId ? <EmptyState title="No invoice to preview yet" description="Finalize one sales invoice, then come back to see your design on it." />
              : preview ? <iframe title="Template preview" srcDoc={preview} sandbox="allow-same-origin" className={cn("mx-auto h-full rounded-md bg-white shadow-md", cfg.paper.startsWith("thermal") ? "w-[340px]" : "w-full max-w-[860px]")} />
              : <EmptyState title="Save to preview" description="Your design is rendered by the same engine that prints and makes PDFs." />}
          </div>
        </Card>
      </div>
    </>
  );
}
