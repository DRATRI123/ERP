"use client";

import {
  CheckCircle2Icon, Loader2Icon, MapPinIcon, MessageCircleIcon, NotebookPenIcon, PhoneIcon, PlusIcon, ShoppingBagIcon,
  TargetIcon, Trash2Icon, WalletIcon,
} from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { StaticTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { PartyPicker } from "@/components/pickers/party-picker";
import { ProductSearch } from "@/components/pickers/product-search";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PortalLinkCard } from "@/features/system/portal-link";
import { PaymentDialog } from "@/features/payments/payment-dialog";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";
import { date, money, todayISO } from "@/lib/format";
import type { Party, Product } from "@/lib/types";
import { useAuth } from "@/stores/auth";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const OUTCOMES: [string, string][] = [["order", "Order taken"], ["payment", "Payment collected"], ["order_payment", "Order + payment"],
  ["no_order", "No order"], ["closed", "Shop closed"], ["other", "Other"]];
const OUTCOME_LABEL = Object.fromEntries(OUTCOMES);

interface Card_ { party_id: string; name: string; phone: string | null; address: string | null; outstanding: string | null; credit_hold: boolean; last_visit: string | null; visited_today: string | null }
interface Day { day: string; beat: { id: string; name: string } | null; beats: { id: string; name: string; weekday: number | null }[]; customers: Card_[]; visits_today: number; productive_today: number }
interface Beat { id: string; name: string; weekday: number | null; salesman_id: string | null; party_ids: string[]; notes: string | null; is_active: boolean }
interface Person { id: string; name: string; email: string }

function FieldPage() {
  const params = useSearchParams();
  const router = useRouter();
  const { can, isOwner } = useBusiness();
  const tab = params.get("tab") ?? "day";
  const manager = isOwner || can("reports.view");
  return (
    <>
      <PageHeader title="Field sales" description="Beats for salesmen, their day on the phone — orders, collections and visit notes — and monthly targets." />
      {(isOwner || can("users.manage")) && <PortalLinkCard />}
      <Tabs value={tab} onValueChange={(v) => router.replace(`/field?tab=${v}`)}>
        <TabsList className="mb-4">
          <TabsTrigger value="day">My day</TabsTrigger><TabsTrigger value="targets">Targets</TabsTrigger>
          {(isOwner || can("customers.manage")) && <TabsTrigger value="beats">Beats</TabsTrigger>}
          <TabsTrigger value="visits">Visits</TabsTrigger>
        </TabsList>
        <TabsContent value="day"><MyDay /></TabsContent>
        <TabsContent value="targets"><Targets manager={manager} editable={isOwner || can("users.manage")} /></TabsContent>
        <TabsContent value="beats"><Beats /></TabsContent>
        <TabsContent value="visits"><Visits /></TabsContent>
      </Tabs>
    </>
  );
}

export default function Page() {
  return <Suspense><FieldPage /></Suspense>;
}

// ---------------------------------------------------------------------------- my day

