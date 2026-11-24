"use client";

import { DownloadIcon, ExternalLinkIcon, Loader2Icon, TruckIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/switch";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";
import { date, money } from "@/lib/format";

interface Pending { id: string; number: string; doc_type: string; doc_date: string; party_name: string | null; goods_value: string; is_interstate: boolean }

/** Invoices that need an e-way bill: build the portal's bulk JSON, then record the numbers it returns. */
export function EwayPanel() {
  const { base } = useBusiness();
  const q = useBizQuery<Pending[]>(["eway", "pending"], "/gst/eway-bills/pending", { days: 60 });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [t, setT] = useState({ distance_km: "", vehicle_no: "", transporter_id: "", transporter_name: "", mode: "road" });
  const [busy, setBusy] = useState(false);
  const [rec, setRec] = useState<Record<string, string>>({});
  const rows = q.data ?? [];

  async function downloadJson() {
    setBusy(true);
    try {
      const res = await api<Response>(`${base}/gst/eway-bills/json`, { raw: true, method: "POST", body: {
        document_ids: [...picked], distance_km: Number(t.distance_km), mode: t.mode, vehicle_no: t.vehicle_no || undefined,
        transporter_id: t.transporter_id || undefined, transporter_name: t.transporter_name || undefined } });
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = "eway-bills.json";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 30_000);
      toast.success("JSON downloaded — upload it on the e-way bill portal (Generate → Bulk)");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Could not prepare the file");
    } finally {
      setBusy(false);
    }
  }

  async function record(id: string) {
    try {
      await api(`${base}/gst/eway-bills/${id}`, { method: "POST", body: { eway_bill_no: rec[id], distance_km: Number(t.distance_km) || undefined } });
      toast.success("E-way bill recorded — it now prints on the invoice");
      q.refetch();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Could not record");
    }
  }

  if (q.error) return <Card><ErrorState error={q.error} retry={() => q.refetch()} /></Card>;
  return (
    <div className="space-y-4">
      <Card><CardContent className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Field label="Distance (km)" htmlFor="ew-km"><Input id="ew-km" inputMode="numeric" value={t.distance_km} onChange={(e) => setT({ ...t, distance_km: e.target.value.replace(/\D/g, "") })} /></Field>
        <Field label="Mode" htmlFor="ew-mode"><NativeSelect id="ew-mode" value={t.mode} onChange={(e) => setT({ ...t, mode: e.target.value })}>
          <option value="road">Road</option><option value="rail">Rail</option><option value="air">Air</option><option value="ship">Ship</option></NativeSelect></Field>
        <Field label="Vehicle no." htmlFor="ew-veh"><Input id="ew-veh" className="uppercase" value={t.vehicle_no} onChange={(e) => setT({ ...t, vehicle_no: e.target.value })} placeholder="MH12AB1234" /></Field>
        <Field label="Transporter ID" htmlFor="ew-tid"><Input id="ew-tid" className="uppercase" value={t.transporter_id} onChange={(e) => setT({ ...t, transporter_id: e.target.value.toUpperCase() })} placeholder="GSTIN / TRANSIN" /></Field>
        <Field label="Transporter" htmlFor="ew-tn"><Input id="ew-tn" value={t.transporter_name} onChange={(e) => setT({ ...t, transporter_name: e.target.value })} /></Field>
        <div className="flex items-end"><Button className="w-full" disabled={!picked.size || !Number(t.distance_km) || busy} onClick={downloadJson}>
          {busy ? <Loader2Icon className="animate-spin" /> : <DownloadIcon />} Portal JSON ({picked.size})</Button></div>
      </CardContent></Card>
      <Card className="overflow-hidden">
        {q.isLoading ? <TableSkeleton rows={4} cols={5} /> : !rows.length ? (
          <EmptyState icon={TruckIcon} title="No e-way bills needed" description="Invoices moving goods worth over ₹50,000 appear here until their e-way bill number is recorded." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-xs text-muted-foreground uppercase"><tr><th className="w-10 px-3 py-2" /><th className="px-3 py-2 text-left">Document</th>
                <th className="px-3 py-2 text-left">Party</th><th className="px-3 py-2 text-right">Goods value</th><th className="px-3 py-2 text-left">E-way bill no. (from the portal)</th></tr></thead>
              <tbody className="divide-y">{rows.map((r) => (
                <tr key={r.id}>
                  <td className="px-3"><Checkbox checked={picked.has(r.id)} aria-label={`Select ${r.number}`}
                    onCheckedChange={(v) => setPicked((s) => { const n = new Set(s); if (v) n.add(r.id); else n.delete(r.id); return n; })} /></td>
                  <td className="px-3 py-2"><Link href={`/documents/${r.id}`} className="font-medium text-blue hover:underline">{r.number}</Link>
                    <p className="text-xs text-muted-foreground">{date(r.doc_date)}{r.is_interstate ? " · inter-state" : ""}</p></td>
                  <td className="px-3">{r.party_name}</td>
                  <td className="num px-3 text-right">{money(r.goods_value)}</td>
                  <td className="px-3 py-2"><div className="flex gap-2">
                    <Input className="num h-8 w-40" inputMode="numeric" placeholder="12 digits" value={rec[r.id] ?? ""} aria-label={`E-way bill number for ${r.number}`}
                      onChange={(e) => setRec({ ...rec, [r.id]: e.target.value.replace(/\D/g, "").slice(0, 12) })} />
                    <Button size="sm" variant="outline" disabled={(rec[r.id] ?? "").length !== 12} onClick={() => record(r.id)}>Save</Button></div></td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
        <p className="flex flex-wrap items-center gap-1 border-t px-4 py-2 text-xs text-muted-foreground">Generate on
          <a href="https://ewaybillgst.gov.in" target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-blue hover:underline">ewaybillgst.gov.in <ExternalLinkIcon className="size-3" /></a>
          then save the number here. Validity is set from the distance (1 day per 200 km).</p>
      </Card>
    </div>
  );
}
