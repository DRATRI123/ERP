"use client";

import {
  CheckCircle2Icon, ChevronLeftIcon, Loader2Icon, MapPinIcon, MessageCircleIcon, MessagesSquareIcon, MinusIcon, NotebookPenIcon, PhoneIcon, PlusIcon,
  SearchIcon, ShoppingCartIcon, TargetIcon, UserPlusIcon, WalletIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Thumb, thumbOf } from "@/components/data/thumb";
import { ChatPanel } from "@/features/chat/chat-panel";
import type { Contact } from "@/features/chat/use-chat";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { useDebounced } from "@/hooks/use-debounce";
import { api, ApiError, type Page } from "@/lib/api";
import { date, money, qty } from "@/lib/format";
import type { Party, Product } from "@/lib/types";
import { useAuth } from "@/stores/auth";
import { useQueryClient } from "@tanstack/react-query";

interface Cust { party_id: string; name: string; phone: string | null; address: string | null; outstanding: string | null; credit_hold: boolean; visited_today: string | null }
interface Day { beat: { id: string; name: string } | null; customers: Cust[] }
interface Summary {
  name: string; today: { orders: number; order_value: string; collected: string; visits: number; productive: number };
  month: { target: string | null; orders: string; sales: string; collections: string; achieved_percent: number | null } | null;
  orders: { id: string; number: string | null; doc_date: string; party_name: string | null; total: string; invoiced: boolean }[];
  collections: { id: string; date: string; party_name: string | null; amount: string; mode: string }[];
}
type Picked = { id: string; name: string; phone?: string | null };

function useGps() {
  return () => new Promise<{ latitude?: string; longitude?: string }>((resolve) => {
    if (!navigator.geolocation) return resolve({});
    navigator.geolocation.getCurrentPosition((p) => resolve({ latitude: p.coords.latitude.toFixed(6), longitude: p.coords.longitude.toFixed(6) }),
      () => resolve({}), { timeout: 6000, maximumAge: 120_000 });
  });
}

export default function SalesPortal() {
  const sp = useSearchParams();
  const tab = sp.get("tab") ?? "home";
  const partyId = sp.get("party");
  const partyName = sp.get("name") ?? "";
  const picked = partyId ? { id: partyId, name: partyName } : null;
  if (tab === "customers") return <Customers />;
  if (tab === "order") return picked ? <OrderView party={picked} /> : <PickCustomer next="order" title="Order for which customer?" />;
  if (tab === "collect") return picked ? <CollectView party={picked} /> : <PickCustomer next="collect" title="Collect from which customer?" />;
  if (tab === "visit" && picked) return <VisitView party={picked} />;
  if (tab === "new") return <NewCustomer />;
  if (tab === "chat") return <ChatView peer={sp.get("with")} />;
  return <Home />;
}

