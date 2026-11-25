"use client";

import { CheckCircle2Icon, Loader2Icon, PackageXIcon, PlusIcon, XCircleIcon } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "@/components/data/confirm";
import { StaticTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { PartyPicker } from "@/components/pickers/party-picker";
import { ProductSearch } from "@/components/pickers/product-search";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money, qty, todayISO } from "@/lib/format";
import type { Party, Product } from "@/lib/types";

interface Claim {
  id: string; number: string; claim_date: string; product_name: string | null; batch_no: string | null; quantity: string;
  reason: string; cost_value: string; party_name: string | null; claim_amount: string; settled_amount: string;
  status: "open" | "settled" | "rejected" | "written_off"; settlement_ref: string | null; notes: string | null;
}
type Bucket = { count: number; cost: string; claimed: string; settled: string };
const STATUS: Record<Claim["status"], ["warning" | "default" | "destructive" | "secondary", string]> = {
  open: ["warning", "Claimed"], settled: ["default", "Settled"], rejected: ["destructive", "Rejected"], written_off: ["secondary", "Written off"],
};
const REASONS = [["breakage", "Breakage"], ["leakage", "Leakage"], ["transit", "Damaged on arrival"], ["expiry", "Expired"], ["quality", "Quality problem"], ["other", "Other"]] as const;

export default function DamagePage() {
  const { can, isOwner } = useBusiness();
  const edit = isOwner || can("stock.edit");
  const [tab, setTab] = useState("open");
  const list = useBizQuery<Claim[]>(["damage-claims"], "/damage-claims", { status: tab === "all" ? undefined : tab });
  const sum = useBizQuery<Record<Claim["status"], Bucket>>(["damage-claims", "summary"], "/damage-claims/summary");
  const [adding, setAdding] = useState(false);
  const [settling, setSettling] = useState<Claim | null>(null);
  const [rejecting, setRejecting] = useState<Claim | null>(null);
  const reject = useBizMutation<{ reason: string }>("POST", () => `/damage-claims/${rejecting?.id}/reject`, { invalidate: [["damage-claims"]], success: "Claim marked rejected" });
  const s = sum.data;
  const lost = s ? Number(s.written_off.cost) + Number(s.rejected.cost) + Number(s.settled.cost) - Number(s.settled.settled) : 0;
  return (
    <>
      <PageHeader title="Damage & breakage" description="Write off broken, leaking or damaged stock, and claim it from the supplier until they credit it."
        actions={edit && <Button onClick={() => setAdding(true)}><PlusIcon /> Record damage</Button>} />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:mb-6 sm:gap-4 lg:grid-cols-3">
        <StatCard label="Waiting from suppliers" icon={PackageXIcon} tone={s?.open.count ? "amber" : "green"} loading={sum.isLoading} value={s ? money(s.open.claimed) : "—"} sub={s ? `${s.open.count} open claims` : undefined} />
        <StatCard label="Recovered" icon={CheckCircle2Icon} loading={sum.isLoading} value={s ? money(s.settled.settled) : "—"} sub={s ? `${s.settled.count} settled` : undefined} />
        <StatCard label="Net loss (at cost)" icon={XCircleIcon} tone="red" loading={sum.isLoading} value={money(lost.toFixed(2))} />
      </div>
      <Card className="overflow-hidden">
        <div className="border-b p-3"><Tabs value={tab} onValueChange={setTab}><TabsList>
          <TabsTrigger value="open">Claimed</TabsTrigger><TabsTrigger value="settled">Settled</TabsTrigger>
          <TabsTrigger value="rejected">Rejected</TabsTrigger><TabsTrigger value="written_off">Written off</TabsTrigger><TabsTrigger value="all">All</TabsTrigger>
        </TabsList></Tabs></div>
        {list.error ? <ErrorState error={list.error} retry={() => list.refetch()} /> : list.isLoading ? <TableSkeleton /> : !list.data?.length ? (
          <EmptyState icon={PackageXIcon} title="Nothing here" description="Record damaged stock: it leaves stock at cost, and if you name the supplier the claim waits here until they send a credit note." />
        ) : (
          <StaticTable rows={list.data} rowKey={(c) => c.id} columns={[
            { key: "p", header: "Item", mobile: "title", cell: (c) => <div><p className="font-medium">{c.product_name}</p>
              <p className="text-xs text-muted-foreground">{c.number} · {date(c.claim_date)} · {qty(c.quantity)} {REASONS.find(([v]) => v === c.reason)?.[1].toLowerCase()}{c.batch_no ? ` · batch ${c.batch_no}` : ""}</p></div> },
            { key: "s", header: "Supplier", cell: (c) => c.party_name ?? "—" },
            { key: "st", header: "Status", cell: (c) => <span><Badge variant={STATUS[c.status][0]}>{STATUS[c.status][1]}</Badge>{c.settlement_ref && <span className="block text-xs text-muted-foreground">CN {c.settlement_ref}</span>}</span> },
            { key: "cost", header: "Cost", align: "right", cell: (c) => <span className="num">{money(c.cost_value)}</span> },
            { key: "amt", header: "Claim / recovered", align: "right", mobile: "aside", cell: (c) => <span className="num font-medium">{c.status === "settled" ? money(c.settled_amount) : c.party_name ? money(c.claim_amount) : "—"}</span> },
            ...(edit ? [{ key: "x", header: "", mobile: "row" as const, cell: (c: Claim) => c.status === "open" && (
              <div className="flex justify-end gap-1"><Button size="sm" variant="outline" onClick={() => setSettling(c)}><CheckCircle2Icon /> Credit received</Button>
                <Button size="sm" variant="ghost" className="text-destructive" onClick={() => setRejecting(c)}>Rejected</Button></div>) }] : []),
          ]} />
        )}
      </Card>
      {adding && <NewDamage onClose={() => setAdding(false)} />}
      {settling && <Settle claim={settling} onClose={() => setSettling(null)} />}
      <ConfirmDialog open={!!rejecting} onOpenChange={(v) => !v && setRejecting(null)} destructive reason confirm="Mark rejected"
        title={`Supplier rejected ${rejecting?.number}?`} description="The loss stays in Stock adjustments." onConfirm={(reason) => reject.mutateAsync({ reason })} />
    </>
  );
}

