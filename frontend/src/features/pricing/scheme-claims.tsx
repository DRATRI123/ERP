"use client";

import { CheckCircle2Icon, GiftIcon, HandCoinsIcon, Loader2Icon, SendIcon } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "@/components/data/confirm";
import { StaticTable } from "@/components/data/data-table";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { PeriodPicker, PRESETS, type Period } from "@/components/pickers/period";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money, qty, todayISO } from "@/lib/format";

interface Group {
  party_id: string; party_name: string; amount: string; free_quantity: string;
  lines: { line_id: string; number: string; doc_date: string; customer: string | null; scheme: string; description: string; free: boolean; quantity: string; value: string }[];
}
interface Claim {
  id: string; number: string; claim_date: string; party_name: string | null; date_from: string; date_to: string; lines: number;
  free_quantity: string; claim_amount: string; settled_amount: string; status: "open" | "settled" | "rejected"; settlement_ref: string | null;
}
const STATUS: Record<Claim["status"], ["warning" | "default" | "destructive", string]> = {
  open: ["warning", "Claimed"], settled: ["default", "Settled"], rejected: ["destructive", "Rejected"],
};

/** Free goods and scheme discounts given on a manufacturer's behalf, claimed back from them. */
export function SchemeClaims() {
  const { can, isOwner } = useBusiness();
  const edit = isOwner || can("purchases.edit");
  const [period, setPeriod] = useState<Period>(() => PRESETS.last_month!());
  const due = useBizQuery<{ parties: Group[]; total: string }>(["scheme-claims", "claimable", period], "/scheme-claims/claimable",
    { date_from: period.from, date_to: period.to });
  const list = useBizQuery<Claim[]>(["scheme-claims"], "/scheme-claims");
  const [open, setOpen] = useState<Group | null>(null);
  const [settling, setSettling] = useState<Claim | null>(null);
  const [rejecting, setRejecting] = useState<Claim | null>(null);
  const create = useBizMutation<Record<string, unknown>>("POST", "/scheme-claims", { invalidate: [["scheme-claims"]], success: "Claim made — send it to the manufacturer", onSuccess: () => setOpen(null) });
  const reject = useBizMutation<{ reason: string }>("POST", () => `/scheme-claims/${rejecting?.id}/reject`, { invalidate: [["scheme-claims"]], success: "Claim marked rejected" });
  const waiting = (list.data ?? []).filter((c) => c.status === "open");
  const got = (list.data ?? []).filter((c) => c.status === "settled").reduce((a, c) => a + Number(c.settled_amount), 0);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-3">
        <StatCard label="To claim (this period)" icon={GiftIcon} tone={Number(due.data?.total) ? "amber" : "green"} loading={due.isLoading} value={due.data ? money(due.data.total) : "—"} sub={`${due.data?.parties.length ?? 0} manufacturers`} />
        <StatCard label="Waiting for credit" icon={SendIcon} loading={list.isLoading} value={money(waiting.reduce((a, c) => a + Number(c.claim_amount), 0).toFixed(2))} sub={`${waiting.length} open claims`} />
        <StatCard label="Recovered" icon={HandCoinsIcon} loading={list.isLoading} value={money(got.toFixed(2))} />
      </div>
      <Card className="overflow-hidden">
        <CardHeader className="flex flex-col gap-3 border-b sm:flex-row sm:items-center sm:justify-between">
          <div><CardTitle>Not claimed yet</CardTitle><p className="text-sm text-muted-foreground">Schemes that name who pays for them. Free goods at your purchase price, discounts at the amount given.</p></div>
          <PeriodPicker value={period} onChange={setPeriod} />
        </CardHeader>
        {due.error ? <ErrorState error={due.error} retry={() => due.refetch()} /> : due.isLoading ? <TableSkeleton /> : !due.data?.parties.length ? (
          <EmptyState icon={GiftIcon} title="Nothing to claim" description="Set “Claim it from” on a scheme. Free goods and discounts given under it on sales bills collect here." />
        ) : (
          <StaticTable rows={due.data.parties} rowKey={(g) => g.party_id} columns={[
            { key: "p", header: "Manufacturer", mobile: "title", cell: (g) => <div><p className="font-medium">{g.party_name}</p><p className="text-xs text-muted-foreground">{g.lines.length} bill lines · {qty(g.free_quantity)} free</p></div> },
            { key: "a", header: "Claim value", align: "right", mobile: "aside", cell: (g) => <span className="num font-medium">{money(g.amount)}</span> },
            { key: "x", header: "", mobile: "row", cell: (g) => <div className="flex justify-end"><Button size="sm" variant="outline" onClick={() => setOpen(g)}>View & claim</Button></div> },
          ]} />
        )}
      </Card>
      <Card className="overflow-hidden">
        <CardHeader className="border-b"><CardTitle>Claims made</CardTitle></CardHeader>
        {list.error ? <ErrorState error={list.error} retry={() => list.refetch()} /> : list.isLoading ? <TableSkeleton /> : !list.data?.length ? (
          <EmptyState icon={SendIcon} title="No claims yet" description="Claims you make appear here until the manufacturer sends a credit note." />
        ) : (
          <StaticTable rows={list.data} rowKey={(c) => c.id} columns={[
            { key: "n", header: "Claim", mobile: "title", cell: (c) => <div><p className="font-medium">{c.party_name}</p><p className="text-xs text-muted-foreground">{c.number} · {date(c.date_from)} – {date(c.date_to)} · {c.lines} lines</p></div> },
            { key: "s", header: "Status", cell: (c) => <span><Badge variant={STATUS[c.status][0]}>{STATUS[c.status][1]}</Badge>{c.settlement_ref && <span className="block text-xs text-muted-foreground">CN {c.settlement_ref}</span>}</span> },
            { key: "a", header: "Claimed / credited", align: "right", mobile: "aside", cell: (c) => <span className="num font-medium">{money(c.status === "settled" ? c.settled_amount : c.claim_amount)}</span> },
            ...(edit ? [{ key: "x", header: "", mobile: "row" as const, cell: (c: Claim) => c.status === "open" && (
              <div className="flex justify-end gap-1"><Button size="sm" variant="outline" onClick={() => setSettling(c)}><CheckCircle2Icon /> Credit received</Button>
                <Button size="sm" variant="ghost" className="text-destructive" onClick={() => setRejecting(c)}>Rejected</Button></div>) }] : []),
          ]} />
        )}
      </Card>
      {open && (
        <Dialog open onOpenChange={(v) => !v && setOpen(null)}>
          <DialogContent wide>
            <DialogHeader><DialogTitle>Claim from {open.party_name}</DialogTitle>
              <DialogDescription>{date(period.from)} – {date(period.to)} · {money(open.amount)}. Print or share this list with the claim.</DialogDescription></DialogHeader>
            <div className="max-h-[50vh] overflow-auto rounded-md border">
              <StaticTable rows={open.lines} rowKey={(l) => l.line_id} columns={[
                { key: "b", header: "Bill", mobile: "title", cell: (l) => <div><p className="font-medium">{l.description}</p><p className="text-xs text-muted-foreground">{l.number} · {date(l.doc_date)} · {l.customer ?? "Walk-in"} · {l.scheme}</p></div> },
                { key: "q", header: "Qty", align: "right", cell: (l) => <span className="num">{qty(l.quantity)}{l.free ? " free" : ""}</span> },
                { key: "v", header: "Value", align: "right", mobile: "aside", cell: (l) => <span className="num">{money(l.value)}</span> },
              ]} />
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(null)}>Close</Button>
              {edit && <Button disabled={create.isPending} onClick={() => create.mutate({ party_id: open.party_id, date_from: period.from, date_to: period.to })}>
                {create.isPending && <Loader2Icon className="animate-spin" />} Make claim of {money(open.amount)}</Button>}
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
      {settling && <Settle claim={settling} onClose={() => setSettling(null)} />}
      <ConfirmDialog open={!!rejecting} onOpenChange={(v) => !v && setRejecting(null)} destructive reason confirm="Mark rejected"
        title={`Manufacturer rejected ${rejecting?.number}?`} description="Its bill lines become claimable again, so you can claim them with corrected figures." onConfirm={(reason) => reject.mutateAsync({ reason })} />
    </div>
  );
}

function Settle({ claim, onClose }: { claim: Claim; onClose: () => void }) {
  const [amount, setAmount] = useState(String(Number(claim.claim_amount)));
  const [ref, setRef] = useState("");
  const [on, setOn] = useState(todayISO());
  const save = useBizMutation<Record<string, unknown>>("POST", `/scheme-claims/${claim.id}/settle`, { invalidate: [["scheme-claims"]], success: "Credit recorded", onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>{claim.party_name} credited {claim.number}</DialogTitle>
          <DialogDescription>The amount comes off what you owe them and is booked as scheme income.</DialogDescription></DialogHeader>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Amount credited" htmlFor="sc-amt"><Input id="sc-amt" inputMode="decimal" className="num" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} /></Field>
          <Field label="Their credit note no." htmlFor="sc-ref"><Input id="sc-ref" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
          <Field label="Date" htmlFor="sc-on"><Input id="sc-on" type="date" value={on} onChange={(e) => setOn(e.target.value)} /></Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!(Number(amount) > 0) || save.isPending} onClick={() => save.mutate({ amount, reference: ref || null, on })}>{save.isPending && <Loader2Icon className="animate-spin" />} Record credit</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
