"use client";

import { FileUpIcon, Loader2Icon, PlusIcon, PrinterIcon } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { StaticTable } from "@/components/data/data-table";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { StatCard } from "@/components/data/stat-card";
import { PartyPicker } from "@/components/pickers/party-picker";
import { ProductSearch } from "@/components/pickers/product-search";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError, openFile } from "@/lib/api";
import { downloadCsv } from "@/lib/csv";
import { date, money, qty, todayISO } from "@/lib/format";
import type { Party, Product } from "@/lib/types";

type Period = { from: string; to: string };

// ---------------------------------------------------------------------------- GSTR-2B

interface RecoRow { status: "matched" | "mismatch" | "only_in_2b" | "only_in_books"; supplier_gstin: string; supplier_name: string | null; number: string;
  date: string | null; portal_taxable: string | null; portal_tax: string | null; books_taxable: string | null; books_tax: string | null; our_number: string | null; document_id: string | null }
const RECO: Record<RecoRow["status"], [string, "default" | "warning" | "destructive" | "secondary", string]> = {
  only_in_books: ["Not in 2B — ITC at risk", "destructive", "You recorded it; the supplier has not filed it. Ask them to file GSTR-1."],
  mismatch: ["Amount differs", "warning", "Check the bill against the supplier's filing."],
  only_in_2b: ["Not in your books", "secondary", "The supplier filed it; record the purchase (or fix its bill number)."],
  matched: ["Matched", "default", ""],
};

export function Gstr2bPanel({ period }: { period: Period }) {
  const { base } = useBusiness();
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ rows: RecoRow[]; summary: Record<string, { count: number; tax: string }>; itc_at_risk: string } | null>(null);
  const [filter, setFilter] = useState("");
  async function upload(f: File) {
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("date_from", period.from); fd.append("date_to", period.to); fd.append("file", f);
      setRes(await api(`${base}/gst/2b-reconcile`, { body: fd }));
    } catch (e) { toast.error((e as ApiError).message); } finally { setBusy(false); if (file.current) file.current.value = ""; }
  }
  const rows = (res?.rows ?? []).filter((r) => !filter || r.status === filter);
  return (
    <div className="space-y-4">
      <Card><CardContent className="flex flex-wrap items-center gap-3">
        <input ref={file} type="file" accept=".json,application/json" className="hidden" aria-label="GSTR-2B file" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        <Button disabled={busy} onClick={() => file.current?.click()}>{busy ? <Loader2Icon className="animate-spin" /> : <FileUpIcon />} Upload GSTR-2B JSON</Button>
        <p className="text-sm text-muted-foreground">GST portal → Returns → GSTR-2B → Download (JSON). Purchase bills of the chosen period are matched by supplier GSTIN and bill number.</p>
      </CardContent></Card>
      {res && <>
        <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
          {(["only_in_books", "mismatch", "only_in_2b", "matched"] as const).map((k) => (
            <button key={k} type="button" className="text-left" onClick={() => setFilter(filter === k ? "" : k)}>
              <StatCard label={RECO[k][0]} icon={FileUpIcon} tone={k === "only_in_books" ? "red" : k === "mismatch" ? "amber" : k === "matched" ? "green" : "blue"}
                value={res.summary[k]?.count ?? 0} sub={res.summary[k] ? `tax ${money(res.summary[k].tax)}` : undefined} />
            </button>))}
        </div>
        <Card className="overflow-hidden">
          {rows.length === 0 ? <EmptyState title="Nothing in this group" /> : (
            <StaticTable rows={rows} rowKey={(r, i) => `${r.supplier_gstin}-${r.number}-${i}`} columns={[
              { key: "s", header: "Supplier", mobile: "title", cell: (r) => <div><p className="font-medium">{r.supplier_name ?? r.supplier_gstin}</p><p className="font-mono text-xs text-muted-foreground">{r.supplier_gstin}</p></div> },
              { key: "n", header: "Bill", cell: (r) => <span>{r.number}{r.date && <span className="block text-xs text-muted-foreground">{date(r.date)}</span>}</span> },
              { key: "st", header: "Status", cell: (r) => <span><Badge variant={RECO[r.status][1]}>{RECO[r.status][0]}</Badge>{RECO[r.status][2] && <span className="block max-w-64 text-xs text-muted-foreground">{RECO[r.status][2]}</span>}</span> },
              { key: "b", header: "Books (taxable / tax)", align: "right", cell: (r) => r.books_taxable ? <span className="num">{money(r.books_taxable)}<span className="block text-xs">{money(r.books_tax!)}</span></span> : "—" },
              { key: "p", header: "2B (taxable / tax)", align: "right", mobile: "aside", cell: (r) => r.portal_taxable ? <span className="num">{money(r.portal_taxable)}<span className="block text-xs">{money(r.portal_tax!)}</span></span> : "—" },
            ]} />)}
        </Card>
        <Button variant="outline" onClick={() => downloadCsv(`gstr2b-reconciliation-${period.from}_${period.to}.csv`, ["Status", "Supplier", "GSTIN", "Bill no", "Date", "Books taxable", "Books tax", "2B taxable", "2B tax"],
          res.rows.map((r) => [RECO[r.status][0], r.supplier_name, r.supplier_gstin, r.number, r.date, r.books_taxable, r.books_tax, r.portal_taxable, r.portal_tax]))}>Download as CSV</Button>
      </>}
    </div>
  );
}

