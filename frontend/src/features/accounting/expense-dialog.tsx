"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type { Account } from "@/features/accounting/journal-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";
import { todayISO } from "@/lib/format";

export function ExpenseDialog({ open, onOpenChange, accounts }: { open: boolean; onOpenChange: (v: boolean) => void; accounts: Account[] }) {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const expenseAccounts = accounts.filter((a) => a.type === "expense" && a.is_active && !["cost_of_goods"].includes(a.subtype));
  const payFrom = accounts.filter((a) => ["cash", "bank"].includes(a.subtype) && a.is_active);
  const [v, setV] = useState({ expense_date: todayISO(), account_id: "", description: "", amount: "", gst_rate: "0", paid_from_account_id: "", reference: "" });
  const save = useMutation({
    mutationFn: () => api<{ number: string }>(`${base}/accounting/expenses`, { body: {
      ...v, paid_from_account_id: v.paid_from_account_id || undefined, reference: v.reference || undefined } }),
    onSuccess: (e) => { toast.success(`Expense ${e.number} recorded`); qc.invalidateQueries({ queryKey: ["biz", id] }); onOpenChange(false); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setV({ ...v, [k]: e.target.value });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>Record expense</DialogTitle></DialogHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Expense head" className="sm:col-span-2"><NativeSelect value={v.account_id} onChange={set("account_id")}><option value="">Choose…</option>
            {expenseAccounts.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</NativeSelect></Field>
          <Field label="Description" className="sm:col-span-2"><Input value={v.description} onChange={set("description")} placeholder="e.g. Electricity bill — October" /></Field>
          <Field label="Amount (before GST)"><Input value={v.amount} inputMode="decimal" className="num" onChange={set("amount")} /></Field>
          <Field label="GST rate"><NativeSelect value={v.gst_rate} onChange={set("gst_rate")}>{["0", "5", "12", "18", "28"].map((g) => <option key={g} value={g}>{g}%</option>)}</NativeSelect></Field>
          <Field label="Date"><Input type="date" value={v.expense_date} onChange={set("expense_date")} /></Field>
          <Field label="Paid from" hint="Leave empty if not paid yet"><NativeSelect value={v.paid_from_account_id} onChange={set("paid_from_account_id")}><option value="">Not paid yet</option>
            {payFrom.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</NativeSelect></Field>
          <Field label="Bill / reference no." className="sm:col-span-2"><Input value={v.reference} onChange={set("reference")} /></Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={!v.account_id || !v.amount || v.description.trim().length < 2 || save.isPending} onClick={() => save.mutate()}>{save.isPending && <Loader2Icon className="animate-spin" />} Save expense</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
