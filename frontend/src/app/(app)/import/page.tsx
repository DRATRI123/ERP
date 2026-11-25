"use client";

import { CheckCircle2Icon, FileSpreadsheetIcon, FileUpIcon, Loader2Icon, XCircleIcon } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/data/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";

interface SheetResult { valid_rows: number; imported: number; errors: { row: number; error: string }[] }
interface TallyResult { customers: number; suppliers: number; products: number; skipped: number; opening_balances: number; opening_stock_lines: number; errors: string[]; dry_run: boolean }

export default function ImportPage() {
  return (
    <>
      <PageHeader title="Import data" description="Coming from Tally, Busy, Marg or Vyapar? Bring your items, customers and suppliers — and their opening balances — across in a few minutes." />
      <Tabs defaultValue="sheet">
        <TabsList><TabsTrigger value="sheet">Excel / CSV</TabsTrigger><TabsTrigger value="tally">Tally XML</TabsTrigger></TabsList>
        <TabsContent value="sheet"><SheetImport /></TabsContent>
        <TabsContent value="tally"><TallyImport /></TabsContent>
      </Tabs>
    </>
  );
}

function SheetImport() {
  const { base } = useBusiness();
  const [kind, setKind] = useState<"products" | "customers" | "suppliers">("products");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<(SheetResult & { dry: boolean }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async (dry: boolean) => {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api<SheetResult>(`${base}/data/import/${kind}`, { body: fd, query: { dry_run: dry } });
      setResult({ ...r, dry });
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)); } finally { setBusy(false); }
  };
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
      <Card>
        <CardHeader><CardTitle>Upload a list</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="What is in the file" htmlFor="im-kind"><NativeSelect id="im-kind" value={kind} onChange={(e) => { setKind(e.target.value as typeof kind); setResult(null); }}>
              <option value="products">Items / products</option><option value="customers">Customers (debtors)</option><option value="suppliers">Suppliers (creditors)</option></NativeSelect></Field>
            <Field label="File (.xlsx or .csv)" htmlFor="im-file"><Input id="im-file" type="file" accept=".xlsx,.xlsm,.csv" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); }} /></Field>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" disabled={!file || busy} onClick={() => run(true)}>{busy ? <Loader2Icon className="animate-spin" /> : <FileSpreadsheetIcon />} Check file</Button>
            <Button disabled={!file || busy || !result?.dry || result.errors.length > 0} onClick={() => run(false)}><FileUpIcon /> Import {result?.dry && !result.errors.length ? result.valid_rows : ""}</Button>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          {result && <ResultBox ok={!result.errors.length} title={result.dry ? (result.errors.length ? "Fix these rows and check again" : `${result.valid_rows} rows are ready to import`) : `${result.imported} imported`}
            lines={result.errors.slice(0, 50).map((e) => `Row ${e.row}: ${e.error}`)} />}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Columns we understand</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>Export the list from your old software and upload it as it is. Column names from Busy, Marg, Vyapar and Tally reports are recognised, for example:</p>
          {kind === "products" ? <ul className="list-disc space-y-1 pl-5">
            <li><b>Item Name</b> / Product / Stock Item</li><li><b>Item Code</b> / Alias / SKU — made from the name if missing</li>
            <li>Unit / Main Unit (Pcs, Nos, Kgs, Btl…)</li><li>HSN/SAC, Tax Category / GST % (e.g. “GST 12%”)</li>
            <li>Sale Price, Purc Price, M.R.P., Barcode, Min Stock</li></ul>
          : <ul className="list-disc space-y-1 pl-5"><li><b>Party Name</b> / Account / Ledger Name</li><li>GST No / GSTIN, Mobile No, E-mail</li>
            <li>State (name or code), Address 1, Station / City, Pin Code</li><li>Credit Limit, Credit Days</li></ul>}
          <p>Nothing is saved until every row is valid. For opening balances use the Tally tab, or Accounting → Opening balances.</p>
        </CardContent>
      </Card>
    </div>
  );
}