function NewDamage({ onClose }: { onClose: () => void }) {
  const [product, setProduct] = useState<Product | null>(null);
  const [supplier, setSupplier] = useState<Party | null>(null);
  const warehouses = useBizQuery<{ id: string; code: string; name: string }[]>(["warehouses"], "/warehouses");
  const batches = useBizQuery<{ batch_no: string; quantity: string; expiry_date: string | null }[]>(["batches-available", product?.id], `/products/${product?.id}/batches/available`, undefined, { enabled: !!product?.batch_tracking });
  const [f, setF] = useState({ warehouse_id: "", batch_no: "", quantity: "1", reason: "breakage", claim_amount: "", notes: "", claim_date: todayISO() });
  const save = useBizMutation<Record<string, unknown>>("POST", "/damage-claims", { invalidate: [["damage-claims"], ["products"]], success: "Damage recorded and stock written off", onSuccess: onClose });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF((x) => ({ ...x, [k]: e.target.value }));
  const wh = f.warehouse_id || warehouses.data?.[0]?.id || "";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>Record damaged stock</DialogTitle>
          <DialogDescription>It leaves stock at its cost. Name the supplier to claim it from them.</DialogDescription></DialogHeader>
        <form className="grid gap-4 sm:grid-cols-4" onSubmit={(e) => { e.preventDefault(); save.mutate({ product_id: product?.id, warehouse_id: wh, batch_no: f.batch_no || null,
          quantity: f.quantity, reason: f.reason, claim_date: f.claim_date, party_id: supplier?.id ?? null, claim_amount: supplier && f.claim_amount ? f.claim_amount : null, notes: f.notes || null }); }}>
          <Field label="Product" className="sm:col-span-4">{product ? <p className="flex items-center gap-2 text-sm font-medium">{product.name}<Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={() => setProduct(null)}>change</Button></p>
            : <ProductSearch onPick={setProduct} placeholder="Scan or search the damaged product" />}</Field>
          <Field label="Godown" htmlFor="dm-wh"><NativeSelect id="dm-wh" value={wh} onChange={set("warehouse_id")}>{warehouses.data?.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}</NativeSelect></Field>
          {product?.batch_tracking && <Field label="Batch" htmlFor="dm-batch"><NativeSelect id="dm-batch" value={f.batch_no} onChange={set("batch_no")}><option value="">Choose batch</option>
            {batches.data?.map((b) => <option key={b.batch_no} value={b.batch_no}>{b.batch_no} · {qty(b.quantity)} left</option>)}</NativeSelect></Field>}
          <Field label="Quantity" htmlFor="dm-qty"><Input id="dm-qty" inputMode="decimal" className="num" value={f.quantity} onChange={set("quantity")} /></Field>
          <Field label="Reason" htmlFor="dm-reason"><NativeSelect id="dm-reason" value={f.reason} onChange={set("reason")}>{REASONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</NativeSelect></Field>
          <Field label="Claim from supplier (optional)" className="sm:col-span-2"><PartyPicker kind="suppliers" value={supplier} onChange={setSupplier} placeholder="No claim — write off" /></Field>
          {supplier && <Field label="Claim amount" htmlFor="dm-amt" hint="Empty: its cost"><Input id="dm-amt" inputMode="decimal" className="num" value={f.claim_amount} onChange={set("claim_amount")} /></Field>}
          <Field label="Date" htmlFor="dm-date"><Input id="dm-date" type="date" value={f.claim_date} onChange={set("claim_date")} /></Field>
          <Field label="Notes" htmlFor="dm-notes" className="sm:col-span-4"><Input id="dm-notes" value={f.notes} onChange={set("notes")} placeholder="e.g. 3 bottles leaking in carton 4 — photo sent to supplier" /></Field>
          <DialogFooter className="sm:col-span-4">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!product || !wh || !(Number(f.quantity) > 0) || (product.batch_tracking && !f.batch_no) || save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />} Write off{supplier ? " & claim" : ""}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Settle({ claim, onClose }: { claim: Claim; onClose: () => void }) {
  const [amount, setAmount] = useState(String(Number(claim.claim_amount)));
  const [ref, setRef] = useState("");
  const [on, setOn] = useState(todayISO());
  const save = useBizMutation<Record<string, unknown>>("POST", `/damage-claims/${claim.id}/settle`, { invalidate: [["damage-claims"]], success: "Credit recorded against the supplier", onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>{claim.party_name} credited {claim.number}</DialogTitle>
          <DialogDescription>The amount comes off what you owe {claim.party_name} and reduces the damage loss.</DialogDescription></DialogHeader>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Amount credited" htmlFor="st-amt"><Input id="st-amt" inputMode="decimal" className="num" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} /></Field>
          <Field label="Their credit note no." htmlFor="st-ref"><Input id="st-ref" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
          <Field label="Date" htmlFor="st-on"><Input id="st-on" type="date" value={on} onChange={(e) => setOn(e.target.value)} /></Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!(Number(amount) > 0) || save.isPending} onClick={() => save.mutate({ amount, reference: ref || null, on })}>{save.isPending && <Loader2Icon className="animate-spin" />} Record credit</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
