"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2Icon, Loader2Icon, ScaleIcon } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { date, money } from "@/lib/format";

interface Entry { date: string; ref: string; narration: string | null; debit: string; credit: string; balance: string }
interface View { as_of: string; balance: string; status: string; statement: { party: string; business: string; business_gstin: string | null; from: string; opening: string; entries: Entry[]; closing: string } }

/** The page a customer / supplier opens from the WhatsApp link: their statement, Confirm or Disagree. No login. */
export default function ConfirmPage() {
  const { token } = useParams<{ token: string }>();
  const q = useQuery<View, Error>({
    queryKey: ["public-confirm", token], retry: false,
    queryFn: async () => {
      const r = await fetch(`/api/v1/public/confirm/${encodeURIComponent(token)}`);
      if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.error?.message ?? "This link is not valid");
      return r.json();
    },
  });
  const [mode, setMode] = useState<"" | "dispute">("");
  const [their, setTheir] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function answer(agree: boolean) {
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`/api/v1/public/confirm/${encodeURIComponent(token)}`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agree, note: note || undefined, their_balance: their || undefined }) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.error?.message ?? "Could not send");
      setDone(agree ? "confirmed" : "disputed");
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  if (q.isLoading) return <div className="mx-auto max-w-xl space-y-3 p-4"><Skeleton className="h-10" /><Skeleton className="h-64" /></div>;
  if (q.error || !q.data) return <p className="p-8 text-center text-muted-foreground">{q.error?.message ?? "Not found"}</p>;
  const s = q.data.statement;
  const bal = Number(q.data.balance);
  const status = done ?? q.data.status;
  return (
    <main className="mx-auto min-h-dvh max-w-xl space-y-4 bg-background p-4">
      <header className="flex items-center gap-2"><ScaleIcon className="size-5 text-primary" /><div><p className="font-semibold">{s.business}</p>{s.business_gstin && <p className="text-xs text-muted-foreground">GSTIN {s.business_gstin}</p>}</div></header>
      <section className="rounded-xl border bg-card p-4">
        <p className="text-sm text-muted-foreground">Account of {s.party} as on {date(q.data.as_of)}</p>
        <p className="num mt-1 text-3xl font-semibold">{money(String(Math.abs(bal)))}</p>
        <p className="text-sm">{bal >= 0 ? `payable by you to ${s.business}` : `payable by ${s.business} to you`}</p>
      </section>
      <section className="overflow-hidden rounded-xl border bg-card text-sm">
        <div className="flex justify-between border-b px-3 py-2 text-muted-foreground"><span>Opening {date(s.from)}</span><span className="num">{money(s.opening)}</span></div>
        {s.entries.map((e, i) => (
          <div key={i} className="grid grid-cols-[1fr_auto] gap-x-3 border-b px-3 py-2 last:border-0">
            <span><b>{e.ref}</b> <span className="text-muted-foreground">· {date(e.date)}</span></span>
            <span className="num text-right">{Number(e.debit) ? `+${money(e.debit)}` : `−${money(e.credit)}`}</span>
            <span className="text-xs text-muted-foreground">{e.narration}</span><span className="num text-right text-xs text-muted-foreground">{money(e.balance)}</span>
          </div>))}
        <div className="flex justify-between bg-muted/40 px-3 py-2 font-medium"><span>Closing</span><span className="num">{money(s.closing)}</span></div>
      </section>
      {status !== "sent" ? (
        <p className="flex items-center gap-2 rounded-xl border bg-card p-4 text-sm"><CheckCircle2Icon className="size-5 text-primary" />{status === "confirmed" ? "Thank you — you confirmed this balance." : "Thank you — your reply was sent; they will check the difference."}</p>
      ) : mode === "dispute" ? (
        <section className="space-y-3 rounded-xl border bg-card p-4">
          <label className="block text-sm font-medium" htmlFor="their">Balance in your books (₹)</label>
          <Input id="their" inputMode="decimal" value={their} onChange={(e) => setTheir(e.target.value.replace(/[^\d.-]/g, ""))} />
          <label className="block text-sm font-medium" htmlFor="note">What differs?</label>
          <Textarea id="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Our payment of ₹5,000 on 12 Nov by UPI is not shown" />
          {err && <p className="text-sm text-destructive">{err}</p>}
          <div className="flex gap-2"><Button variant="outline" onClick={() => setMode("")}>Back</Button><Button disabled={busy} onClick={() => answer(false)}>{busy && <Loader2Icon className="animate-spin" />} Send</Button></div>
        </section>
      ) : (
        <section className="grid grid-cols-2 gap-3">
          <Button size="lg" disabled={busy} onClick={() => answer(true)}>{busy ? <Loader2Icon className="animate-spin" /> : <CheckCircle2Icon />} Confirm</Button>
          <Button size="lg" variant="outline" onClick={() => setMode("dispute")}>Doesn&apos;t match</Button>
          {err && <p className="col-span-2 text-sm text-destructive">{err}</p>}
        </section>
      )}
    </main>
  );
}