/** Unique per order, so a retry after a dropped connection never books it twice. */
const newRef = () => `sp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const go = (tab: string, p?: Picked) => `/sp?tab=${tab}${p ? `&party=${p.id}&name=${encodeURIComponent(p.name)}` : ""}`;

function Home() {
  const s = useBizQuery<Summary>(["field-me"], "/field/me");
  const day = useBizQuery<Day>(["field-day"], "/field/day");
  const m = s.data?.month;
  const pct = m?.achieved_percent ?? null;
  return (
    <div className="space-y-3">
      <p className="text-lg font-semibold">Namaste{s.data ? `, ${s.data.name.split(" ")[0]}` : ""}</p>
      <div className="grid grid-cols-3 gap-2 text-center">
        {[["Orders", s.data ? `${s.data.today.orders}` : "—", s.data ? money(s.data.today.order_value) : ""], ["Collected", s.data ? money(s.data.today.collected) : "—", ""],
          ["Visits", s.data ? `${s.data.today.visits}` : "—", s.data ? `${s.data.today.productive} productive` : ""]].map(([l, v, sub]) => (
          <Card key={l}><CardContent className="p-2"><p className="text-[11px] text-muted-foreground uppercase">{l}</p><p className="num text-base font-semibold">{v}</p>{sub && <p className="text-[10px] text-muted-foreground">{sub}</p>}</CardContent></Card>
        ))}
      </div>
      {m && <Card><CardContent className="space-y-1.5 p-3">
        <p className="flex items-center gap-1.5 text-sm font-medium"><TargetIcon className="size-4 text-primary" /> This month</p>
        {m.target ? <>
          <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary" style={{ width: `${Math.min(100, pct ?? 0)}%` }} /></div>
          <p className="text-xs text-muted-foreground">{money(m.sales)} sold of {money(m.target)} target{pct !== null ? ` · ${pct}%` : ""} · orders {money(m.orders)} · collected {money(m.collections)}</p>
        </> : <p className="text-xs text-muted-foreground">Orders {money(m.orders)} · sold {money(m.sales)} · collected {money(m.collections)}</p>}
      </CardContent></Card>}
      <div className="grid grid-cols-3 gap-2">
        <Button asChild className="h-14 flex-col gap-0.5 text-xs"><Link href="/sp?tab=order"><ShoppingCartIcon /> New order</Link></Button>
        <Button asChild variant="outline" className="h-14 flex-col gap-0.5 text-xs"><Link href="/sp?tab=collect"><WalletIcon /> Collect</Link></Button>
        <Button asChild variant="outline" className="h-14 flex-col gap-0.5 text-xs"><Link href="/sp?tab=new"><UserPlusIcon /> New shop</Link></Button>
      </div>
      <h2 className="pt-1 text-sm font-semibold">{day.data?.beat ? `Today's beat · ${day.data.beat.name}` : "Today's customers"}</h2>
      {day.isLoading ? <Skeleton className="h-32" /> : !day.data?.customers.length ? (
        <p className="rounded-lg border border-dashed p-3 text-sm text-muted-foreground">No beat for today. Use Customers to find any shop, or ask the office to set your beats.</p>
      ) : day.data.customers.map((c) => <CustomerCard key={c.party_id} c={c} />)}
      {!!s.data?.orders.length && <>
        <h2 className="pt-1 text-sm font-semibold">My recent orders</h2>
        <ul className="divide-y rounded-lg border bg-card text-sm">{s.data.orders.slice(0, 8).map((o) => (
          <li key={o.id} className="flex items-center justify-between gap-2 px-3 py-2">
            <span className="min-w-0"><span className="block truncate font-medium">{o.party_name}</span><span className="text-xs text-muted-foreground">{o.number} · {date(o.doc_date)}</span></span>
            <span className="text-right"><span className="num block">{money(o.total)}</span>{o.invoiced ? <Badge>Billed</Badge> : <Badge variant="secondary">Pending</Badge>}</span>
          </li>))}</ul>
      </>}
    </div>
  );
}

function CustomerCard({ c }: { c: Cust }) {
  const p = { id: c.party_id, name: c.name, phone: c.phone };
  return (
    <Card><CardContent className="space-y-2 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0"><p className="font-medium">{c.name}</p>
          {c.address && <p className="truncate text-xs text-muted-foreground">{c.address}</p>}
          {c.outstanding && Number(c.outstanding) > 0 && <p className="text-xs text-amber-700 dark:text-warning">Due {money(c.outstanding)}{c.credit_hold ? " · on credit hold" : ""}</p>}</div>
        {c.visited_today ? <Badge>Visited</Badge> : null}
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Button asChild size="sm"><Link href={go("order", p)}><ShoppingCartIcon /> Order</Link></Button>
        <Button asChild size="sm" variant="outline"><Link href={go("collect", p)}><WalletIcon /> Collect</Link></Button>
        <Button asChild size="sm" variant="outline"><Link href={go("visit", p)}><NotebookPenIcon /> Note</Link></Button>
        {c.phone && <Button asChild size="icon" variant="ghost" className="size-8"><a href={`tel:${c.phone}`} aria-label="Call"><PhoneIcon /></a></Button>}
        {c.address && <Button asChild size="icon" variant="ghost" className="size-8"><a aria-label="Map" target="_blank" rel="noreferrer" href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${c.name} ${c.address}`)}`}><MapPinIcon /></a></Button>}
      </div>
    </CardContent></Card>
  );
}

function useCustomerSearch(term: string) {
  const t = useDebounced(term, 250);
  return useBizQuery<Page<Party>>(["sp-customers", t], "/customers", { q: t || undefined, limit: 30 });
}

