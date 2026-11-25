"use client";

import { KeyRoundIcon, Loader2Icon, PencilIcon, SendIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";

export interface DiscountGateInfo { docId: string; percent: string; limit: string; payments: unknown[]; pending: boolean }

/** The bill's discount is above the biller's limit: ask a manager, or type the code they read out. */
export function DiscountGate({ gate, onClose, onDone }: { gate: DiscountGateInfo; onClose: () => void; onDone: (doc: { id: string; number: string }) => void }) {
  const { base } = useBusiness();
  const router = useRouter();
  const [asked, setAsked] = useState(gate.pending);
  const [reason, setReason] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"ask" | "finalize" | null>(null);
  const finalize = async (withCode: boolean) => {
    setBusy("finalize");
    try {
      const doc = await api<{ id: string; number: string }>(`${base}/documents/${gate.docId}/finalize`, {
        body: { payments: gate.payments, ...(withCode ? { discount_code: code } : {}) } });
      onDone(doc);
    } catch (e) {
      toast.error(e instanceof ApiError && e.code === "discount_approval_required" ? "Not approved yet" : e instanceof Error ? e.message : String(e));
    } finally { setBusy(null); }
  };
  const ask = async () => {
    setBusy("ask");
    try {
      await api(`${base}/documents/${gate.docId}/discount-approval`, { body: { reason: reason || null } });
      setAsked(true);
      toast.success("Sent to managers — they can approve in the app or read you a code");
    } catch (e) { toast.error(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Discount needs approval</DialogTitle>
          <DialogDescription>This bill gives {Number(gate.percent)} % off; you may give up to {Number(gate.limit)} %. The bill is kept as a draft.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {!asked ? (
            <div className="space-y-2">
              <Field label="Why (optional)" htmlFor="dg-reason"><Input id="dg-reason" value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} placeholder="Old customer, bulk order…" /></Field>
              <Button className="w-full" variant="outline" disabled={busy !== null} onClick={ask}>{busy === "ask" ? <Loader2Icon className="animate-spin" /> : <SendIcon />} Ask a manager</Button>
            </div>
          ) : (
            <p className="rounded-md bg-primary-soft p-2 text-sm">Asked. When a manager approves in the app, press <b>Try again</b>. Or type the 6-digit code they read out.</p>
          )}
          <Field label="Manager's code" htmlFor="dg-code">
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (code.length === 6) finalize(true); }}>
              <Input id="dg-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} className="num tracking-[0.3em]" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} placeholder="000000" />
              <Button type="submit" disabled={code.length !== 6 || busy !== null}>{busy === "finalize" ? <Loader2Icon className="animate-spin" /> : <KeyRoundIcon />} Use code</Button>
            </form>
          </Field>
        </div>
        <DialogFooter className="gap-2 sm:justify-between">
          <Button variant="ghost" onClick={() => router.replace(`/sales/new?draft=${gate.docId}`)}><PencilIcon /> Change the discount</Button>
          <Button variant="outline" disabled={busy !== null} onClick={() => finalize(false)}>Try again</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
