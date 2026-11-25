"use client";

import { ClipboardCheckIcon, Loader2Icon, MinusIcon, PlusIcon, RotateCcwIcon, ScanLineIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { StaticTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { CameraScanner } from "@/components/scan/camera-scanner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";
import { qty, todayISO } from "@/lib/format";
import type { Product } from "@/lib/types";

interface Balance { product_id: string; sku: string; product_name: string; batch_no: string | null; quantity: string }
interface Row { key: string; product_id: string; sku: string; name: string; batch_no: string | null; book: number; barcode?: string | null }
const DRAFT = (biz: string, wh: string) => `erp-stock-audit:${biz}:${wh}`;

function loadDraft(key: string): Record<string, number> {
  try { return JSON.parse(localStorage.getItem(key) || "{}") as Record<string, number>; } catch { return {}; }
}

export default function StockAuditPage() {
  const { base, id: biz, can, isOwner } = useBusiness();
  const edit = isOwner || can("stock.edit");
  const warehouses = useBizQuery<{ id: string; code: string; name: string }[]>(["warehouses"], "/warehouses");
  const [whPick, setWh] = useState("");
  const wh = whPick || warehouses.data?.[0]?.id || "";
  const [rows, setRows] = useState<Row[] | null>(null);
  const [loadErr, setLoadErr] = useState<unknown>(null);
  const [counted, setCounted] = useState<Record<string, number>>({});
  const [fullAudit, setFullAudit] = useState(false);
  const [code, setCode] = useState("");
  const [onlyDiff, setOnlyDiff] = useState(false);
  const [lastKey, setLastKey] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!wh || !biz) return;
    let alive = true;
    (async () => {
      setRows(null); setLoadErr(null);
      try {
        const all: Balance[] = [];
        for (let page = 0; page < 50; page++) {
          const r = await api<{ items: Balance[]; total: number }>(`${base}/inventory/balances`, { query: { warehouse_id: wh, limit: 200, offset: page * 200 } });
          all.push(...r.items);
          if (r.items.length < 200) break;
        }
        if (!alive) return;
        setRows(all.map((b) => ({ key: `${b.product_id}|${b.batch_no ?? ""}`, product_id: b.product_id, sku: b.sku, name: b.product_name, batch_no: b.batch_no, book: Number(b.quantity) })));
        setCounted(loadDraft(DRAFT(biz, wh)));
      } catch (e) { if (alive) setLoadErr(e); }
    })();
    return () => { alive = false; };
  }, [wh, biz, base, reload]);

  useEffect(() => {
    if (!biz || !wh || rows === null) return;
    try { localStorage.setItem(DRAFT(biz, wh), JSON.stringify(counted)); } catch { /* storage full */ }
  }, [counted, biz, wh, rows]);

  const bump = (key: string, by: number) => { setCounted((c) => ({ ...c, [key]: Math.max(0, Math.round(((c[key] ?? 0) + by) * 1000) / 1000) })); setLastKey(key); };

  const scan = async (raw: string) => {
    const c = raw.trim();
    if (!c || !rows) return;
    const lc = c.toLowerCase();
    let hit = rows.find((r) => r.barcode === c || r.sku.toLowerCase() === lc);
    if (!hit) {
      try {
        const p = await api<Product>(`${base}/products/lookup`, { query: { code: c } });
        hit = rows.find((r) => r.product_id === p.id);
        if (!hit) {
          if (p.batch_tracking) { toast.error(`${p.name}: no stock of any batch here — count it by batch from a purchase`); return; }
          hit = { key: `${p.id}|`, product_id: p.id, sku: p.sku, name: p.name, batch_no: null, book: 0, barcode: p.barcode };
          const added = hit;
          setRows((rs) => [added, ...(rs ?? [])]);
        } else {
          const pid = p.id, bc = p.barcode;
          setRows((rs) => (rs ?? []).map((r) => (r.product_id === pid ? { ...r, barcode: bc } : r)));
        }
      } catch (e) {
        toast.error(e instanceof ApiError && e.status === 404 ? `No product with code ${c}` : "Lookup failed");
        return;
      }
    }
    bump(hit.key, 1);
    toast.success(`${hit.name}${hit.batch_no ? ` · ${hit.batch_no}` : ""}: ${(counted[hit.key] ?? 0) + 1}`, { duration: 1200 });
  };

  const view = useMemo(() => {
    const rs = rows ?? [];
    const withCount = rs.map((r) => ({ ...r, count: counted[r.key] ?? (fullAudit ? 0 : undefined) }));
    const list = onlyDiff ? withCount.filter((r) => r.count !== undefined && r.count !== r.book) : withCount;
    return [...list].sort((a, b) => (a.key === lastKey ? -1 : b.key === lastKey ? 1 : (a.count === undefined ? 1 : 0) - (b.count === undefined ? 1 : 0) || a.name.localeCompare(b.name)));
  }, [rows, counted, fullAudit, onlyDiff, lastKey]);
  const done = (rows ?? []).filter((r) => counted[r.key] !== undefined).length;
  const diffs = (rows ?? []).filter((r) => { const c = counted[r.key] ?? (fullAudit ? 0 : undefined); return c !== undefined && c !== r.book; });

  const post = useBizMutation<Record<string, unknown>>("POST", "/inventory/counts", {
    invalidate: [["products"], ["inventory"]], success: "Count posted — stock corrected",
    onSuccess: () => { setCounted({}); try { localStorage.removeItem(DRAFT(biz ?? "", wh)); } catch { /* */ } setReload((n) => n + 1); },
  });
  const submit = () => {
    const lines = (rows ?? []).filter((r) => counted[r.key] !== undefined || fullAudit)
      .map((r) => ({ product_id: r.product_id, batch_no: r.batch_no, counted_quantity: String(counted[r.key] ?? 0) }));
    post.mutate({ warehouse_id: wh, doc_date: todayISO(), lines, reference: "Stock audit", notes: fullAudit ? "Full audit: items not counted set to zero" : "Spot check" });
  };

  return (
    <>
      <PageHeader title="Stock audit" description="Walk the shelves with your phone: scan each item (camera, USB scanner or typed code), then post the count. Differences correct the stock."
        actions={edit && <Button disabled={!rows || (!done && !fullAudit) || post.isPending} onClick={submit}>{post.isPending ? <Loader2Icon className="animate-spin" /> : <ClipboardCheckIcon />} Post count</Button>} />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:mb-6 sm:gap-4 lg:grid-cols-3">
        <StatCard label="Counted" icon={ScanLineIcon} loading={!rows && !loadErr} value={rows ? `${done} / ${rows.length}` : "—"} />
        <StatCard label="Differences" icon={ClipboardCheckIcon} tone={diffs.length ? "amber" : "green"} loading={!rows && !loadErr} value={String(diffs.length)} />
      </div>
      <Card className="mb-4 space-y-3 p-3 sm:p-4">
        <div className="grid gap-3 sm:grid-cols-[14rem_1fr]">
          <Field label="Godown" htmlFor="sa-wh"><NativeSelect id="sa-wh" value={wh} onChange={(e) => setWh(e.target.value)}>
            {warehouses.data?.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}</NativeSelect></Field>
          <Field label="Scan or type a barcode / SKU" htmlFor="sa-code">
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); scan(code); setCode(""); input.current?.focus(); }}>
              <Input id="sa-code" ref={input} autoFocus autoComplete="off" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Scan here" />
              <Button type="submit" variant="outline" disabled={!code.trim()}>Add</Button>
            </form>
          </Field>
        </div>
        <CameraScanner onCode={scan} />
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <label className="flex items-center gap-2"><Switch checked={fullAudit} onCheckedChange={setFullAudit} /> Full audit (anything not counted is zero)</label>
          <label className="flex items-center gap-2"><Switch checked={onlyDiff} onCheckedChange={setOnlyDiff} /> Only differences</label>
          {done > 0 && <Button size="sm" variant="ghost" onClick={() => setCounted({})}><RotateCcwIcon /> Start again</Button>}
        </div>
      </Card>
      <Card className="overflow-hidden">
        {loadErr ? <ErrorState error={loadErr as Error} /> : !rows ? <TableSkeleton /> : !view.length ? (
          <EmptyState icon={ScanLineIcon} title={onlyDiff ? "No differences" : "No stock in this godown"} description="Scan an item to add it." />
        ) : (
          <StaticTable rows={view.slice(0, 500)} rowKey={(r) => r.key} columns={[
            { key: "n", header: "Item", mobile: "title", cell: (r) => <div><p className="font-medium">{r.name}</p><p className="text-xs text-muted-foreground">{r.sku}{r.batch_no ? ` · batch ${r.batch_no}` : ""}</p></div> },
            { key: "b", header: "Books", align: "right", cell: (r) => <span className="num">{qty(String(r.book))}</span> },
            { key: "c", header: "Counted", align: "right", mobile: "aside", cell: (r) => (
              <div className="flex items-center justify-end gap-1">
                <Button size="icon" variant="ghost" className="size-8" aria-label="One less" onClick={() => bump(r.key, -1)}><MinusIcon /></Button>
                <Input aria-label={`Counted ${r.name}`} inputMode="decimal" className="num h-8 w-20 text-right" value={r.count === undefined ? "" : String(r.count)} placeholder="—"
                  onChange={(e) => { const v = e.target.value.replace(/[^\d.]/g, ""); setCounted((c) => { const n = { ...c }; if (v === "") delete n[r.key]; else n[r.key] = Number(v); return n; }); }} />
                <Button size="icon" variant="ghost" className="size-8" aria-label="One more" onClick={() => bump(r.key, 1)}><PlusIcon /></Button>
              </div>) },
            { key: "d", header: "Difference", align: "right", mobile: "row", cell: (r) => r.count === undefined ? <span className="text-muted-foreground">not counted</span>
              : r.count === r.book ? <Badge variant="secondary">matches</Badge>
              : <Badge variant={r.count < r.book ? "destructive" : "warning"}>{r.count > r.book ? "+" : ""}{qty(String(Math.round((r.count - r.book) * 1000) / 1000))}</Badge> },
          ]} />
        )}
      </Card>
    </>
  );
}
