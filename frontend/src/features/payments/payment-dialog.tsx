"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { PartyPicker } from "@/components/pickers/party-picker";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";
import { date, dec, decStr, money, todayISO } from "@/lib/format";
import type { DocumentSummary, Party } from "@/lib/types";

export function PaymentDialog({ open, onOpenChange, initial }: {
  open: boolean; onOpenChange: (v: boolean) => void; initial?: { direction?: "in" | "out"; partyId?: string; docId?: string };
}) {
  const { base, id: bizId } = useBusiness();
  const qc = useQueryClient();
  const [direction, setDirection] = useState<"in" | "out">(initial?.direction ?? "in");
  const [party, setParty] = useState<Party | null>(null);
  const [mode, setMode] = useState("upi");
  const [amount, setAmount] = useState("");
  const [payDate, setPayDate] = useState(todayISO());
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");
  const [alloc, setAlloc] = useState<Record<string, string>>({});
  const kind = direction === "in" ? "customers" : "suppliers";

  // Prefill from a document ("Record payment" on an invoice).
  useEffect(() => {
    if (!open || !initial?.partyId) return;
    api<Party>(`${base}/${initial.direction === "out" ? "suppliers" : "customers"}/${initial.partyId}`).then(setParty).catch(() => undefined);
  }, [open, initial?.partyId, initial?.direction, base]);
  const outstanding = useQuery({
    queryKey: ["biz", bizId, "outstanding", party?.id], enabled: open && !!party,
    queryFn: () => api<{ documents: DocumentSummary[]; outstanding: string }>(`${base}/parties/${party!.id}/outstanding`),
  });
  const docs = (outstanding.data?.documents ?? []).filter((d) => ["sales_invoice", "debit_note", "purchase_invoice"].includes(d.doc_type));
  useEffect(() => {
    if (initial?.docId && docs.length) {
      const d = docs.find((x) => x.id === initial.docId);
      if (d && !amount) { setAmount(decStr(dec(d.balance_due))); setAlloc({ [d.id]: decStr(dec(d.balance_due)) }); }
    }
  }, [docs, initial?.docId, amount]);

  function autoAllocate(total: string) {
    let left = dec(total || "0");
    const next: Record<string, string> = {};
    for (const d of [...docs].sort((a, b) => (a.due_date ?? a.doc_date).localeCompare(b.due_date ?? b.doc_date))) {
      if (left <= 0n) break;
      const take = dec(d.balance_due) < left ? dec(d.balance_due) : left;
      next[d.id] = decStr(take);
      left -= take;
    }
    setAlloc(next);
  }
  const allocated = Object.values(alloc).reduce((s, v) => s + dec(v || "0"), 0n);
  const unallocated = dec(amount || "0") - allocated;

  const save = useMutation({
    mutationFn: () => api<{ number: string }>(`${base}/payments`, { body: {
      direction, party_id: party?.id, mode, amount, payment_date: payDate, reference: reference || undefined, notes: notes || undefined,
      allocations: Object.entries(alloc).filter(([, v]) => Number(v) > 0).map(([document_id, a]) => ({ document_id, amount: a })),
    } }),
    onSuccess: (p) => { toast.success(`${direction === "in" ? "Receipt" : "Payment"} ${p.number} recorded`); qc.invalidateQueries({ queryKey: ["biz", bizId] }); onOpenChange(false); setAmount(""); setAlloc({}); },
    onError: (e: ApiError) => toast.error(e.message),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>{direction === "in" ? "Receive payment" : "Make payment"}</DialogTitle></DialogHeader>
        <div className="grid gap-4 sm:grid-cols-4">
          <Field label="Type"><NativeSelect value={direction} onChange={(e) => { setDirection(e.target.value as "in" | "out"); setParty(null); setAlloc({}); }}>
            <option value="in">Received from customer</option><option value="out">Paid to supplier</option></NativeSelect></Field>
          <Field label={direction === "in" ? "Customer" : "Supplier"} className="sm:col-span-2"><PartyPicker kind={kind} value={party} onChange={(p) => { setParty(p); setAlloc({}); }} /></Field>
          <Field label="Date"><Input type="date" value={payDate} onChange={(e) => setPayDate(e.target.value)} /></Field>
          <Field label="Mode"><NativeSelect value={mode} onChange={(e) => setMode(e.target.value)}>
            {["upi", "cash", "card", "bank_transfer", "cheque", "other"].map((m) => <option key={m} value={m}>{m.replace("_", " ").toUpperCase()}</option>)}</NativeSelect></Field>
          <Field label="Amount"><Input value={amount} inputMode="decimal" className="num" onChange={(e) => { const v = e.target.value.replace(/[^\d.]/g, ""); setAmount(v); autoAllocate(v); }} autoFocus /></Field>
          <Field label="Reference / UTR" className="sm:col-span-2"><Input value={reference} onChange={(e) => setReference(e.target.value)} /></Field>
        </div>
        {party && (
          <div className="rounded-lg border">
            <div className="flex items-center justify-between border-b px-3 py-2 text-sm">
              <span className="font-medium">Settle against</span>
              <span className="text-muted-foreground">{money(outstanding.data?.outstanding)} outstanding</span>
            </div>
            {outstanding.isLoading ? <p className="p-3 text-sm text-muted-foreground">Loading…</p> : docs.length === 0 ? <p className="p-3 text-sm text-muted-foreground">No open documents — the payment is kept as an advance.</p> : (
              <div className="max-h-56 divide-y overflow-y-auto">
                {docs.map((d) => (
                  <div key={d.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                    <span className="flex-1"><span className="font-medium">{d.number}</span> <span className="text-muted-foreground">· {date(d.doc_date)} · due {money(d.balance_due)}</span></span>
                    <Input value={alloc[d.id] ?? ""} placeholder="0.00" className="num h-8 w-28 text-right" aria-label={`Amount for ${d.number}`}
                      onChange={(e) => setAlloc((a) => ({ ...a, [d.id]: e.target.value.replace(/[^\d.]/g, "") }))} />
                  </div>
                ))}
              </div>
            )}
            {unallocated !== 0n && amount && <p className="border-t px-3 py-2 text-xs text-muted-foreground">{unallocated > 0n ? `${money(decStr(unallocated))} kept as advance` : `Allocated ${money(decStr(-unallocated))} more than the amount`}</p>}
          </div>
        )}
        <Field label="Notes"><Textarea value={notes} onChange={(e) => setNotes(e.target.value)} className="min-h-12" /></Field>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={!amount || Number(amount) <= 0 || unallocated < 0n || save.isPending} onClick={() => save.mutate()}>
            {save.isPending && <Loader2Icon className="animate-spin" />} Record {money(amount || "0")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