function TallyImport() {
  const { base } = useBusiness();
  const warehouses = useBizQuery<{ id: string; code: string; name: string }[]>(["warehouses"], "/warehouses");
  const years = useBizQuery<{ id: string; code: string; start_date: string; end_date: string }[]>(["financial-years"], "/financial-years");
  const [file, setFile] = useState<File | null>(null);
  const [withOpening, setWithOpening] = useState(true);
  const [fyPick, setFy] = useState("");
  const [whPick, setWh] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TallyResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fy = fyPick || years.data?.[0]?.start_date || "";
  const wh = whPick || warehouses.data?.[0]?.id || "";
  const run = async (dry: boolean) => {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api<TallyResult>(`${base}/data/import-tally`, { body: fd, query: { dry_run: dry, ...(withOpening && fy ? { opening_date: fy, warehouse_id: wh } : {}) } });
      setResult(r);
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)); } finally { setBusy(false); }
  };
  const summary = result && [`${result.customers} customers`, `${result.suppliers} suppliers`, `${result.products} items`,
    result.skipped ? `${result.skipped} already here` : "", result.opening_balances ? `${result.opening_balances} opening balances` : "",
    result.opening_stock_lines ? `${result.opening_stock_lines} items of opening stock` : ""].filter(Boolean).join(" · ");
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
      <Card>
        <CardHeader><CardTitle>Tally masters (XML)</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <Field label="Master.xml from Tally" htmlFor="tl-file"><Input id="tl-file" type="file" accept=".xml" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); }} /></Field>
          <label className="flex items-center gap-2 text-sm"><Switch checked={withOpening} onCheckedChange={(v) => { setWithOpening(v); setResult(null); }} /> Bring opening balances and opening stock</label>
          {withOpening && <div className="grid gap-4 sm:grid-cols-2">
            <Field label="As on (start of year)" htmlFor="tl-fy"><NativeSelect id="tl-fy" value={fy} onChange={(e) => setFy(e.target.value)}>
              {years.data?.map((y) => <option key={y.id} value={y.start_date}>{y.code} — {y.start_date}</option>)}</NativeSelect></Field>
            <Field label="Opening stock into" htmlFor="tl-wh"><NativeSelect id="tl-wh" value={wh} onChange={(e) => setWh(e.target.value)}>
              {warehouses.data?.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}</NativeSelect></Field>
          </div>}
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" disabled={!file || busy} onClick={() => run(true)}>{busy ? <Loader2Icon className="animate-spin" /> : <FileSpreadsheetIcon />} Check file</Button>
            <Button disabled={!file || busy || !result?.dry_run || result.errors.length > 0} onClick={() => run(false)}><FileUpIcon /> Import</Button>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          {result && <ResultBox ok={!result.errors.length} title={result.errors.length ? "Fix these in Tally and export again" : `${result.dry_run ? "Ready" : "Imported"}: ${summary}`} lines={result.errors.slice(0, 50)} />}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>How to export from Tally</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <ol className="list-decimal space-y-1 pl-5">
            <li>Gateway of Tally → <b>Export</b> (Alt+E) → <b>Masters</b>.</li>
            <li>Type of masters: <b>All Masters</b>, format <b>XML (Data Interchange)</b>.</li>
            <li>Upload the <b>Master.xml</b> file here.</li>
          </ol>
          <p>Ledgers under Sundry Debtors become customers, Sundry Creditors become suppliers (with GSTIN, state, address and credit period). Stock items come with unit, HSN, GST rate and opening quantity at their rate.</p>
          <p>Names already here are skipped, so importing again is safe. Opening balances need an open financial year with no opening balances yet.</p>
        </CardContent>
      </Card>
    </div>
  );
}

function ResultBox({ ok, title, lines }: { ok: boolean; title: string; lines: string[] }) {
  return (
    <div className={`rounded-md border p-3 text-sm ${ok ? "border-primary/30 bg-primary-soft" : "border-destructive/30 bg-destructive/5"}`}>
      <p className="flex items-center gap-2 font-medium">{ok ? <CheckCircle2Icon className="size-4 text-primary" /> : <XCircleIcon className="size-4 text-destructive" />}{title}</p>
      {lines.length > 0 && <ul className="mt-2 max-h-60 list-disc space-y-0.5 overflow-auto pl-5 text-xs">{lines.map((l, i) => <li key={i}>{l}</li>)}</ul>}
    </div>
  );
}