function MyDay() {
  const [beatId, setBeatId] = useState("");
  const q = useBizQuery<Day>(["field-day", beatId], "/field/day", { beat_id: beatId || undefined });
  const [order, setOrder] = useState<Card_ | null>(null);
  const [collect, setCollect] = useState<Card_ | null>(null);
  const [note, setNote] = useState<{ c: Card_; outcome?: string; document_id?: string } | null>(null);
  const [other, setOther] = useState<Party | null>(null);
  const d = q.data;
  if (q.error) return <Card><ErrorState error={q.error} retry={() => q.refetch()} /></Card>;
  if (!d) return <TableSkeleton />;
  const extra: Card_ | null = other ? { party_id: other.id, name: other.name, phone: other.phone, address: null, outstanding: null, credit_hold: false, last_visit: null, visited_today: null } : null;
  const cards = [...d.customers, ...(extra && !d.customers.some((c) => c.party_id === extra.party_id) ? [extra] : [])];
  return (
    <div className="space-y-3">
      <Card><CardContent className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted-foreground">{new Date(d.day).toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "short" })}</p>
          <p className="font-semibold">{d.beat ? d.beat.name : "No beat for today"}</p>
          <p className="text-xs text-muted-foreground">{d.visits_today} visits · {d.productive_today} productive · {d.customers.filter((c) => !c.visited_today).length} left</p>
        </div>
        {d.beats.length > 1 && <NativeSelect className="w-auto" value={beatId || d.beat?.id || ""} onChange={(e) => setBeatId(e.target.value)} aria-label="Beat">
          {d.beats.map((b) => <option key={b.id} value={b.id}>{b.name}{b.weekday !== null ? ` (${DAYS[b.weekday]!.slice(0, 3)})` : ""}</option>)}</NativeSelect>}
      </CardContent></Card>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {cards.map((c, i) => (
          <Card key={c.party_id} className={c.visited_today ? "opacity-80" : ""}>
            <CardContent className="space-y-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0"><p className="font-semibold"><span className="mr-1.5 text-muted-foreground">{i + 1}.</span>{c.name}</p>
                  {c.address && <p className="truncate text-xs text-muted-foreground">{c.address}</p>}
                  <p className="text-xs text-muted-foreground">{c.last_visit ? `Last visit ${date(c.last_visit)}` : "Not visited yet"}</p></div>
                {c.visited_today ? <Badge><CheckCircle2Icon className="size-3" /> {OUTCOME_LABEL[c.visited_today]}</Badge> : c.credit_hold ? <Badge variant="destructive">Credit hold</Badge> : null}
              </div>
              {c.outstanding !== null && Number(c.outstanding) > 0 && <p className="text-sm">Dues <b className="num">{money(c.outstanding)}</b></p>}
              <div className="grid grid-cols-3 gap-2">
                <Button size="sm" onClick={() => setOrder(c)}><ShoppingBagIcon /> Order</Button>
                <Button size="sm" variant="outline" onClick={() => setCollect(c)}><WalletIcon /> Collect</Button>
                <Button size="sm" variant="outline" onClick={() => setNote({ c })}><NotebookPenIcon /> Note</Button>
              </div>
              <div className="flex gap-3 text-xs">
                {c.phone && <a className="flex items-center gap-1 text-primary" href={`tel:${c.phone}`}><PhoneIcon className="size-3.5" /> Call</a>}
                {c.phone && <a className="flex items-center gap-1 text-primary" href={`https://wa.me/91${c.phone.replace(/\D/g, "").slice(-10)}`} target="_blank" rel="noreferrer"><MessageCircleIcon className="size-3.5" /> WhatsApp</a>}
                {c.address && <a className="flex items-center gap-1 text-primary" href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${c.name} ${c.address}`)}`} target="_blank" rel="noreferrer"><MapPinIcon className="size-3.5" /> Map</a>}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      {!d.customers.length && <Card><EmptyState title="No customers on today's beat" description="Ask your manager to set up your beats, or visit any customer below." /></Card>}
      <Card><CardContent><Field label="Visit another customer"><PartyPicker kind="customers" value={other} onChange={setOther} placeholder="Search customer" /></Field></CardContent></Card>
      {order && <QuickOrder c={order} onClose={() => setOrder(null)} onDone={(docId) => { setOrder(null); setNote({ c: order, outcome: "order", document_id: docId }); }} />}
      {collect && <PaymentDialog open initial={{ direction: "in", partyId: collect.party_id }} onOpenChange={(v) => { if (!v) { setNote({ c: collect, outcome: "payment" }); setCollect(null); } }} />}
      {note && <VisitNote {...note} beatId={d.beat?.id} onClose={() => setNote(null)} />}
    </div>
  );
}

function QuickOrder({ c, onClose, onDone }: { c: Card_; onClose: () => void; onDone: (docId: string) => void }) {
  const { base } = useBusiness();
  const me = useAuth((s) => s.me);
  const [lines, setLines] = useState<{ p: Product; qty: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const add = (p: Product) => setLines((ls) => ls.some((l) => l.p.id === p.id) ? ls.map((l) => (l.p.id === p.id ? { ...l, qty: String(Number(l.qty) + 1) } : l)) : [...ls, { p, qty: "1" }]);
  const total = lines.reduce((s, l) => s + Number(l.qty || 0) * Number(l.p.selling_price ?? 0), 0);
  async function save() {
    setBusy(true);
    try {
      const doc = await api<{ id: string }>(`${base}/documents`, { body: { doc_type: "sales_order", party_id: c.party_id, salesperson_id: me?.user.id,
        client_ref: `field-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, lines: lines.map((l) => ({ product_id: l.p.id, quantity: l.qty })) } });
      const fin = await api<{ id: string; number: string }>(`${base}/documents/${doc.id}/finalize`, { body: { payments: [] } });
      toast.success(`Order ${fin.number} booked for ${c.name}`);
      onDone(fin.id);
    } catch (e) { toast.error((e as ApiError).message); setBusy(false); }
  }
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Order — {c.name}</DialogTitle><DialogDescription>The office turns it into an invoice when the goods go out. Prices follow the customer&apos;s price list.</DialogDescription></DialogHeader>
        <ProductSearch onPick={add} placeholder="Search or scan product" />
        {lines.length > 0 && <ul className="divide-y rounded-lg border">{lines.map((l, i) => (
          <li key={l.p.id} className="flex items-center gap-2 px-3 py-2 text-sm">
            <span className="min-w-0 flex-1 truncate">{l.p.name}</span>
            <Input value={l.qty} inputMode="decimal" className="num h-8 w-16 text-center" aria-label={`Quantity of ${l.p.name}`}
              onChange={(e) => setLines((ls) => ls.map((x, j) => (j === i ? { ...x, qty: e.target.value.replace(/[^\d.]/g, "") } : x)))} />
            <Button variant="ghost" size="icon-sm" onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))} aria-label="Remove"><Trash2Icon /></Button>
          </li>))}</ul>}
        <DialogFooter className="items-center">
          <span className="num mr-auto text-sm text-muted-foreground">≈ {money(total.toFixed(2))}</span>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!lines.length || lines.some((l) => !(Number(l.qty) > 0)) || busy} onClick={save}>{busy && <Loader2Icon className="animate-spin" />} Book order</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function VisitNote({ c, outcome, document_id, beatId, onClose }: { c: Card_; outcome?: string; document_id?: string; beatId?: string; onClose: () => void }) {
  const [f, setF] = useState({ outcome: outcome ?? "no_order", note: "", next_visit: "", gps: true });
  const save = useBizMutation<Record<string, unknown>>("POST", "/field/visits", { invalidate: [["field-day"], ["field-visits"]], success: "Visit saved", onSuccess: onClose });
  function submit() {
    const body = { party_id: c.party_id, outcome: f.outcome, note: f.note || null, next_visit: f.next_visit || null, beat_id: beatId ?? null, document_id: document_id ?? null };
    if (f.gps && typeof navigator !== "undefined" && navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => save.mutate({ ...body, latitude: pos.coords.latitude.toFixed(6), longitude: pos.coords.longitude.toFixed(6) }),
        () => save.mutate(body), { timeout: 8000, maximumAge: 60_000 });
    } else save.mutate(body);
  }
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Visit — {c.name}</DialogTitle></DialogHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Outcome" htmlFor="vn-out"><NativeSelect id="vn-out" value={f.outcome} onChange={(e) => setF((x) => ({ ...x, outcome: e.target.value }))}>
            {OUTCOMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</NativeSelect></Field>
          <Field label="Visit again on" htmlFor="vn-next"><Input id="vn-next" type="date" min={todayISO()} value={f.next_visit} onChange={(e) => setF((x) => ({ ...x, next_visit: e.target.value }))} /></Field>
          <Field label="Note" htmlFor="vn-note" className="sm:col-span-2"><Input id="vn-note" value={f.note} onChange={(e) => setF((x) => ({ ...x, note: e.target.value }))} placeholder="Stock low on Triphala; wants new scheme" /></Field>
          <label className="flex items-center gap-2 text-sm sm:col-span-2"><input type="checkbox" className="size-4 accent-primary" checked={f.gps} onChange={(e) => setF((x) => ({ ...x, gps: e.target.checked }))} /> Save my location with the visit</label>
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose}>Skip</Button>
          <Button disabled={save.isPending} onClick={submit}>{save.isPending && <Loader2Icon className="animate-spin" />} Save visit</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------- targets

interface TargetRow { salesman_id: string; name: string; target: string | null; collection_target: string | null; sales: string; invoices: number; orders: string; order_count: number; collections: string; visits: number; productive_visits: number; achieved_percent: number | null; on_pace: boolean | null }

function Targets({ manager, editable }: { manager: boolean; editable: boolean }) {
  const [month, setMonth] = useState(todayISO().slice(0, 7));
  const q = useBizQuery<{ rows: TargetRow[]; days: number; days_elapsed: number }>(["field-targets", month], "/field/targets", { month: `${month}-01` });
  const [setting, setSetting] = useState<TargetRow | null>(null);
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b p-3">
        <Input type="month" className="w-44" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month" />
        {q.data && <span className="text-xs text-muted-foreground">Day {q.data.days_elapsed} of {q.data.days}{manager ? "" : " · your figures"}</span>}
      </div>
      {q.error ? <ErrorState error={q.error} retry={() => q.refetch()} /> : !q.data ? <TableSkeleton /> : (
        <StaticTable rows={q.data.rows} rowKey={(r) => r.salesman_id} columns={[
          { key: "n", header: "Salesman", mobile: "title", cell: (r) => <span className="font-medium">{r.name}</span> },
          { key: "t", header: "Target", align: "right", cell: (r) => <span className="num">{r.target ? money(r.target) : "—"}</span> },
          { key: "s", header: "Sales", align: "right", mobile: "aside", cell: (r) => <span className="num font-medium">{money(r.sales)}
            {r.achieved_percent !== null && <span className={`block text-xs ${r.on_pace ? "text-primary" : "text-amber-700 dark:text-warning"}`}>{r.achieved_percent}% {r.on_pace ? "· on pace" : "· behind"}</span>}</span> },
          { key: "bar", header: "", mobile: "hide", cell: (r) => r.achieved_percent !== null && (
            <div className="h-2 w-28 overflow-hidden rounded-full bg-muted"><div className={`h-full ${r.on_pace ? "bg-primary" : "bg-warning"}`} style={{ width: `${Math.min(100, r.achieved_percent)}%` }} /></div>) },
          { key: "o", header: "Orders", align: "right", cell: (r) => <span className="num">{money(r.orders)}<span className="block text-xs text-muted-foreground">{r.order_count}</span></span> },
          { key: "c", header: "Collected", align: "right", cell: (r) => <span className="num">{money(r.collections)}{r.collection_target && <span className="block text-xs text-muted-foreground">of {money(r.collection_target)}</span>}</span> },
          { key: "v", header: "Visits", align: "right", cell: (r) => <span className="num">{r.visits}<span className="block text-xs text-muted-foreground">{r.productive_visits} productive</span></span> },
          ...(editable ? [{ key: "x", header: "", mobile: "row" as const, cell: (r: TargetRow) => <Button size="sm" variant="ghost" onClick={() => setSetting(r)}><TargetIcon /> Set target</Button> }] : []),
        ]} />
      )}
      {setting && <SetTarget row={setting} month={month} onClose={() => setSetting(null)} />}
    </Card>
  );
}

function SetTarget({ row, month, onClose }: { row: TargetRow; month: string; onClose: () => void }) {
  const [amount, setAmount] = useState(row.target ? String(Number(row.target)) : "");
  const [coll, setColl] = useState(row.collection_target ? String(Number(row.collection_target)) : "");
  const save = useBizMutation<Record<string, unknown>>("PUT", "/field/targets", { invalidate: [["field-targets"]], success: "Target saved", onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Target for {row.name} — {new Date(`${month}-01`).toLocaleDateString("en-IN", { month: "long", year: "numeric" })}</DialogTitle>
          <DialogDescription>Sales are invoices (before tax) where they are the salesperson, less returns.</DialogDescription></DialogHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Sales target (₹)" htmlFor="tg-a"><Input id="tg-a" inputMode="decimal" className="num" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} /></Field>
          <Field label="Collection target (₹)" htmlFor="tg-c"><Input id="tg-c" inputMode="decimal" className="num" value={coll} onChange={(e) => setColl(e.target.value.replace(/[^\d.]/g, ""))} /></Field>
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={amount === "" || save.isPending} onClick={() => save.mutate({ salesman_id: row.salesman_id, month: `${month}-01`, amount, collection_amount: coll || null })}>Save</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------- beats

function Beats() {
  const beats = useBizQuery<Beat[]>(["field-beats"], "/field/beats");
  const people = useBizQuery<Person[]>(["field-salesmen"], "/field/salesmen");
  const [editing, setEditing] = useState<Beat | "new" | null>(null);
  const name = (id: string | null) => people.data?.find((p) => p.id === id)?.name ?? "—";
  return (
    <Card className="overflow-hidden">
      <div className="flex justify-end border-b p-3"><Button onClick={() => setEditing("new")}><PlusIcon /> New beat</Button></div>
      {beats.error ? <ErrorState error={beats.error} retry={() => beats.refetch()} /> : !beats.data ? <TableSkeleton /> : !beats.data.length ? (
        <EmptyState title="No beats yet" description="A beat is a route: the customers a salesman visits, in order, usually on a fixed day." />
      ) : (
        <StaticTable rows={beats.data} rowKey={(b) => b.id} columns={[
          { key: "n", header: "Beat", mobile: "title", cell: (b) => <span className="font-medium">{b.name}{!b.is_active && <Badge variant="secondary" className="ml-2">Inactive</Badge>}</span> },
          { key: "d", header: "Day", cell: (b) => (b.weekday !== null ? DAYS[b.weekday] : "Any day") },
          { key: "s", header: "Salesman", cell: (b) => name(b.salesman_id) },
          { key: "c", header: "Customers", align: "right", mobile: "aside", cell: (b) => <span className="num">{b.party_ids.length}</span> },
          { key: "x", header: "", mobile: "row", cell: (b) => <Button size="sm" variant="ghost" onClick={() => setEditing(b)}>Edit</Button> },
        ]} />
      )}
      {editing && <BeatDialog beat={editing === "new" ? null : editing} people={people.data ?? []} onClose={() => setEditing(null)} />}
    </Card>
  );
}

function BeatDialog({ beat, people, onClose }: { beat: Beat | null; people: Person[]; onClose: () => void }) {
  const { base } = useBusiness();
  const [f, setF] = useState({ name: beat?.name ?? "", weekday: beat?.weekday ?? null as number | null, salesman_id: beat?.salesman_id ?? "", is_active: beat?.is_active ?? true });
  const names = useQuery({
    queryKey: ["biz", base, "beat-parties", beat?.id], enabled: !!beat?.party_ids.length, staleTime: Infinity,
    queryFn: () => Promise.all(beat!.party_ids.map((id) => api<Party>(`${base}/customers/${id}`).then((p) => ({ id: p.id, name: p.name })).catch(() => ({ id, name: "(removed)" })))),
  });
  const [edited, setParties] = useState<{ id: string; name: string }[] | null>(null);
  const parties = edited ?? names.data ?? [];
  const [pick, setPick] = useState<Party | null>(null);
  const save = useBizMutation<Record<string, unknown>>(beat ? "PATCH" : "POST", beat ? `/field/beats/${beat.id}` : "/field/beats", { invalidate: [["field-beats"]], success: "Beat saved", onSuccess: onClose });
  const move = (i: number, d: number) => { const c = [...parties]; const [x] = c.splice(i, 1); c.splice(Math.max(0, Math.min(c.length, i + d)), 0, x!); setParties(c); };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>{beat ? "Edit beat" : "New beat"}</DialogTitle></DialogHeader>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Name" htmlFor="bt-name"><Input id="bt-name" value={f.name} onChange={(e) => setF((x) => ({ ...x, name: e.target.value }))} placeholder="Adajan – Monday" /></Field>
          <Field label="Day" htmlFor="bt-day"><NativeSelect id="bt-day" value={f.weekday ?? ""} onChange={(e) => setF((x) => ({ ...x, weekday: e.target.value === "" ? null : Number(e.target.value) }))}>
            <option value="">Any day</option>{DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</NativeSelect></Field>
          <Field label="Salesman" htmlFor="bt-sm"><NativeSelect id="bt-sm" value={f.salesman_id} onChange={(e) => setF((x) => ({ ...x, salesman_id: e.target.value }))}>
            <option value="">— Not assigned —</option>{people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</NativeSelect></Field>
        </div>
        <div className="rounded-lg border">
          <div className="border-b p-2"><PartyPicker kind="customers" value={pick} placeholder="Add a customer to the route" onChange={(p) => { if (p && !parties.some((x) => x.id === p.id)) setParties([...parties, { id: p.id, name: p.name }]); setPick(null); }} /></div>
          {parties.length === 0 ? <p className="p-3 text-sm text-muted-foreground">No customers yet.</p> : (
            <ol className="max-h-64 divide-y overflow-y-auto">{parties.map((p, i) => (
              <li key={p.id} className="flex items-center gap-2 px-3 py-1.5 text-sm">
                <span className="w-6 text-muted-foreground">{i + 1}.</span><span className="min-w-0 flex-1 truncate">{p.name}</span>
                <Button variant="ghost" size="icon-sm" onClick={() => move(i, -1)} aria-label="Move up" disabled={i === 0}>↑</Button>
                <Button variant="ghost" size="icon-sm" onClick={() => move(i, 1)} aria-label="Move down" disabled={i === parties.length - 1}>↓</Button>
                <Button variant="ghost" size="icon-sm" onClick={() => setParties(parties.filter((x) => x.id !== p.id))} aria-label="Remove"><Trash2Icon /></Button>
              </li>))}</ol>)}
        </div>
        <DialogFooter>
          {beat && <label className="mr-auto flex items-center gap-2 text-sm"><input type="checkbox" className="size-4" checked={f.is_active} onChange={(e) => setF((x) => ({ ...x, is_active: e.target.checked }))} /> Active</label>}
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!f.name.trim() || save.isPending} onClick={() => save.mutate({ name: f.name.trim(), weekday: f.weekday, salesman_id: f.salesman_id || null, party_ids: parties.map((p) => p.id), is_active: f.is_active })}>Save beat</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------- visits

function Visits() {
  const [day, setDay] = useState(todayISO());
  const q = useBizQuery<{ id: string; at: string; party_name: string; salesman_name: string; outcome: string; note: string | null; latitude: string | null; longitude: string | null; next_visit: string | null }[]>(
    ["field-visits", day], "/field/visits", { date_from: day, date_to: day });
  return (
    <Card className="overflow-hidden">
      <div className="border-b p-3"><Input type="date" className="w-44" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Day" /></div>
      {q.error ? <ErrorState error={q.error} retry={() => q.refetch()} /> : !q.data ? <TableSkeleton /> : !q.data.length ? <EmptyState title="No visits on this day" /> : (
        <StaticTable rows={q.data} rowKey={(v) => v.id} columns={[
          { key: "p", header: "Customer", mobile: "title", cell: (v) => <div><p className="font-medium">{v.party_name}</p>{v.note && <p className="text-xs text-muted-foreground">{v.note}</p>}</div> },
          { key: "s", header: "Salesman", cell: (v) => v.salesman_name },
          { key: "t", header: "Time", cell: (v) => new Date(v.at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) },
          { key: "o", header: "Outcome", mobile: "aside", cell: (v) => <Badge variant={["order", "payment", "order_payment"].includes(v.outcome) ? "default" : "secondary"}>{OUTCOME_LABEL[v.outcome]}</Badge> },
          { key: "g", header: "Where", cell: (v) => v.latitude ? <a className="text-primary hover:underline" target="_blank" rel="noreferrer" href={`https://www.google.com/maps?q=${v.latitude},${v.longitude}`}><MapPinIcon className="inline size-3.5" /> Map</a> : "—" },
          { key: "n", header: "Next visit", cell: (v) => (v.next_visit ? date(v.next_visit) : "—") },
        ]} />
      )}
    </Card>
  );
}
