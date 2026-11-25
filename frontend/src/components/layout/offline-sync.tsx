"use client";

import { useQueryClient } from "@tanstack/react-query";
import { CloudOffIcon, CloudUploadIcon, Loader2Icon, RefreshCwIcon, Trash2Icon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useBusiness } from "@/hooks/use-business";
import { money } from "@/lib/format";
import { cacheProducts, cachedProducts, discardBill, queuedBills, syncQueue, useOffline, useOnline } from "@/lib/offline";
import { cn } from "@/lib/utils";

const PRODUCT_REFRESH_MS = 30 * 60_000;

/** Keeps the offline product copy fresh and sends queued bills whenever the connection is there. */
export function OfflineSync() {
  const { base, id } = useBusiness();
  const online = useOnline();
  const qc = useQueryClient();
  const run = useCallback(async () => {
    if (!id || !online) return;
    useOffline.getState().refresh(id);
    if (queuedBills(id).length) {
      const r = await syncQueue(base, id);
      if (r.sent) {
        toast.success(`${r.sent} bill${r.sent === 1 ? "" : "s"} made offline ${r.sent === 1 ? "was" : "were"} saved`);
        void qc.invalidateQueries({ queryKey: ["biz", id] });
      }
      if (r.failed) toast.error(`${r.failed} offline bill${r.failed === 1 ? "" : "s"} could not be saved — see the cloud icon at the top`);
    }
    const at = cachedProducts(id).at;
    if (!at || Date.now() - new Date(at).getTime() > PRODUCT_REFRESH_MS) {
      try { await cacheProducts(base, id); } catch { /* try again later */ }
    }
  }, [base, id, online, qc]);
  useEffect(() => {
    // The service worker only in production builds: in development it would cache stale code.
    if (process.env.NODE_ENV === "production" && "serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    }
  }, []);
  useEffect(() => {
    void run();
    const t = window.setInterval(() => void run(), 60_000);
    return () => window.clearInterval(t);
  }, [run]);
  return null;
}

/** Topbar: offline / bills waiting to sync, with the queue. */
export function OfflineBadge() {
  const { base, id } = useBusiness();
  const online = useOnline();
  const { pending, failed, syncing } = useOffline();
  const [open, setOpen] = useState(false);
  if (online && !pending) return null;
  const bills = open ? queuedBills(id) : [];
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} aria-label={online ? `${pending} bills waiting to sync` : "Offline"}
        className={cn("flex h-8 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium", failed ? "bg-destructive/10 text-destructive" : "bg-warning/15 text-amber-800 dark:text-warning")}>
        {online ? <CloudUploadIcon className="size-4" /> : <CloudOffIcon className="size-4" />}
        <span>{online ? `${pending} to sync` : pending ? `Offline · ${pending}` : "Offline"}</span>
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>{online ? "Bills waiting to be saved" : "You are offline"}</DialogTitle>
            <DialogDescription>Bills made without internet are kept on this device and sent automatically when the connection is back. Product search uses the copy saved {cachedProducts(id).at ? `at ${new Date(cachedProducts(id).at!).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}` : "— none yet"}.</DialogDescription></DialogHeader>
          {bills.length === 0 ? <p className="py-4 text-center text-sm text-muted-foreground">Nothing waiting.</p> : (
            <ul className="divide-y rounded-lg border">{bills.map((b) => (
              <li key={b.client_ref} className="flex items-start gap-3 px-3 py-2 text-sm">
                <div className="min-w-0 flex-1"><p className="font-medium">{b.label}</p>
                  <p className="text-xs text-muted-foreground">{new Date(b.created_at).toLocaleString("en-IN")} · about {money(b.estimate)}{b.pay_mode ? ` · paid by ${b.pay_mode}` : " · on credit"}</p>
                  {b.error && <p className="text-xs text-destructive">{b.error}</p>}</div>
                {b.error && <Button variant="ghost" size="icon-sm" aria-label="Discard this bill" onClick={() => { if (confirm("Discard this offline bill? It was never saved.")) { discardBill(id, b.client_ref); setOpen(false); } }}><Trash2Icon /></Button>}
              </li>))}</ul>
          )}
          {online && pending > 0 && <Button disabled={syncing} onClick={async () => { const r = await syncQueue(base, id); toast.message(`${r.sent} sent, ${r.failed} refused`); setOpen(false); }}>
            {syncing ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />} Send now</Button>}
        </DialogContent>
      </Dialog>
    </>
  );
}