function Customers() {
  const [q, setQ] = useState("");
  const list = useCustomerSearch(q);
  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <div className="relative flex-1"><SearchIcon className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
          <Input className="pl-8" placeholder="Shop name or mobile" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <Button asChild variant="outline"><Link href="/sp?tab=new"><UserPlusIcon /> New</Link></Button>
      </div>
      {list.isLoading ? <Skeleton className="h-40" /> : !list.data?.items.length ? <p className="text-sm text-muted-foreground">No customer found. Add the shop with New.</p>
        : list.data.items.map((p) => <CustomerCard key={p.id} c={{ party_id: p.id, name: p.name, phone: p.phone ?? null, address: null, outstanding: null, credit_hold: !!p.credit_hold, visited_today: null }} />)}
    </div>
  );
}

function PickCustomer({ next, title }: { next: string; title: string }) {
  const [q, setQ] = useState("");
  const list = useCustomerSearch(q);
  return (
    <div className="space-y-3">
      <p className="font-semibold">{title}</p>
      <Input autoFocus placeholder="Shop name or mobile" value={q} onChange={(e) => setQ(e.target.value)} />
      <ul className="divide-y rounded-lg border bg-card">{(list.data?.items ?? []).map((p) => (
        <li key={p.id}><Link className="flex items-center justify-between px-3 py-3 text-sm" href={go(next, { id: p.id, name: p.name })}>
          <span className="font-medium">{p.name}</span><span className="text-xs text-muted-foreground">{p.phone}</span></Link></li>))}</ul>
      <Button asChild variant="outline" className="w-full"><Link href="/sp?tab=new"><UserPlusIcon /> New shop</Link></Button>
    </div>
  );
}

function Back({ title }: { title: string }) {
  const router = useRouter();
  return <button type="button" onClick={() => router.back()} className="mb-2 flex items-center gap-1 text-sm font-semibold"><ChevronLeftIcon className="size-4" />{title}</button>;
}

