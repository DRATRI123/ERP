"use client";

import { CheckIcon, CopyIcon, KeyRoundIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

/** Shows a secret exactly once (API keys, webhook secrets, recovery codes). */
export function SecretReveal({ title, secret, note, onClose }: { title: string; secret: string | null; note?: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Dialog open={!!secret} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><KeyRoundIcon className="size-5 text-primary" />{title}</DialogTitle>
          <DialogDescription>{note ?? "Copy it now and store it safely. It will not be shown again."}</DialogDescription>
        </DialogHeader>
        <pre className="rounded-lg border bg-muted p-3 font-mono text-sm break-all whitespace-pre-wrap select-all">{secret}</pre>
        <DialogFooter>
          <Button variant="outline" onClick={() => { void navigator.clipboard.writeText(secret ?? ""); setCopied(true); }}>{copied ? <CheckIcon /> : <CopyIcon />} {copied ? "Copied" : "Copy"}</Button>
          <Button onClick={onClose}>I&apos;ve stored it</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
