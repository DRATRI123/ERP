"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, PlusIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";
import { dec, decStr, money, todayISO } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface Account { id: string; code: string; name: string; type: string; subtype: string; system_key: string | null; is_active: boolean; balance?: string | null; parent_id: string | null }
interface Row { key: string; account_id: string; debit: string; credit: string; description: string }
const blank = (): Row => ({ key: Math.random().toString(36).slice(2), account_id: "", debit: "", credit: "", description: "" });

/** Manual journal: must balance (total debit = total credit) before it can be posted. */
export function JournalDialog({ open, onOpenChange, accounts }: { open: boolean; onOpenChange: (v: boolean) => void; accounts: Account[] }) {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const [entryDate, setEntryDate] = useState(todayISO());
  const [narration, setNarration] = useState("");
  const [rows, setRows] = useState<Row[]>([blank(), blank()]);
  const dr = rows.reduce((s, r) => s + dec(r.debit || "0"), 0n);
  const cr = rows.reduce((s, r) => s + dec(r.credit || "0"), 0n);
  const balanced = dr === cr && dr > 0n;
  const save = useMutation({
    mutationFn: () => api<{ number: string }>(`${base}/accounting/journals`, { body: {
      entry_date: entryDate, narration,
      lines: rows.filter((r) => r.account_id && (Number(r.debit) || Number(r.credit))).map((r) => ({
        account_id: r.account_id, debit: r.debit || "0", credit: r.credit || "0", description: r.description || undefined })),
    } }),
    onSuccess: (j) => { toast.success(`Journal ${j.number} posted`); qc.invalidateQueries({ queryKey: ["biz", id] }); onOpenChange(false); setRows([blank(), blank()]); setNarration(""); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const set = (k: string, p: Partial<Row>) => setRows((rs) => rs.map((r) => (r.key === k ? { ...r, ...p } : r)));
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>Manual journal</DialogTitle><DialogDescription>For adjustments the automatic postings don&apos;t cover. Debits must equal credits.</DialogDescription></DialogHeader>
        <div className="grid gap-3 sm:grid-cols-[160px_1fr]">
          <Field label="Date"><Input type="date" value={entryDate} onChange={(e) => setEntryDate(e.target.value)} /></Field>
          <Field label="Narration"><Input value={narration} onChange={(e) => setNarration(e.target.value)} placeholder="e.g. Owner's capital introduced" /></Field>
        </div>
        <div className="space-y-2">
          {rows.map((r) => (
            <div key={r.key} className="grid grid-cols-[1fr_110px_110px_auto] gap-2">
              <NativeSelect value={r.account_id} onChange={(e) => set(r.key, { account_id: e.target.value })} aria-label="Account">
                <option value="">Account…</option>{accounts.filter((a) => a.is_active).map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}
              </NativeSelect>
              <Input placeholder="Debit" value={r.debit} className="num text-right" onChange={(e) => set(r.key, { debit: e.target.value.replace(/[^\d.]/g, ""), credit: "" })} />
              <Input placeholder="Credit" value={r.credit} className="num text-right" onChange={(e) => set(r.key, { credit: e.target.value.replace(/[^\d.]/g, ""), debit: "" })} />
              <Button variant="ghost" size="icon" disabled={rows.length <= 2} onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))} aria-label="Remove line"><Trash2Icon /></Button>
            </div>
          ))}
          <div className="flex items-center justify-between">
            <Button variant="ghost" size="sm" onClick={() => setRows((rs) => [...rs, blank()])}><PlusIcon /> Add line</Button>
            <span className={cn("num text-sm", balanced ? "text-primary" : "text-destructive")}>
              Dr {money(decStr(dr))} · Cr {money(decStr(cr))}{!balanced && dr !== cr && ` · off by ${money(decStr(dr > cr ? dr - cr : cr - dr))}`}
            </span>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={!balanced || narration.trim().length < 3 || save.isPending} onClick={() => save.mutate()}>{save.isPending && <Loader2Icon className="animate-spin" />} Post journal</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