function OrderView({ party }: { party: Picked }) {
  const { base } = useBusiness();
  const me = useAuth((s) => s.me);
  const qc = useQueryClient();
  const gps = useGps();
  const [q, setQ] = useState("");
  const t = useDebounced(q, 250);
  const products = useBizQuery<Page<Product>>(["sp-products", t], "/products", { q: t || undefined, limit: 60, with_stock: true, is_active: true });
  const [cart, setCart] = useState<Record<string, { p: Product; qty: number; free: number }>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ id: string; number: string; total: string } | null>(null);
  const lines = Object.values(cart).filter((l) => l.qty > 0);
  const approx = lines.reduce((s, l) => s + l.qty * Number(l.p.selling_price ?? 0), 0);
  const set = (p: Product, patch: Partial<{ qty: number; free: number }>) => setCart((c) => {
    const cur = c[p.id] ?? { p, qty: 0, free: 0 };
    return { ...c, [p.id]: { ...cur, ...patch, qty: Math.max(0, patch.qty ?? cur.qty), free: Math.max(0, patch.free ?? cur.free) } };
  });
  async function book() {
    setBusy(true);
    try {
      const doc = await api<{ id: string }>(`${base}/documents`, { body: { doc_type: "sales_order", party_id: party.id, salesperson_id: me?.user.id,
        notes: note || undefined, client_ref: newRef(),
        lines: lines.map((l) => ({ product_id: l.p.id, quantity: String(l.qty), ...(l.free > 0 ? { free_quantity: String(l.free) } : {}) })) } });
      const fin = await api<{ id: string; number: string; grand_total: string }>(`${base}/documents/${doc.id}/finalize`, { body: { payments: [] } });
      await api(`${base}/field/visits`, { body: { party_id: party.id, outcome: "order", document_id: fin.id, note: note || undefined, ...(await gps()) } }).catch(() => undefined);
      void qc.invalidateQueries({ queryKey: ["biz"] });
      setDone({ id: fin.id, number: fin.number, total: fin.grand_total });
    } catch (e) { toast.error((e as ApiError).message); } finally { setBusy(false); }
  }
  const summary = lines.map((l) => `${l.p.name} × ${l.qty}${l.free ? ` + ${l.free} free` : ""}`).join("\n");
  if (done) return (
    <div className="space-y-3 pt-6 text-center">
      <CheckCircle2Icon className="mx-auto size-12 text-primary" />
      <p className="text-lg font-semibold">Order {done.number} booked</p>
      <p className="text-sm text-muted-foreground">{party.name} · {money(done.total)}. The office makes the bill when the goods go out.</p>
      <Button asChild className="w-full bg-[#25D366] text-white hover:bg-[#1ebe57]"><a target="_blank" rel="noreferrer"
        href={`https://wa.me/${(party.phone ?? "").replace(/\D/g, "").replace(/^(\d{10})$/, "91$1")}?text=${encodeURIComponent(`Order ${done.number} for ${party.name}\n${summary}\nTotal ${money(done.total)}`)}`}><MessageCircleIcon /> Send to the shop on WhatsApp</a></Button>
      <TellOwner documentId={done.id} />
      <div className="grid grid-cols-2 gap-2"><Button asChild variant="outline"><Link href={go("collect", party)}>Collect payment</Link></Button><Button asChild variant="outline"><Link href="/sp">Done</Link></Button></div>
    </div>
  );
  return (
    <div className="space-y-3">
      <Back title={`Order · ${party.name}`} />
      <div className="relative"><SearchIcon className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
        <Input className="pl-8" placeholder="Search products" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <ul className="divide-y rounded-lg border bg-card">{(products.data?.items ?? []).map((p) => {
        const l = cart[p.id]; const out = p.track_inventory && p.on_hand !== undefined && Number(p.on_hand) <= 0;
        return (
          <li key={p.id} className="flex items-center gap-2.5 p-2.5">
            <Thumb src={thumbOf(p)} className="size-12" />
            <div className="min-w-0 flex-1"><p className="text-sm leading-snug font-medium">{p.name}</p>
              <p className="text-xs text-muted-foreground">{money(p.selling_price)}{p.mrp ? ` · MRP ${money(p.mrp)}` : ""}{p.on_hand !== undefined && p.track_inventory ? ` · ${out ? "out of stock" : `${qty(p.on_hand as string)} in stock`}` : ""}</p>
              {l && l.qty > 0 && <label className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">+ free <Input inputMode="numeric" value={l.free || ""} placeholder="0" aria-label={`Free ${p.name}`}
                onChange={(e) => set(p, { free: Number(e.target.value.replace(/\D/g, "") || 0) })} className="num h-6 w-12 px-1 text-center text-xs" /></label>}</div>
            {l && l.qty > 0 ? (
              <div className="flex items-center gap-1">
                <Button size="icon" variant="outline" className="size-8" aria-label={`Fewer ${p.name}`} onClick={() => set(p, { qty: l.qty - 1 })}><MinusIcon /></Button>
                <Input inputMode="numeric" value={String(l.qty)} aria-label={`Quantity of ${p.name}`} onChange={(e) => set(p, { qty: Number(e.target.value.replace(/\D/g, "") || 0) })} className="num h-8 w-12 px-1 text-center" />
                <Button size="icon" variant="outline" className="size-8" aria-label={`More ${p.name}`} onClick={() => set(p, { qty: l.qty + 1 })}><PlusIcon /></Button>
              </div>
            ) : <Button size="sm" variant="outline" onClick={() => set(p, { qty: 1 })}>Add</Button>}
          </li>);
      })}</ul>
      {lines.length > 0 && <Textarea rows={2} placeholder="Note for the office (delivery day, scheme asked…)" value={note} onChange={(e) => setNote(e.target.value)} />}
      {lines.length > 0 && (
        <div className="fixed inset-x-0 bottom-[calc(3.6rem+env(safe-area-inset-bottom))] z-10 mx-auto flex max-w-md items-center gap-3 border-t bg-background/95 px-3 py-2 backdrop-blur">
          <div className="flex-1 text-sm"><p className="font-semibold">{lines.length} item{lines.length === 1 ? "" : "s"} · ≈ {money(approx.toFixed(2))}</p><p className="text-[11px] text-muted-foreground">Schemes and price list apply automatically</p></div>
          <Button disabled={busy} onClick={book}>{busy && <Loader2Icon className="animate-spin" />} Book order</Button>
        </div>)}
    </div>
  );
}

