"use client";

import { Loader2Icon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Textarea } from "@/components/ui/input";

/** Confirmation for consequential actions; optionally asks for a reason (cancellations). */
export function ConfirmDialog({ open, onOpenChange, title, description, confirm = "Confirm", destructive, reason, onConfirm }: {
  open: boolean; onOpenChange: (v: boolean) => void; title: string; description?: React.ReactNode; confirm?: string;
  destructive?: boolean; reason?: boolean; onConfirm: (reason: string) => Promise<unknown> | void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open={open} onOpenChange={(v) => { if (!busy) { onOpenChange(v); setText(""); } }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{title}</DialogTitle>{description && <DialogDescription>{description}</DialogDescription>}</DialogHeader>
        {reason && <Field label="Reason" hint="Recorded in the audit trail."><Textarea value={text} onChange={(e) => setText(e.target.value)} autoFocus /></Field>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>Back</Button>
          <Button variant={destructive ? "destructive" : "default"} disabled={busy || (reason && text.trim().length < 3)}
            onClick={async () => { setBusy(true); try { await onConfirm(text.trim()); onOpenChange(false); setText(""); } catch { /* toast shown */ } finally { setBusy(false); } }}>
            {busy && <Loader2Icon className="animate-spin" />}{confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
