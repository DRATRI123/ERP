"use client";

import { CameraIcon, CheckIcon, Loader2Icon, PaperclipIcon, PiggyBankIcon, PlusIcon, ReceiptIcon, XIcon } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { type Column, DataTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { ApiError } from "@/lib/api";
import { date, money, todayISO } from "@/lib/format";
import { download, uploadFile } from "@/features/files/files";

interface Claim {
  id: string; expense_date: string; account_name: string | null; amount: string; description: string; file_id: string | null;
  status: "submitted" | "approved" | "paid" | "rejected"; claimant_name: string | null; decision_note: string | null; created_at: string;
}
interface PayFrom { id: string; code: string; name: string; petty: boolean }
const STATUS: Record<Claim["status"], ["warning" | "blue" | "default" | "destructive", string]> = {
  submitted: ["warning", "Waiting"], approved: ["blue", "Approved — to pay"], paid: ["default", "Paid"], rejected: ["destructive", "Rejected"],
};

export default function ClaimsPage() {
  const { base, can, isOwner } = useBusiness();
  const approver = isOwner || can("expenses.manage");
  const claimant = isOwner || can("expenses.claim");
  const [filter, setFilter] = useState(approver ? "submitted" : "");
  const [newClaim, setNewClaim] = useState(false);
  const [deciding, setDeciding] = useState<Claim | null>(null);
  const [topUp, setTopUp] = useState(false);
  const petty = useBizQuery<{ balance: string; claims_waiting: string; account_id: string }>(["petty-cash"], "/petty-cash", undefined, { enabled: approver });
  if (!claimant) return <Card><EmptyState title="Not available" description="Needs expenses.claim." /></Card>;
  const cols: Column<Claim>[] = [
    { key: "d", header: "Date", cell: (c) => date(c.expense_date) },
    ...(approver ? [{ key: "w", header: "By", cell: (c: Claim) => c.claimant_name ?? "—" }] : []),
    { key: "f", header: "For", cell: (c) => <div><p className="font-medium">{c.description}</p><p className="text-xs text-muted-foreground">{c.account_name}{c.decision_note ? ` · ${c.decision_note}` : ""}</p></div> },
    { key: "b", header: "Bill", cell: (c) => c.file_id ? <Button size="icon" variant="ghost" aria-label="Open bill" onClick={(e) => { e.stopPropagation(); download(base, { id: c.file_id!, name: "bill" }); }}><PaperclipIcon /></Button> : <span className="text-xs text-muted-foreground">—</span> },
    { key: "a", header: "Amount", align: "right", cell: (c) => <span className="num font-medium">{money(c.amount)}</span> },
    { key: "s", header: "Status", cell: (c) => <Badge variant={STATUS[c.status][0]}>{STATUS[c.status][1]}</Badge> },
  ];
  return (
    <>
      <PageHeader title="Expense claims" description={approver ? "Staff claims with the bill photo — approve and pay from petty cash in one step." : "Spent your own money on the business? Claim it here with a photo of the bill."}
        actions={<Button onClick={() => setNewClaim(true)}><PlusIcon /> New claim</Button>} />
      {approver && <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <StatCard label="Petty cash" icon={PiggyBankIcon} tone="blue" loading={petty.isLoading} value={petty.data ? money(petty.data.balance) : "—"}
          sub={<button type="button" className="text-blue hover:underline" onClick={() => setTopUp(true)}>Top up</button>} />
        <StatCard label="Claims waiting" icon={ReceiptIcon} tone={petty.data && Number(petty.data.claims_waiting) > 0 ? "amber" : "green"} loading={petty.isLoading}
          value={petty.data ? money(petty.data.claims_waiting) : "—"} />
      </div>}
      <DataTable<Claim> queryKey={["claims"]} path="/claims" columns={cols} searchParam={null} filters={{ status_: filter || undefined }}
        onRowClick={approver ? (c) => c.status !== "paid" && c.status !== "rejected" && setDeciding(c) : undefined}
        toolbar={<NativeSelect className="w-44" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Status">
          <option value="">All claims</option>{Object.entries(STATUS).map(([k, [, l]]) => <option key={k} value={k}>{l}</option>)}</NativeSelect>}
        empty={<EmptyState icon={ReceiptIcon} title="No claims" description={approver ? "Nothing waiting for you." : "Your claims and their status appear here."} />} />
      {newClaim && <ClaimDialog onClose={() => setNewClaim(false)} />}
      {deciding && <DecideDialog claim={deciding} onClose={() => setDeciding(null)} />}
      {topUp && <TopUpDialog onClose={() => setTopUp(false)} />}
    </>
  );
}

function ClaimDialog({ onClose }: { onClose: () => void }) {
  const { base } = useBusiness();
  const heads = useBizQuery<{ id: string; code: string; name: string }[]>(["claims", "heads"], "/claims/heads");
  const [d, setD] = useState({ expense_date: todayISO(), account_id: "", amount: "", description: "" });
  const [file, setFile] = useState<{ id: string; name: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  const save = useBizMutation<Record<string, unknown>>("POST", "/claims", { invalidate: [["claims"], ["petty-cash"]], success: "Claim submitted", onSuccess: onClose });
  const head = d.account_id || heads.data?.[0]?.id || "";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>New expense claim</DialogTitle><DialogDescription>Add a photo of the bill — it speeds up approval.</DialogDescription></DialogHeader>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); save.mutate({ ...d, account_id: head, file_id: file?.id }); }}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Date" htmlFor="cl-date"><Input id="cl-date" type="date" max={todayISO()} value={d.expense_date} onChange={(e) => setD({ ...d, expense_date: e.target.value })} /></Field>
            <Field label="Amount (₹)" htmlFor="cl-amt"><Input id="cl-amt" inputMode="decimal" value={d.amount} onChange={(e) => setD({ ...d, amount: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
          </div>
          <Field label="Type of expense" htmlFor="cl-head"><NativeSelect id="cl-head" value={head} onChange={(e) => setD({ ...d, account_id: e.target.value })}>
            {heads.data?.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}</NativeSelect></Field>
          <Field label="What for" htmlFor="cl-desc"><Textarea id="cl-desc" rows={2} value={d.description} maxLength={300} onChange={(e) => setD({ ...d, description: e.target.value })} placeholder="Auto fare to supplier for urgent stock" /></Field>
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" size="sm" disabled={uploading} onClick={() => ref.current?.click()}>
              {uploading ? <Loader2Icon className="animate-spin" /> : <CameraIcon />} {file ? "Replace bill" : "Bill photo"}</Button>
            {file && <span className="truncate text-sm text-muted-foreground">{file.name}</span>}
            <input ref={ref} type="file" hidden accept="image/*,.pdf" capture="environment" onChange={async (e) => {
              const f = e.target.files?.[0]; e.target.value = "";
              if (!f) return;
              setUploading(true);
              try { const up = await uploadFile(base, f, { folder: "/Claims", tags: "claim" }); setFile({ id: up.id, name: f.name }); }
              catch (err) { toast.error(err instanceof ApiError ? err.message : "Upload failed"); } finally { setUploading(false); }
            }} />
          </div>
          <DialogFooter><Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!(Number(d.amount) > 0) || d.description.trim().length < 3 || !head || save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />}Submit claim</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DecideDialog({ claim, onClose }: { claim: Claim; onClose: () => void }) {
  const from = useBizQuery<PayFrom[]>(["claims", "pay-from"], "/claims/pay-from");
  const [note, setNote] = useState("");
  const [payFrom, setPayFrom] = useState("");
  const src = payFrom || from.data?.find((a) => a.petty)?.id || from.data?.[0]?.id || "";
  const opts = { invalidate: [["claims"], ["petty-cash"]], onSuccess: onClose };
  const approve = useBizMutation<Record<string, unknown>>("POST", `/claims/${claim.id}/approve`, { ...opts, success: "Approved and paid" });
  const pay = useBizMutation<Record<string, unknown>>("POST", `/claims/${claim.id}/pay`, { ...opts, success: "Paid" });
  const reject = useBizMutation<Record<string, unknown>>("POST", `/claims/${claim.id}/reject`, { ...opts, success: "Rejected" });
  const waiting = claim.status === "submitted";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>{money(claim.amount)} — {claim.claimant_name}</DialogTitle>
          <DialogDescription>{date(claim.expense_date)} · {claim.account_name} · {claim.description}</DialogDescription></DialogHeader>
        <div className="grid gap-4">
          <Field label="Pay from" htmlFor="dc-from"><NativeSelect id="dc-from" value={src} onChange={(e) => setPayFrom(e.target.value)}>
            {from.data?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</NativeSelect></Field>
          {waiting && <Field label="Note" htmlFor="dc-note" hint="Required to reject"><Input id="dc-note" value={note} onChange={(e) => setNote(e.target.value)} /></Field>}
        </div>
        <DialogFooter>
          {waiting ? <>
            <Button variant="outline" className="text-destructive" disabled={note.trim().length < 3 || reject.isPending} onClick={() => reject.mutate({ note })}><XIcon /> Reject</Button>
            <Button disabled={!src || approve.isPending} onClick={() => approve.mutate({ note: note || undefined, paid_from_account_id: src })}><CheckIcon /> Approve & pay</Button>
          </> : <Button disabled={!src || pay.isPending} onClick={() => pay.mutate({ paid_from_account_id: src })}><CheckIcon /> Pay now</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TopUpDialog({ onClose }: { onClose: () => void }) {
  const from = useBizQuery<PayFrom[]>(["claims", "pay-from"], "/claims/pay-from");
  const [amount, setAmount] = useState("");
  const [src, setSrc] = useState("");
  const options = (from.data ?? []).filter((a) => !a.petty);
  const account = src || options[0]?.id || "";
  const save = useBizMutation<Record<string, unknown>>("POST", "/petty-cash/top-up", { invalidate: [["petty-cash"]], success: "Petty cash topped up", onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Top up petty cash</DialogTitle></DialogHeader>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); save.mutate({ from_account_id: account, amount }); }}>
          <Field label="From" htmlFor="tu-from"><NativeSelect id="tu-from" value={account} onChange={(e) => setSrc(e.target.value)}>{options.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</NativeSelect></Field>
          <Field label="Amount (₹)" htmlFor="tu-amt"><Input id="tu-amt" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} /></Field>
          <DialogFooter><Button type="button" variant="outline" onClick={onClose}>Cancel</Button><Button type="submit" disabled={!(Number(amount) > 0) || !account || save.isPending}>Top up</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

