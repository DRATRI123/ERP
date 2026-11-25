"use client";

import { FilePlus2Icon, Loader2Icon, PercentIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { StaticTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money, todayISO } from "@/lib/format";

interface Inv { document_id: string; number: string; due_date: string; balance_due: string; interest_from: string; interest_to: string; days: number; interest: string }
interface PartyRow { party_id: string; party_name: string; rate: string; interest: string; invoices: Inv[] }
interface Data { as_of: string; settings: { rate: string; grace_days: number }; parties: PartyRow[] }

export default function InterestPage() {
  const { can, isOwner } = useBusiness();
  const [asOf, setAsOf] = useState(todayISO());
  const q = useBizQuery<Data>(["interest"], "/interest", { as_of: asOf });
  const notes = useBizMutation<{ party_id: string; as_of: string }, { id: string; number: string | null }[]>("POST", "/interest/debit-notes", {
    invalidate: [["interest"], ["documents"]], success: (out) => `${out.length} draft debit note${out.length === 1 ? "" : "s"} created — review and finalize them in Sales`,
  });
  const create = isOwner || can("invoices.create");
  const total = (q.data?.parties ?? []).reduce((s, p) => s + Number(p.interest), 0);
  return (
    <>
      <PageHeader title="Interest on overdue bills" description="Interest at a yearly rate, day by day, only on what stayed unpaid after the due date. Charge it with a debit note."
        actions={<label className="flex items-center gap-2 text-sm text-muted-foreground">As of <Input type="date" className="w-40" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>} />
      <InterestSettings current={q.data?.settings} editable={isOwner || can("business.settings")} />
      {q.error ? <Card><ErrorState error={q.error} retry={() => q.refetch()} /></Card> : q.isLoading ? <Card><TableSkeleton /></Card> : !q.data?.parties.length ? (
        <Card><EmptyState icon={PercentIcon} title="No interest due" description={Number(q.data?.settings.rate) > 0 ? "No customer has paid late, or the interest is already charged." : "Set the yearly rate above to start. A customer can have their own rate (or 0%) on their page."} /></Card>
      ) : (
        <div className="space-y-4">
          <p className="text-sm">Total interest due: <b className="num">{money(total.toFixed(2))}</b> from {q.data.parties.length} customer{q.data.parties.length === 1 ? "" : "s"}.</p>
          {q.data.parties.map((p) => (
            <Card key={p.party_id} className="overflow-hidden">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
                <div><Link href={`/customers/${p.party_id}`} className="font-semibold hover:underline">{p.party_name}</Link>
                  <p className="text-xs text-muted-foreground">{Number(p.rate)}% per year</p></div>
                <div className="flex items-center gap-3"><span className="num text-lg font-semibold">{money(p.interest)}</span>
                  {create && <Button size="sm" disabled={notes.isPending} onClick={() => notes.mutate({ party_id: p.party_id, as_of: asOf })}>
                    {notes.isPending ? <Loader2Icon className="animate-spin" /> : <FilePlus2Icon />} Draft debit notes</Button>}</div>
              </div>
              <StaticTable rows={p.invoices} rowKey={(i) => i.document_id} columns={[
                { key: "n", header: "Invoice", mobile: "title", cell: (i) => <Link href={`/documents/${i.document_id}`} className="font-medium text-primary hover:underline">{i.number}</Link> },
                { key: "due", header: "Due", cell: (i) => date(i.due_date) },
                { key: "per", header: "Interest period", cell: (i) => <span>{date(i.interest_from)} – {date(i.interest_to)} <span className="text-muted-foreground">({i.days} days)</span></span> },
                { key: "bal", header: "Still due", align: "right", cell: (i) => <span className="num">{money(i.balance_due)}</span> },
                { key: "int", header: "Interest", align: "right", mobile: "aside", cell: (i) => <span className="num font-medium">{money(i.interest)}</span> },
              ]} />
            </Card>
          ))}
          <p className="text-xs text-muted-foreground">Under GST, interest for late payment is part of the value of the supply: each debit note carries the invoice’s HSN and GST rate. Check them before finalizing.</p>
        </div>
      )}
    </>
  );
}

function InterestSettings({ current, editable }: { current?: { rate: string; grace_days: number }; editable: boolean }) {
  const [edit, setEdit] = useState<{ rate: string; grace_days: string } | null>(null);
  const v = edit ?? { rate: current ? String(Number(current.rate) || "") : "", grace_days: String(current?.grace_days ?? 0) };
  const save = useBizMutation<{ rate: string; grace_days: number }>("PUT", "/interest/settings", { invalidate: [["interest"]], success: "Interest settings saved", onSuccess: () => setEdit(null) });
  return (
    <Card className="mb-4">
      <CardContent className="flex flex-wrap items-end gap-3">
        <Field label="Default rate (% per year)" htmlFor="int-rate"><Input id="int-rate" inputMode="decimal" className="num w-36" disabled={!editable} value={v.rate} placeholder="18"
          onChange={(e) => setEdit({ ...v, rate: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
        <Field label="Grace days after due date" htmlFor="int-grace"><Input id="int-grace" inputMode="numeric" className="num w-36" disabled={!editable} value={v.grace_days}
          onChange={(e) => setEdit({ ...v, grace_days: e.target.value.replace(/\D/g, "") })} /></Field>
        {editable && edit && <Button disabled={save.isPending} onClick={() => save.mutate({ rate: v.rate || "0", grace_days: Number(v.grace_days || 0) })}>Save</Button>}
        <p className="basis-full text-xs text-muted-foreground">A customer’s own rate (on their page) overrides this; 0% exempts them.</p>
      </CardContent>
    </Card>
  );
}
