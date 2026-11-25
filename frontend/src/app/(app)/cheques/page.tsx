"use client";

import { ArrowDownLeftIcon, ArrowUpRightIcon, BanIcon, CheckCircle2Icon, Loader2Icon, PlusIcon, PrinterIcon, RulerIcon, Undo2Icon } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "@/components/data/confirm";
import { StaticTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { PartyPicker } from "@/components/pickers/party-picker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, openFile } from "@/lib/api";
import { date, money, todayISO } from "@/lib/format";
import type { DocumentSummary, Party } from "@/lib/types";

interface Cheque {
  id: string; direction: "in" | "out"; party_id: string; party_name: string | null; cheque_no: string; cheque_date: string;
  bank_name: string | null; amount: string; status: "pending" | "cleared" | "bounced" | "cancelled"; payment_id: string | null;
  cleared_on: string | null; bounced_on: string | null; bounce_reason: string | null; days_to_date: number;
  allocations: { document_id: string; number: string; amount: string }[]; payee_name: string | null;
}
interface Summary { pending: number; pending_amount: string; due_7d: number; due_7d_amount: string }
const STATUS: Record<Cheque["status"], ["default" | "warning" | "destructive" | "secondary", string]> = {
  pending: ["warning", "Pending"], cleared: ["default", "Cleared"], bounced: ["destructive", "Bounced"], cancelled: ["secondary", "Cancelled"],
};

export default function ChequesPage() {
  const { can, isOwner, base } = useBusiness();
  const manage = isOwner || can("payments.create");
  const [tab, setTab] = useState("pending");
  const [dir, setDir] = useState("");
  const list = useBizQuery<Cheque[]>(["cheques"], "/cheques", { status: tab === "all" ? undefined : tab, direction: dir || undefined });
  const summary = useBizQuery<{ in: Summary; out: Summary }>(["cheques", "summary"], "/cheques/summary");
  const [adding, setAdding] = useState<"in" | "out" | null>(null);
  const [clearing, setClearing] = useState<Cheque | null>(null);
  const [bouncing, setBouncing] = useState<Cheque | null>(null);
  const [cancelling, setCancelling] = useState<Cheque | null>(null);
  const [layout, setLayout] = useState(false);
  const inv = [["cheques"], ["payments"], ["documents"]];
  const bounce = useBizMutation<{ reason: string; on: string }>("POST", () => `/cheques/${bouncing?.id}/bounce`, { invalidate: inv, success: "Marked bounced; the bills are due again" });
  const cancel = useBizMutation<{ reason: string }>("POST", () => `/cheques/${cancelling?.id}/cancel`, { invalidate: inv, success: "Cheque cancelled" });
  const s = summary.data;

  return (
    <>
      <PageHeader title="Cheques" description="Post-dated cheques received and issued. They stay out of the books until the bank clears them; clearing records the receipt or payment."
        actions={manage && <>
          <Button onClick={() => setAdding("in")}><ArrowDownLeftIcon /> Cheque received</Button>
          <Button variant="outline" onClick={() => setAdding("out")}><ArrowUpRightIcon /> Cheque issued</Button>
          <Button variant="outline" onClick={() => setLayout(true)}><RulerIcon /> Print layout</Button>
        </>} />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:mb-6 sm:gap-4 lg:grid-cols-4">
        <StatCard label="Received, pending" icon={ArrowDownLeftIcon} loading={summary.isLoading} value={s ? money(s.in.pending_amount) : "—"} sub={s ? `${s.in.pending} cheques` : undefined} />
        <StatCard label="Received, due in 7 days" icon={ArrowDownLeftIcon} tone={s && s.in.due_7d ? "amber" : "green"} loading={summary.isLoading} value={s ? money(s.in.due_7d_amount) : "—"} sub={s ? `${s.in.due_7d} to deposit` : undefined} />
        <StatCard label="Issued, pending" icon={ArrowUpRightIcon} loading={summary.isLoading} value={s ? money(s.out.pending_amount) : "—"} sub={s ? `${s.out.pending} cheques` : undefined} />
        <StatCard label="Issued, due in 7 days" icon={ArrowUpRightIcon} tone={s && s.out.due_7d ? "amber" : "green"} loading={summary.isLoading} value={s ? money(s.out.due_7d_amount) : "—"} sub={s ? "keep the bank funded" : undefined} />
      </div>
      <Card className="overflow-hidden">
        <div className="flex flex-col gap-2 border-b p-3 sm:flex-row sm:items-center sm:justify-between">
          <Tabs value={tab} onValueChange={setTab}><TabsList>
            <TabsTrigger value="pending">Pending</TabsTrigger><TabsTrigger value="cleared">Cleared</TabsTrigger>
            <TabsTrigger value="bounced">Bounced</TabsTrigger><TabsTrigger value="all">All</TabsTrigger>
          </TabsList></Tabs>
          <NativeSelect className="sm:w-44" value={dir} onChange={(e) => setDir(e.target.value)} aria-label="Direction">
            <option value="">Received & issued</option><option value="in">Received</option><option value="out">Issued</option>
          </NativeSelect>
        </div>
        {list.error ? <ErrorState error={list.error} retry={() => list.refetch()} /> : list.isLoading ? <TableSkeleton /> : !list.data?.length ? (
          <EmptyState title="No cheques here" description="Record a post-dated cheque when you receive or issue it. You get a reminder on its date, and clearing it settles the bills." />
        ) : (
          <StaticTable rows={list.data} rowKey={(c) => c.id} columns={[
            { key: "party", header: "Party", mobile: "title", cell: (c) => (
              <div className="flex items-center gap-2"><span className={c.direction === "in" ? "text-primary" : "text-blue"}>{c.direction === "in" ? <ArrowDownLeftIcon className="size-4" /> : <ArrowUpRightIcon className="size-4" />}</span>
                <div><p className="font-medium">{c.party_name}</p><p className="text-xs text-muted-foreground">No. {c.cheque_no}{c.bank_name ? ` · ${c.bank_name}` : ""}{c.allocations.length ? ` · for ${c.allocations.map((a) => a.number).join(", ")}` : ""}</p></div></div>) },
            { key: "date", header: "Cheque date", cell: (c) => (
              <span>{date(c.cheque_date)}{c.status === "pending" && <span className={`block text-xs ${c.days_to_date <= 0 ? "font-medium text-amber-700 dark:text-warning" : "text-muted-foreground"}`}>
                {c.days_to_date > 0 ? `in ${c.days_to_date} day${c.days_to_date === 1 ? "" : "s"}` : c.days_to_date === 0 ? "today" : `${-c.days_to_date} days ago`}</span>}</span>) },
            { key: "status", header: "Status", cell: (c) => <span><Badge variant={STATUS[c.status][0]}>{STATUS[c.status][1]}</Badge>
              {c.cleared_on && c.status === "cleared" && <span className="block text-xs text-muted-foreground">{date(c.cleared_on)}</span>}
              {c.bounce_reason && <span className="block max-w-48 truncate text-xs text-muted-foreground">{c.bounce_reason}</span>}</span> },
            { key: "amount", header: "Amount", align: "right", mobile: "aside", cell: (c) => <span className="num font-medium">{money(c.amount)}</span> },
            { key: "act", header: "", mobile: "row", cell: (c) => (
              <div className="flex flex-wrap justify-end gap-1">
                {c.direction === "out" && c.status === "pending" && <Button size="sm" variant="ghost" onClick={() => openFile(`${base}/cheques/${c.id}/print`)}><PrinterIcon /> Print</Button>}
                {manage && c.status === "pending" && <Button size="sm" variant="outline" onClick={() => setClearing(c)}><CheckCircle2Icon /> Cleared</Button>}
                {manage && (c.status === "pending" || c.status === "cleared") && <Button size="sm" variant="ghost" className="text-destructive" onClick={() => setBouncing(c)}><Undo2Icon /> Bounced</Button>}
                {manage && c.status === "pending" && <Button size="sm" variant="ghost" onClick={() => setCancelling(c)} aria-label="Cancel cheque"><BanIcon /></Button>}
              </div>) },
          ]} />
        )}
      </Card>
      {adding && <ChequeDialog direction={adding} onClose={() => setAdding(null)} />}
      {clearing && <ClearDialog cheque={clearing} onClose={() => setClearing(null)} />}
      <ConfirmDialog open={!!bouncing} onOpenChange={(v) => !v && setBouncing(null)} destructive reason confirm="Mark bounced"
        title={`Cheque ${bouncing?.cheque_no} bounced?`}
        description={bouncing?.status === "cleared" ? "Its receipt / payment is cancelled, so the bills it settled are due again." : "The cheque is closed as bounced; nothing was in the books yet."}
        onConfirm={(reason) => bounce.mutateAsync({ reason, on: todayISO() })} />
      <ConfirmDialog open={!!cancelling} onOpenChange={(v) => !v && setCancelling(null)} reason confirm="Cancel cheque"
        title={`Cancel cheque ${cancelling?.cheque_no}?`} description="For a cheque returned or torn up before presenting." onConfirm={(reason) => cancel.mutateAsync({ reason })} />
      {layout && <LayoutDialog onClose={() => setLayout(false)} />}
    </>
  );
}

function ChequeDialog({ direction, onClose }: { direction: "in" | "out"; onClose: () => void }) {
  const { base } = useBusiness();
  const [party, setParty] = useState<Party | null>(null);
  const [f, setF] = useState({ cheque_no: "", cheque_date: todayISO(), bank_name: "", amount: "", account_id: "", notes: "" });
  const [bills, setBills] = useState<DocumentSummary[]>([]);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const banks = useBizQuery<{ id: string; code: string; name: string; subtype: string }[]>(["accounts"], "/accounting/accounts");
  const save = useBizMutation<Record<string, unknown>>("POST", "/cheques", { invalidate: [["cheques"]], success: "Cheque recorded", onSuccess: onClose });
  const choose = async (p: Party | null) => {
    setParty(p); setPicked({});
    if (!p) return setBills([]);
    const out = await api<{ documents: DocumentSummary[] }>(`${base}/parties/${p.id}/outstanding`);
    setBills(out.documents.filter((d) => (direction === "in" ? ["sales_invoice", "debit_note"] : ["purchase_invoice"]).includes(d.doc_type)));
  };
  const chosen = bills.filter((b) => picked[b.id]);
  const total = chosen.reduce((s, b) => s + Number(b.balance_due), 0);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF((x) => ({ ...x, [k]: e.target.value }));
  const allocations = allocate(chosen, Number(f.amount || 0));
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>{direction === "in" ? "Cheque received" : "Cheque issued"}</DialogTitle>
          <DialogDescription>Nothing is posted until you mark it cleared.</DialogDescription></DialogHeader>
        <form className="grid gap-4 sm:grid-cols-4" onSubmit={(e) => { e.preventDefault(); save.mutate({ direction, party_id: party?.id, ...f, bank_name: f.bank_name || null,
          account_id: f.account_id || null, notes: f.notes || null, allocations }); }}>
          <Field label={direction === "in" ? "From customer" : "To supplier"} className="sm:col-span-2"><PartyPicker kind={direction === "in" ? "customers" : "suppliers"} value={party} onChange={choose} /></Field>
          <Field label="Cheque no." required htmlFor="cq-no"><Input id="cq-no" inputMode="numeric" value={f.cheque_no} onChange={set("cheque_no")} /></Field>
          <Field label="Cheque date" required htmlFor="cq-date"><Input id="cq-date" type="date" value={f.cheque_date} onChange={set("cheque_date")} /></Field>
          <Field label={direction === "in" ? "Their bank" : "Bank"} htmlFor="cq-bank"><Input id="cq-bank" value={f.bank_name} onChange={set("bank_name")} placeholder="SBI, HDFC…" /></Field>
          <Field label="Amount" required htmlFor="cq-amt"><Input id="cq-amt" inputMode="decimal" className="num" value={f.amount} onChange={(e) => setF((x) => ({ ...x, amount: e.target.value.replace(/[^\d.]/g, "") }))} /></Field>
          <Field label={direction === "in" ? "Deposit into" : "Drawn on"} htmlFor="cq-acc" className="sm:col-span-2">
            <NativeSelect id="cq-acc" value={f.account_id} onChange={set("account_id")}><option value="">Default bank account</option>
              {banks.data?.filter((a) => a.subtype === "bank").map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</NativeSelect>
          </Field>
          {party && (
            <div className="rounded-lg border sm:col-span-4">
              <div className="flex justify-between border-b px-3 py-2 text-sm"><span className="font-medium">Bills it pays</span>
                {chosen.length > 0 && <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={() => setF((x) => ({ ...x, amount: total.toFixed(2) }))}>Use total {money(total.toFixed(2))}</Button>}</div>
              {bills.length === 0 ? <p className="p-3 text-sm text-muted-foreground">No open bills — it will be an advance.</p> : (
                <div className="max-h-48 divide-y overflow-y-auto">{bills.map((b) => (
                  <label key={b.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                    <input type="checkbox" className="size-4 accent-primary" checked={!!picked[b.id]} onChange={(e) => setPicked((p) => ({ ...p, [b.id]: e.target.checked }))} />
                    <span className="flex-1"><b>{b.number}</b> <span className="text-muted-foreground">· {date(b.doc_date)}</span></span>
                    <span className="num">{money(b.balance_due)}</span>
                  </label>))}</div>)}
            </div>
          )}
          <Field label="Notes" htmlFor="cq-notes" className="sm:col-span-4"><Input id="cq-notes" value={f.notes} onChange={set("notes")} /></Field>
          <DialogFooter className="sm:col-span-4">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!party || !f.cheque_no || !(Number(f.amount) > 0) || save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />} Save cheque</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Spread the cheque over the chosen bills, oldest first, up to each bill's balance. */
function allocate(bills: DocumentSummary[], amount: number) {
  const out: { document_id: string; amount: string }[] = [];
  let left = amount;
  for (const b of bills) {
    const a = Math.min(left, Number(b.balance_due));
    if (a > 0) out.push({ document_id: b.id, amount: a.toFixed(2) });
    left -= a;
  }
  return out;
}

function ClearDialog({ cheque, onClose }: { cheque: Cheque; onClose: () => void }) {
  const [on, setOn] = useState(cheque.cheque_date > todayISO() ? cheque.cheque_date : todayISO());
  const clear = useBizMutation<{ on: string }>("POST", `/cheques/${cheque.id}/clear`, {
    invalidate: [["cheques"], ["payments"], ["documents"]], success: "Cleared: the receipt / payment is recorded", onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Cheque {cheque.cheque_no} cleared</DialogTitle>
          <DialogDescription>{money(cheque.amount)} {cheque.direction === "in" ? "from" : "to"} {cheque.party_name}{cheque.allocations.length ? `, settling ${cheque.allocations.map((a) => a.number).join(", ")}` : " (kept as an advance)"}.</DialogDescription></DialogHeader>
        <Field label="Cleared on" htmlFor="clr-on"><Input id="clr-on" type="date" value={on} min={cheque.cheque_date} onChange={(e) => setOn(e.target.value)} /></Field>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={clear.isPending} onClick={() => clear.mutate({ on })}>{clear.isPending && <Loader2Icon className="animate-spin" />} Record as cleared</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const LAYOUT_FIELDS: [string, string][] = [
  ["offset_x", "Shift right (mm)"], ["offset_y", "Shift down (mm)"], ["font_mm", "Text size (mm)"],
  ["date_x", "Date: from left"], ["date_y", "Date: from top"], ["date_pitch", "Date: box width"],
  ["payee_x", "Payee: from left"], ["payee_y", "Payee: from top"], ["words_x", "Words line 1: left"], ["words_y", "Words line 1: top"],
  ["words2_x", "Words line 2: left"], ["words2_y", "Words line 2: top"], ["words_width", "Words line 1: width"],
  ["amount_x", "Amount box: left"], ["amount_y", "Amount box: top"],
];

function LayoutDialog({ onClose }: { onClose: () => void }) {
  const { base, can, isOwner } = useBusiness();
  const q = useBizQuery<{ layout: Record<string, number | boolean> }>(["cheque-layout"], "/cheque-layout");
  const [edit, setEdit] = useState<Record<string, number | boolean> | null>(null);
  const cur = edit ?? q.data?.layout;
  const save = useBizMutation<{ layout: Record<string, number | boolean> }>("PUT", "/cheque-layout", { invalidate: [["cheque-layout"]], success: "Layout saved" });
  const editable = isOwner || can("business.settings");
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>Cheque print layout</DialogTitle>
          <DialogDescription>Positions in millimetres on a standard CTS-2010 cheque (203 × 92 mm). Print a test on plain paper, hold it against a cheque leaf, and adjust. Most printers need only the two “Shift” values.</DialogDescription></DialogHeader>
        {!cur ? <TableSkeleton rows={3} /> : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {LAYOUT_FIELDS.map(([k, label]) => (
              <Field key={k} label={label} htmlFor={`lay-${k}`}><Input id={`lay-${k}`} inputMode="decimal" className="num" disabled={!editable} value={String(cur[k] ?? "")}
                onChange={(e) => setEdit({ ...cur, [k]: Number(e.target.value.replace(/[^\d.-]/g, "")) || 0 })} /></Field>
            ))}
            <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" className="size-4 accent-primary" disabled={!editable} checked={!!cur.ac_payee}
              onChange={(e) => setEdit({ ...cur, ac_payee: e.target.checked })} /> Print “A/C PAYEE” crossing</label>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => openFile(`${base}/cheque-layout/sample`)}><PrinterIcon /> Test print</Button>
          {editable && <Button disabled={!edit || save.isPending} onClick={() => edit && save.mutate({ layout: edit }, { onSuccess: () => setEdit(null) })}>Save layout</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
