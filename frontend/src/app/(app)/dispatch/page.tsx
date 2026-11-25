"use client";

import { useQueryClient } from "@tanstack/react-query";
import { CameraIcon, CheckCircle2Icon, Loader2Icon, MapPinIcon, PackageCheckIcon, PackageIcon, PhoneIcon, TruckIcon, XCircleIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { StaticTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, openFile } from "@/lib/api";
import { date, money } from "@/lib/format";

type Status = "pending" | "packed" | "out" | "delivered" | "failed";
interface Dispatch {
  id: string; document_id: string; status: Status; packages: number; vehicle_no: string | null; driver_name: string | null; driver_phone: string | null;
  transporter: string | null; lr_no: string | null; dispatched_at: string | null; delivered_at: string | null; receiver_name: string | null;
  pod_file_id: string | null; failure_reason: string | null; number: string; doc_date: string; party_name: string | null; amount: string;
  balance_due: string; phone: string | null; address: string | null;
}
interface ToSend { id: string; number: string; doc_type: string; doc_date: string; party_name: string | null; amount: string }
const ST: Record<Status, [string, "warning" | "secondary" | "default" | "destructive"]> = {
  pending: ["To pack", "warning"], packed: ["Packed", "secondary"], out: ["Out for delivery", "secondary"], delivered: ["Delivered", "default"], failed: ["Not delivered", "destructive"],
};

export default function DispatchPage() {
  const { base, can, isOwner } = useBusiness();
  const edit = isOwner || can("dispatch.update");
  const [tab, setTab] = useState("open");
  const list = useBizQuery<Dispatch[]>(["dispatch", tab], "/dispatches", { status: tab === "open" ? "open" : tab === "delivered" ? "delivered" : undefined }, { enabled: tab !== "send" });
  const open = useBizQuery<Dispatch[]>(["dispatch", "open"], "/dispatches", { status: "open" });
  const send = useBizQuery<ToSend[]>(["dispatch", "to-send"], "/dispatches/to-send");
  const [out, setOut] = useState<Dispatch | null>(null);
  const [deliver, setDeliver] = useState<Dispatch | null>(null);
  const [fail, setFail] = useState<Dispatch | null>(null);
  const add = useBizMutation<{ document_id: string }>("POST", "/dispatches", { invalidate: [["dispatch"]], success: "Added to dispatch" });
  const move = useBizMutation<{ id: string; status: Status }>("PATCH", (b) => `/dispatches/${b.id}`, { invalidate: [["dispatch"]], success: "Updated" });
  const o = open.data ?? [];
  const rows = tab === "send" ? [] : list.data ?? [];
  return (
    <>
      <PageHeader title="Dispatch & delivery" description="Pack, send with a vehicle, and close each delivery with who received it and a photo of the signed copy." />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:mb-6 sm:gap-4 lg:grid-cols-4">
        <StatCard label="Bills not dispatched" icon={PackageIcon} tone={send.data?.length ? "amber" : "green"} loading={send.isLoading} value={String(send.data?.length ?? "—")} />
        <StatCard label="To pack" icon={PackageCheckIcon} loading={open.isLoading} value={String(o.filter((d) => d.status === "pending").length)} />
        <StatCard label="Out for delivery" icon={TruckIcon} loading={open.isLoading} value={String(o.filter((d) => d.status === "out" || d.status === "packed").length)} />
        <StatCard label="Not delivered" icon={XCircleIcon} tone={o.some((d) => d.status === "failed") ? "red" : "green"} loading={open.isLoading} value={String(o.filter((d) => d.status === "failed").length)} />
      </div>
      <Card className="overflow-hidden">
        <div className="border-b p-3"><Tabs value={tab} onValueChange={setTab}><TabsList className="max-w-full overflow-x-auto">
          <TabsTrigger value="open">In progress</TabsTrigger><TabsTrigger value="send">Not dispatched</TabsTrigger>
          <TabsTrigger value="delivered">Delivered</TabsTrigger><TabsTrigger value="all">All</TabsTrigger></TabsList></Tabs></div>
        {tab === "send" ? (
          send.error ? <ErrorState error={send.error} retry={() => send.refetch()} /> : send.isLoading ? <TableSkeleton /> : !send.data?.length ? (
            <EmptyState icon={PackageCheckIcon} title="Everything is dispatched" description="Final invoices and delivery challans of the last 30 days appear here until you add them to dispatch." />
          ) : (
            <StaticTable rows={send.data} rowKey={(d) => d.id} columns={[
              { key: "n", header: "Bill", mobile: "title", cell: (d) => <div><p className="font-medium">{d.party_name}</p><p className="text-xs text-muted-foreground"><Link className="hover:underline" href={`/documents/${d.id}`}>{d.number}</Link> · {date(d.doc_date)}</p></div> },
              { key: "a", header: "Amount", align: "right", mobile: "aside", cell: (d) => <span className="num">{money(d.amount)}</span> },
              ...(edit ? [{ key: "x", header: "", mobile: "row" as const, cell: (d: ToSend) => <div className="flex justify-end"><Button size="sm" variant="outline" disabled={add.isPending} onClick={() => add.mutate({ document_id: d.id })}><PackageIcon /> Add to dispatch</Button></div> }] : []),
            ]} />
          )
        ) : list.error ? <ErrorState error={list.error} retry={() => list.refetch()} /> : list.isLoading ? <TableSkeleton /> : !rows.length ? (
          <EmptyState icon={TruckIcon} title="Nothing here" description="Add bills from the “Not dispatched” tab." />
        ) : (
          <StaticTable rows={rows} rowKey={(d) => d.id} columns={[
            { key: "n", header: "Delivery", mobile: "title", cell: (d) => <div><p className="font-medium">{d.party_name}</p>
              <p className="text-xs text-muted-foreground"><Link className="hover:underline" href={`/documents/${d.document_id}`}>{d.number}</Link> · {d.packages} pkg{d.address ? ` · ${d.address}` : ""}</p>
              {d.failure_reason && <p className="text-xs text-destructive">{d.failure_reason}</p>}</div> },
            { key: "v", header: "Vehicle", cell: (d) => d.vehicle_no ? <span className="text-sm">{d.vehicle_no}{d.driver_name ? ` · ${d.driver_name}` : ""}</span> : "—" },
            { key: "s", header: "Status", mobile: "aside", cell: (d) => <div className="text-right sm:text-left"><Badge variant={ST[d.status][1]}>{ST[d.status][0]}</Badge>
              {d.status === "delivered" && <p className="text-xs text-muted-foreground">{d.receiver_name ?? ""}{d.delivered_at ? ` · ${new Date(d.delivered_at).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" })}` : ""}</p>}</div> },
            { key: "x", header: "", mobile: "row", cell: (d) => <div className="flex flex-wrap justify-end gap-1">
              {d.phone && <Button asChild size="icon" variant="ghost" aria-label="Call customer"><a href={`tel:${d.phone}`}><PhoneIcon /></a></Button>}
              {d.pod_file_id && <Button size="sm" variant="ghost" onClick={() => openFile(`${base}/files/${d.pod_file_id}/download`)}><CameraIcon /> POD</Button>}
              {edit && d.status === "pending" && <Button size="sm" variant="outline" onClick={() => move.mutate({ id: d.id, status: "packed" })}>Packed</Button>}
              {edit && (d.status === "pending" || d.status === "packed" || d.status === "failed") && <Button size="sm" variant="outline" onClick={() => setOut(d)}><TruckIcon /> Send</Button>}
              {edit && d.status === "out" && <><Button size="sm" onClick={() => setDeliver(d)}><CheckCircle2Icon /> Delivered</Button>
                <Button size="sm" variant="ghost" className="text-destructive" onClick={() => setFail(d)}>Not delivered</Button></>}
            </div> },
          ]} />
        )}
      </Card>
      {out && <SendDialog d={out} onClose={() => setOut(null)} />}
      {deliver && <DeliverDialog d={deliver} onClose={() => setDeliver(null)} />}
      {fail && <FailDialog d={fail} onClose={() => setFail(null)} />}
    </>
  );
}

function SendDialog({ d, onClose }: { d: Dispatch; onClose: () => void }) {
  const [f, setF] = useState({ vehicle_no: d.vehicle_no ?? "", driver_name: d.driver_name ?? "", driver_phone: d.driver_phone ?? "", transporter: d.transporter ?? "", lr_no: d.lr_no ?? "", packages: String(d.packages) });
  const save = useBizMutation<Record<string, unknown>>("PATCH", `/dispatches/${d.id}`, { invalidate: [["dispatch"]], success: "Out for delivery", onSuccess: onClose });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF((x) => ({ ...x, [k]: e.target.value }));
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Send {d.number}</DialogTitle><DialogDescription>{d.party_name}{d.address ? ` — ${d.address}` : ""}</DialogDescription></DialogHeader>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate({ status: "out", packages: Number(f.packages) || 1,
          ...Object.fromEntries(Object.entries(f).filter(([k]) => k !== "packages").map(([k, v]) => [k, v.trim() || null])) }); }}>
          <Field label="Vehicle no." htmlFor="sd-v"><Input id="sd-v" value={f.vehicle_no} onChange={set("vehicle_no")} placeholder="GJ05AB1234" /></Field>
          <Field label="Packages" htmlFor="sd-p"><Input id="sd-p" inputMode="numeric" value={f.packages} onChange={set("packages")} /></Field>
          <Field label="Driver / delivery boy" htmlFor="sd-d"><Input id="sd-d" value={f.driver_name} onChange={set("driver_name")} /></Field>
          <Field label="Driver mobile" htmlFor="sd-dp"><Input id="sd-dp" inputMode="tel" value={f.driver_phone} onChange={set("driver_phone")} /></Field>
          <Field label="Transporter" htmlFor="sd-t"><Input id="sd-t" value={f.transporter} onChange={set("transporter")} /></Field>
          <Field label="LR / docket no." htmlFor="sd-lr"><Input id="sd-lr" value={f.lr_no} onChange={set("lr_no")} /></Field>
          <DialogFooter className="sm:col-span-2"><Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />}<TruckIcon /> Out for delivery</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeliverDialog({ d, onClose }: { d: Dispatch; onClose: () => void }) {
  const { base } = useBusiness();
  const qc = useQueryClient();
  const [receiver, setReceiver] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [gps, setGps] = useState<{ lat: number; lng: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const where = () => navigator.geolocation?.getCurrentPosition((p) => setGps({ lat: p.coords.latitude, lng: p.coords.longitude }), () => toast.error("Location not available"), { timeout: 8000 });
  const submit = async () => {
    setBusy(true);
    try {
      if (photo) {
        const fd = new FormData();
        fd.append("file", photo);
        if (receiver.trim()) fd.append("receiver_name", receiver.trim());
        if (gps) { fd.append("latitude", gps.lat.toFixed(6)); fd.append("longitude", gps.lng.toFixed(6)); }
        await api(`${base}/dispatches/${d.id}/pod`, { body: fd });
      } else {
        await api(`${base}/dispatches/${d.id}`, { method: "PATCH", body: { status: "delivered", receiver_name: receiver.trim(), ...(gps ? { latitude: gps.lat.toFixed(6), longitude: gps.lng.toFixed(6) } : {}) } });
      }
      await qc.invalidateQueries({ queryKey: ["biz"] });
      toast.success(`${d.number} delivered`);
      onClose();
    } catch (e) { toast.error(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Delivered {d.number}</DialogTitle>
          <DialogDescription>{d.party_name}{Number(d.balance_due) > 0 ? ` · ${money(d.balance_due)} to collect` : ""}</DialogDescription></DialogHeader>
        <div className="space-y-4">
          <Field label="Received by" htmlFor="dl-r"><Input id="dl-r" value={receiver} onChange={(e) => setReceiver(e.target.value)} placeholder="Name of the person who signed" /></Field>
          <Field label="Photo of the signed copy" htmlFor="dl-ph" hint="Opens the camera on a phone">
            <Input id="dl-ph" type="file" accept="image/*" capture="environment" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} /></Field>
          <Button type="button" variant="outline" size="sm" onClick={where}><MapPinIcon /> {gps ? `Location saved (${gps.lat.toFixed(4)}, ${gps.lng.toFixed(4)})` : "Add my location"}</Button>
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={(!receiver.trim() && !photo) || busy} onClick={submit}>{busy && <Loader2Icon className="animate-spin" />}<CheckCircle2Icon /> Mark delivered</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FailDialog({ d, onClose }: { d: Dispatch; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const save = useBizMutation<Record<string, unknown>>("PATCH", `/dispatches/${d.id}`, { invalidate: [["dispatch"]], success: "Marked not delivered", onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Not delivered — {d.number}</DialogTitle><DialogDescription>It stays in the list to send again.</DialogDescription></DialogHeader>
        <div className="flex flex-wrap gap-1">{["Shop closed", "Customer refused", "Wrong address", "No payment ready", "Vehicle problem"].map((r) => <Button key={r} size="sm" variant="outline" onClick={() => setReason(r)}>{r}</Button>)}</div>
        <Input aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason" />
        <DialogFooter><Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button variant="destructive" disabled={!reason.trim() || save.isPending} onClick={() => save.mutate({ status: "failed", failure_reason: reason })}>Not delivered</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