function CollectView({ party }: { party: Picked }) {
  const { base } = useBusiness();
  const qc = useQueryClient();
  const gps = useGps();
  const [f, setF] = useState({ amount: "", mode: "cash", reference: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  async function save() {
    setBusy(true);
    try {
      const pay = await api<{ id: string }>(`${base}/payments`, { body: { direction: "in", party_id: party.id, mode: f.mode, amount: f.amount,
        reference: f.reference || undefined, notes: f.notes ? `Collected by salesperson: ${f.notes}` : "Collected by salesperson" } });
      await api(`${base}/field/visits`, { body: { party_id: party.id, outcome: "payment", payment_id: pay.id, ...(await gps()) } }).catch(() => undefined);
      void qc.invalidateQueries({ queryKey: ["biz"] });
      setDone(true);
    } catch (e) { toast.error((e as ApiError).message); } finally { setBusy(false); }
  }
  if (done) return (
    <div className="space-y-3 pt-6 text-center">
      <CheckCircle2Icon className="mx-auto size-12 text-primary" />
      <p className="text-lg font-semibold">{money(f.amount)} collected</p>
      <p className="text-sm text-muted-foreground">From {party.name} by {f.mode.toUpperCase()}. It is in the books now; hand the cash to the office.</p>
      <Button asChild variant="outline" className="w-full"><Link href="/sp">Done</Link></Button>
    </div>
  );
  return (
    <div className="space-y-3">
      <Back title={`Collect · ${party.name}`} />
      <Field label="Amount (₹)" htmlFor="sp-amt"><Input id="sp-amt" autoFocus inputMode="decimal" className="num text-lg" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
      <div className="grid grid-cols-3 gap-2">{[["cash", "Cash"], ["upi", "UPI"], ["cheque", "Cheque"]].map(([v, l]) => (
        <Button key={v} type="button" variant={f.mode === v ? "default" : "outline"} onClick={() => setF({ ...f, mode: v! })}>{l}</Button>))}</div>
      {f.mode !== "cash" && <Field label={f.mode === "upi" ? "UPI reference / UTR" : "Cheque number and bank"} htmlFor="sp-ref"><Input id="sp-ref" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>}
      <Field label="Note (optional)" htmlFor="sp-n"><Input id="sp-n" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
      <Button className="w-full" disabled={!(Number(f.amount) > 0) || (f.mode !== "cash" && !f.reference.trim()) || busy} onClick={save}>{busy && <Loader2Icon className="animate-spin" />} Save collection</Button>
    </div>
  );
}

function VisitView({ party }: { party: Picked }) {
  const { base } = useBusiness();
  const qc = useQueryClient();
  const router = useRouter();
  const gps = useGps();
  const [f, setF] = useState({ outcome: "no_order", note: "", next_visit: "" });
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      await api(`${base}/field/visits`, { body: { party_id: party.id, outcome: f.outcome, note: f.note || undefined, next_visit: f.next_visit || undefined, ...(await gps()) } });
      void qc.invalidateQueries({ queryKey: ["biz"] });
      toast.success("Visit saved"); router.push("/sp");
    } catch (e) { toast.error((e as ApiError).message); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-3">
      <Back title={`Visit · ${party.name}`} />
      <Field label="What happened" htmlFor="sp-o"><NativeSelect id="sp-o" value={f.outcome} onChange={(e) => setF({ ...f, outcome: e.target.value })}>
        <option value="no_order">Met, no order</option><option value="closed">Shop closed / owner not there</option><option value="other">Other</option></NativeSelect></Field>
      <Field label="Note" htmlFor="sp-vn"><Textarea id="sp-vn" rows={3} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="Stock is full; asked for the new scheme; competitor visited…" /></Field>
      <Field label="Visit again on (optional)" htmlFor="sp-nv"><Input id="sp-nv" type="date" value={f.next_visit} onChange={(e) => setF({ ...f, next_visit: e.target.value })} /></Field>
      <Button className="w-full" disabled={busy} onClick={save}>{busy && <Loader2Icon className="animate-spin" />} Save visit</Button>
    </div>
  );
}

function NewCustomer() {
  const { base } = useBusiness();
  const router = useRouter();
  const gps = useGps();
  const [f, setF] = useState({ name: "", contact_person: "", phone: "", address: "", city: "", pincode: "", gstin: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });
  async function save() {
    setBusy(true);
    try {
      const loc = await gps();
      const p = await api<Party>(`${base}/customers`, { body: { name: f.name.trim(), contact_person: f.contact_person || undefined, phone: f.phone || undefined,
        gstin: f.gstin.trim().toUpperCase() || undefined, gst_type: f.gstin.trim() ? "regular" : "unregistered", notes: f.notes || undefined,
        attributes: loc.latitude ? { location: `${loc.latitude},${loc.longitude}`, added_from: "salesperson portal" } : { added_from: "salesperson portal" },
        addresses: f.address.trim() ? [{ kind: "billing", address_line1: f.address.trim(), city: f.city || undefined, pincode: /^\d{6}$/.test(f.pincode) ? f.pincode : undefined, is_default: true }] : [] } });
      toast.success(`${p.name} added`);
      router.replace(go("order", { id: p.id, name: p.name }));
    } catch (e) { toast.error((e as ApiError).message); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-3">
      <Back title="New shop / customer" />
      <Field label="Shop / firm name" htmlFor="nc-n"><Input id="nc-n" autoFocus value={f.name} onChange={set("name")} /></Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Owner / contact" htmlFor="nc-c"><Input id="nc-c" value={f.contact_person} onChange={set("contact_person")} /></Field>
        <Field label="Mobile" htmlFor="nc-p"><Input id="nc-p" inputMode="tel" value={f.phone} onChange={set("phone")} /></Field>
      </div>
      <Field label="Address" htmlFor="nc-a"><Input id="nc-a" value={f.address} onChange={set("address")} /></Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="City" htmlFor="nc-ci"><Input id="nc-ci" value={f.city} onChange={set("city")} /></Field>
        <Field label="PIN code" htmlFor="nc-pin"><Input id="nc-pin" inputMode="numeric" value={f.pincode} onChange={set("pincode")} /></Field>
      </div>
      <Field label="GSTIN (if registered)" htmlFor="nc-g"><Input id="nc-g" value={f.gstin} onChange={set("gstin")} className="uppercase" /></Field>
      <Field label="Notes" htmlFor="nc-no"><Textarea id="nc-no" rows={2} value={f.notes} onChange={set("notes")} placeholder="Doctor's clinic next door; buys on Monday…" /></Field>
      <p className="text-xs text-muted-foreground">Your location is saved with the shop so the office can see it on a map.</p>
      <Button className="w-full" disabled={f.name.trim().length < 2 || busy} onClick={save}>{busy && <Loader2Icon className="animate-spin" />} Add and take order</Button>
    </div>
  );
}

function ChatView({ peer }: { peer: string | null }) {
  const router = useRouter();
  return <ChatPanel peer={peer} linkDocs={false} onPeer={(id) => router.replace(id ? `/sp?tab=chat&with=${id}` : "/sp?tab=chat")} className="h-[calc(100dvh-10.5rem)]" />;
}

/** After booking an order: send it to the owner in chat with one tap. */
function TellOwner({ documentId }: { documentId: string }) {
  const { base } = useBusiness();
  const contacts = useBizQuery<{ contacts: Contact[] }>(["chat", "contacts"], "/chat/contacts");
  const owner = contacts.data?.contacts.find((c) => c.role === "owner") ?? contacts.data?.contacts[0];
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(false);
  if (!owner) return null;
  if (sent) return <p className="text-sm text-primary">Sent to {owner.name} in chat ✓</p>;
  return (
    <div className="flex gap-2">
      <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder={`Note for ${owner.name} (optional)`} aria-label="Note" />
      <Button variant="outline" onClick={async () => {
        try { await api(`${base}/chat/messages`, { body: { to: owner.id, document_id: documentId, body: note || undefined } }); setSent(true); }
        catch (e) { toast.error((e as ApiError).message); }
      }}><MessagesSquareIcon /> Tell {owner.name.split(" ")[0]}</Button>
    </div>
  );
}