// ---------------------------------------------------------------------------- annual / composition

type Heads = { taxable_value: string; igst: string; cgst: string; sgst: string; cess: string };

export function AnnualPanel() {
  const [rate, setRate] = useState("1");
  const g9 = useBizQuery<Record<string, unknown> & { financial_year: string; note: string }>(["gstr9"], "/gst/gstr9");
  const cmp = useBizQuery<{ quarters: { quarter: string; from: string; to: string; outward_turnover: string; composition_tax: string; rcm_inward: Heads; due_date: string }[]; gstr4: { outward_turnover: string; composition_tax: string; inward_registered: string; inward_unregistered: string; due_date: string }; note: string }>(["cmp08", rate], "/gst/cmp08", { rate });
  const h = (x: unknown) => x as Heads | undefined;
  const line = (label: string, v?: Heads) => v && (
    <div className="grid grid-cols-[1fr_auto_auto] gap-x-4 border-b py-1.5 text-sm last:border-0 sm:grid-cols-[1fr_120px_120px_120px]">
      <span>{label}</span><span className="num text-right">{money(v.taxable_value)}</span>
      <span className="num text-right">{money(String(Number(v.igst) + Number(v.cgst) + Number(v.sgst) + Number(v.cess)))}</span>
      <span className="hidden text-right text-xs text-muted-foreground sm:block">IGST {money(v.igst)}</span>
    </div>);
  return (
    <div className="space-y-4">
      <Card><CardHeader><CardTitle>GSTR-9 annual return — FY {g9.data?.financial_year ?? "…"}</CardTitle></CardHeader>
        <CardContent>
          {g9.error ? <ErrorState error={g9.error} /> : !g9.data ? <TableSkeleton rows={4} /> : <>
            <div className="grid grid-cols-[1fr_auto_auto] gap-x-4 pb-1 text-xs text-muted-foreground uppercase sm:grid-cols-[1fr_120px_120px_120px]"><span>Table</span><span className="text-right">Taxable</span><span className="text-right">Tax</span></div>
            {line("4 — Outward taxable supplies", h(g9.data.table_4_outward_taxable))}
            {line("4 — Zero-rated / exports / SEZ", h(g9.data.table_4_zero_rated))}
            {line("4 — Inward on reverse charge", h(g9.data.table_4_inward_rcm))}
            {line("5 — Nil-rated and exempt", h(g9.data.table_5_nil_exempt))}
            {line("5 — Non-GST", h(g9.data.table_5_non_gst))}
            <p className="mt-3 text-xs text-muted-foreground">{g9.data.note}</p>
          </>}
        </CardContent></Card>
      <Card><CardHeader className="flex-row flex-wrap items-center justify-between gap-2"><CardTitle>Composition: CMP-08 and GSTR-4</CardTitle>
        <label className="flex items-center gap-2 text-sm">Rate <NativeSelect className="w-44" value={rate} onChange={(e) => setRate(e.target.value)}>
          <option value="1">1% — trader / manufacturer</option><option value="5">5% — restaurant</option><option value="6">6% — services</option></NativeSelect></label></CardHeader>
        {!cmp.data ? <TableSkeleton rows={4} /> : <>
          <StaticTable rows={cmp.data.quarters} rowKey={(q) => q.quarter} columns={[
            { key: "q", header: "Quarter", mobile: "title", cell: (q) => <span className="font-medium">{q.quarter} <span className="text-xs font-normal text-muted-foreground">{date(q.from)} – {date(q.to)}</span></span> },
            { key: "t", header: "Turnover", align: "right", cell: (q) => <span className="num">{money(q.outward_turnover)}</span> },
            { key: "r", header: "RCM tax", align: "right", cell: (q) => <span className="num">{money(String(Number(q.rcm_inward.igst) + Number(q.rcm_inward.cgst) + Number(q.rcm_inward.sgst)))}</span> },
            { key: "d", header: "Due", cell: (q) => date(q.due_date) },
            { key: "x", header: "Composition tax", align: "right", mobile: "aside", cell: (q) => <span className="num font-medium">{money(q.composition_tax)}</span> },
          ]} />
          <CardContent className="border-t text-sm">GSTR-4 (due {date(cmp.data.gstr4.due_date)}): turnover <b className="num">{money(cmp.data.gstr4.outward_turnover)}</b>, tax <b className="num">{money(cmp.data.gstr4.composition_tax)}</b>,
            inward from registered <span className="num">{money(cmp.data.gstr4.inward_registered)}</span>, from unregistered <span className="num">{money(cmp.data.gstr4.inward_unregistered)}</span>.
            <p className="mt-1 text-xs text-muted-foreground">Only for businesses registered under composition. {cmp.data.note}</p></CardContent>
        </>}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------- RCM self-invoices

export function RcmPanel() {
  const { base, can, isOwner } = useBusiness();
  const q = useBizQuery<{ document_id: string; number: string; date: string; supplier: string | null; taxable: string; tax: string; self_invoice: { id: string; number: string; date: string } | null }[]>(["rcm-self"], "/gst/rcm-self-invoices");
  const make = useBizMutation<{ document_id: string }>("POST", "/gst/rcm-self-invoices", { invalidate: [["rcm-self"]], success: "Self-invoice created" });
  return (
    <Card className="overflow-hidden">
      <CardContent className="border-b text-sm text-muted-foreground">Buying under reverse charge from an unregistered supplier? You must issue yourself an invoice (section 31(3)(f)). Purchases with “Reverse charge” ticked appear here.</CardContent>
      {q.error ? <ErrorState error={q.error} /> : !q.data ? <TableSkeleton /> : !q.data.length ? <EmptyState title="No reverse-charge purchases from unregistered suppliers" /> : (
        <StaticTable rows={q.data} rowKey={(r) => r.document_id} columns={[
          { key: "s", header: "Supplier", mobile: "title", cell: (r) => <div><p className="font-medium">{r.supplier}</p><p className="text-xs text-muted-foreground">{r.number} · {date(r.date)}</p></div> },
          { key: "t", header: "Taxable", align: "right", cell: (r) => <span className="num">{money(r.taxable)}</span> },
          { key: "x", header: "Tax (RCM)", align: "right", mobile: "aside", cell: (r) => <span className="num font-medium">{money(r.tax)}</span> },
          { key: "a", header: "", mobile: "row", cell: (r) => r.self_invoice
            ? <Button size="sm" variant="outline" onClick={() => openFile(`${base}/gst/rcm-self-invoices/${r.self_invoice!.id}/print`)}><PrinterIcon /> {r.self_invoice.number}</Button>
            : (isOwner || can("gst.file")) && <Button size="sm" disabled={make.isPending} onClick={() => make.mutate({ document_id: r.document_id })}><PlusIcon /> Make self-invoice</Button> },
        ]} />)}
    </Card>
  );
}

// ---------------------------------------------------------------------------- job work

interface JobChallan { id: string; number: string; direction: "out" | "in"; nature: string; party_id: string; challan_date: string; sent_challan_id: string | null; process: string | null; lines: { product_id: string; name: string; quantity: string; value: string }[] }
interface Itc04 { sent: unknown[]; received: unknown[]; pending: { number: string; date: string; job_worker: string; gstin: string | null; nature: string; return_by: string; overdue: boolean; lines: { name: string; pending: string; value: string }[] }[] }

export function JobWorkPanel({ period }: { period: Period }) {
  const { can, isOwner } = useBusiness();
  const list = useBizQuery<JobChallan[]>(["job-work"], "/job-work");
  const r = useBizQuery<Itc04>(["itc04", period.from, period.to], "/gst/itc04", { date_from: period.from, date_to: period.to });
  const [open, setOpen] = useState<null | { direction: "out" | "in"; sent?: JobChallan }>(null);
  const edit = isOwner || can("stock.edit");
  return (
    <div className="space-y-4">
      {edit && <div className="flex flex-wrap gap-2"><Button onClick={() => setOpen({ direction: "out" })}><PlusIcon /> Send to job worker</Button></div>}
      <Card className="overflow-hidden"><CardHeader><CardTitle>Still with job workers</CardTitle></CardHeader>
        {!r.data ? <TableSkeleton /> : !r.data.pending.length ? <EmptyState title="Nothing pending" description="Inputs must come back within 1 year (capital goods 3 years), or GST is due as if sold." /> : (
          <StaticTable rows={r.data.pending} rowKey={(p) => p.number} columns={[
            { key: "w", header: "Job worker", mobile: "title", cell: (p) => <div><p className="font-medium">{p.job_worker}</p><p className="text-xs text-muted-foreground">{p.number} · {date(p.date)}</p></div> },
            { key: "i", header: "Pending", cell: (p) => p.lines.map((l) => `${l.name}: ${qty(l.pending)}`).join(", ") },
            { key: "b", header: "Return by", mobile: "aside", cell: (p) => <span>{date(p.return_by)} {p.overdue && <Badge variant="destructive">Overdue</Badge>}</span> },
            ...(edit ? [{ key: "x", header: "", mobile: "row" as const, cell: (p: Itc04["pending"][number]) => {
              const c = list.data?.find((x) => x.number === p.number);
              return c && <Button size="sm" variant="outline" onClick={() => setOpen({ direction: "in", sent: c })}>Receive back</Button>;
            } }] : []),
          ]} />)}
      </Card>
      <Card className="overflow-hidden"><CardHeader className="flex-row items-center justify-between"><CardTitle>Challans</CardTitle>
        {r.data && <Button size="sm" variant="outline" onClick={() => downloadCsv(`ITC-04-${period.from}_${period.to}.csv`, ["Type", "Challan", "Date", "Job worker", "GSTIN", "Nature", "Item", "Qty", "Taxable value"],
          ([...(r.data!.sent as Itc04["pending"]).map((x) => ["Sent", x] as const), ...(r.data!.received as Itc04["pending"]).map((x) => ["Received", x] as const)])
            .flatMap(([t, x]) => x.lines.map((l) => [t, x.number, x.date, x.job_worker, x.gstin, x.nature, l.name, (l as unknown as { quantity: string }).quantity, l.value])))}>ITC-04 CSV</Button>}</CardHeader>
        {!list.data ? <TableSkeleton /> : !list.data.length ? <EmptyState title="No job-work challans yet" /> : (
          <StaticTable rows={list.data} rowKey={(c) => c.id} columns={[
            { key: "n", header: "Challan", mobile: "title", cell: (c) => <span className="font-medium">{c.number}{c.process && <span className="block text-xs text-muted-foreground">{c.process}</span>}</span> },
            { key: "d", header: "Date", cell: (c) => date(c.challan_date) },
            { key: "t", header: "Type", cell: (c) => <Badge variant={c.direction === "out" ? "warning" : "default"}>{c.direction === "out" ? "Sent" : "Received"}</Badge> },
            { key: "i", header: "Items", mobile: "aside", cell: (c) => c.lines.map((l) => `${l.name} × ${qty(l.quantity)}`).join(", ") },
          ]} />)}
      </Card>
      {open && <JobDialog {...open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function JobDialog({ direction, sent, onClose }: { direction: "out" | "in"; sent?: JobChallan; onClose: () => void }) {
  const warehouses = useBizQuery<{ id: string; code: string; name: string }[]>(["warehouses"], "/warehouses");
  const [party, setParty] = useState<Party | null>(sent ? ({ id: sent.party_id, name: "" } as Party) : null);
  const [f, setF] = useState({ warehouse_id: "", challan_date: todayISO(), nature: "inputs", process: sent?.process ?? "" });
  const [lines, setLines] = useState<{ product_id: string; name: string; quantity: string }[]>(
    sent ? sent.lines.map((l) => ({ product_id: l.product_id, name: l.name, quantity: l.quantity })) : []);
  const save = useBizMutation<Record<string, unknown>>("POST", "/job-work", { invalidate: [["job-work"], ["itc04"]], success: direction === "out" ? "Challan made; stock moved to the job worker" : "Received back into stock", onSuccess: onClose });
  const wh = f.warehouse_id || warehouses.data?.[0]?.id || "";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>{direction === "out" ? "Send goods to a job worker" : `Receive back against ${sent?.number}`}</DialogTitle>
          <DialogDescription>A delivery challan without GST (section 143). Stock moves to / from the job worker&apos;s own godown.</DialogDescription></DialogHeader>
        <div className="grid gap-4 sm:grid-cols-4">
          {!sent && <Field label="Job worker" className="sm:col-span-2"><PartyPicker kind="suppliers" value={party} onChange={setParty} /></Field>}
          <Field label="Our godown" htmlFor="jw-wh"><NativeSelect id="jw-wh" value={wh} onChange={(e) => setF((x) => ({ ...x, warehouse_id: e.target.value }))}>
            {warehouses.data?.filter((w) => !w.code.startsWith("JW-")).map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}</NativeSelect></Field>
          <Field label="Date" htmlFor="jw-date"><Input id="jw-date" type="date" value={f.challan_date} onChange={(e) => setF((x) => ({ ...x, challan_date: e.target.value }))} /></Field>
          {!sent && <Field label="Goods are" htmlFor="jw-nat"><NativeSelect id="jw-nat" value={f.nature} onChange={(e) => setF((x) => ({ ...x, nature: e.target.value }))}>
            <option value="inputs">Inputs (back in 1 year)</option><option value="capital_goods">Capital goods (3 years)</option></NativeSelect></Field>}
          {!sent && <Field label="Process" htmlFor="jw-proc" className="sm:col-span-3"><Input id="jw-proc" value={f.process} onChange={(e) => setF((x) => ({ ...x, process: e.target.value }))} placeholder="Grinding, packing, labelling…" /></Field>}
        </div>
        {!sent && <ProductSearch onPick={(p: Product) => setLines((ls) => ls.some((l) => l.product_id === p.id) ? ls : [...ls, { product_id: p.id, name: p.name, quantity: "1" }])} placeholder="Add product" />}
        {lines.length > 0 && <ul className="divide-y rounded-lg border">{lines.map((l, i) => (
          <li key={l.product_id} className="flex items-center gap-2 px-3 py-2 text-sm"><span className="flex-1 truncate">{l.name}</span>
            <Input value={l.quantity} inputMode="decimal" className="num h-8 w-24 text-right" aria-label={`Quantity of ${l.name}`}
              onChange={(e) => setLines((ls) => ls.map((x, j) => (j === i ? { ...x, quantity: e.target.value.replace(/[^\d.]/g, "") } : x)))} /></li>))}</ul>}
        <DialogFooter><Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!party || !wh || !lines.length || save.isPending} onClick={() => save.mutate({ direction, party_id: party!.id, warehouse_id: wh, challan_date: f.challan_date,
            ...(sent ? { sent_challan_id: sent.id } : { nature: f.nature, process: f.process || null }), lines: lines.filter((l) => Number(l.quantity) > 0).map((l) => ({ product_id: l.product_id, quantity: l.quantity })) })}>
            {save.isPending && <Loader2Icon className="animate-spin" />} {direction === "out" ? "Make challan" : "Receive"}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
