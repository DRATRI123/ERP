"use client";

import { Loader2Icon, PackageOpenIcon, Undo2Icon, UsersIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { StaticTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money, qty, todayISO } from "@/lib/format";

interface Row {
  document_id: string; number: string; doc_date: string; party_name: string | null; line_id: string; description: string;
  unit_price: string; sent: string; invoiced: string; returned: string; pending: string; pending_value: string;
}

export default function SaleOrReturnPage() {
  const { can, isOwner } = useBusiness();
  const edit = isOwner || can("stock.edit");
  const [tab, setTab] = useState("open");
  const data = useBizQuery<{ lines: Row[]; parties: { party_name: string; pending_value: string; lines: number }[]; pending_value: string }>(
    ["sale-or-return", tab], "/sale-or-return", { open_only: tab === "open" });
  const [returning, setReturning] = useState<Row[] | null>(null);
  const d = data.data;
  return (
    <>
      <PageHeader title="Sale or return" description="Goods sent on a delivery challan: how much the customer sold (invoiced), sent back, and still holds."
        actions={<Button asChild variant="outline"><Link href="/sales">Delivery challans</Link></Button>} />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:mb-6 sm:gap-4 lg:grid-cols-3">
        <StatCard label="With customers (at selling price)" icon={PackageOpenIcon} loading={data.isLoading} value={d ? money(d.pending_value) : "—"} />
        <StatCard label="Customers holding goods" icon={UsersIcon} loading={data.isLoading} value={d ? String(d.parties.filter((p) => Number(p.pending_value) > 0).length) : "—"}
          sub={d?.parties[0] && Number(d.parties[0].pending_value) > 0 ? `Most: ${d.parties[0].party_name}` : undefined} />
      </div>
      <Card className="overflow-hidden">
        <div className="border-b p-3"><Tabs value={tab} onValueChange={setTab}><TabsList>
          <TabsTrigger value="open">Still with customer</TabsTrigger><TabsTrigger value="all">All challans</TabsTrigger></TabsList></Tabs></div>
        {data.error ? <ErrorState error={data.error} retry={() => data.refetch()} /> : data.isLoading ? <TableSkeleton /> : !d?.lines.length ? (
          <EmptyState icon={PackageOpenIcon} title="Nothing out on sale-or-return" description="Make a delivery challan for goods you send on approval. Invoice what they sell from the challan, and record what comes back here." />
        ) : (
          <StaticTable rows={d.lines} rowKey={(r) => r.line_id} columns={[
            { key: "i", header: "Item", mobile: "title", cell: (r) => <div><p className="font-medium">{r.description}</p>
              <p className="text-xs text-muted-foreground"><Link className="hover:underline" href={`/documents/${r.document_id}`}>{r.number}</Link> · {date(r.doc_date)} · {r.party_name ?? "—"}</p></div> },
            { key: "s", header: "Sent", align: "right", cell: (r) => <span className="num">{qty(r.sent)}</span> },
            { key: "v", header: "Sold", align: "right", cell: (r) => <span className="num">{qty(r.invoiced)}</span> },
            { key: "b", header: "Back", align: "right", cell: (r) => <span className="num">{qty(r.returned)}</span> },
            { key: "p", header: "With them", align: "right", mobile: "aside", cell: (r) => <div className="text-right"><p className="num font-medium">{qty(r.pending)}</p><p className="num text-xs text-muted-foreground">{money(r.pending_value)}</p></div> },
            ...(edit ? [{ key: "x", header: "", mobile: "row" as const, cell: (r: Row) => Number(r.pending) > 0 && (
              <div className="flex justify-end"><Button size="sm" variant="outline" onClick={() => setReturning(d.lines.filter((x) => x.document_id === r.document_id && Number(x.pending) > 0))}><Undo2Icon /> Goods back</Button></div>) }] : []),
          ]} />
        )}
      </Card>
      {returning && <ReturnDialog rows={returning} onClose={() => setReturning(null)} />}
    </>
  );
}

function ReturnDialog({ rows, onClose }: { rows: Row[]; onClose: () => void }) {
  const [q, setQ] = useState<Record<string, string>>(() => Object.fromEntries(rows.map((r) => [r.line_id, String(Number(r.pending))])));
  const [on, setOn] = useState(todayISO());
  const first = rows[0]!;
  const save = useBizMutation<Record<string, unknown>>("POST", `/documents/${first.document_id}/return-unsold`,
    { invalidate: [["sale-or-return"], ["products"]], success: "Unsold goods back in stock", onSuccess: onClose });
  const lines = rows.map((r) => ({ line_id: r.line_id, quantity: q[r.line_id] ?? "0" })).filter((l) => Number(l.quantity) > 0);
  const over = rows.some((r) => Number(q[r.line_id] || 0) > Number(r.pending));
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>Goods back on {first.number}</DialogTitle>
          <DialogDescription>{first.party_name} returned these unsold. They go back into stock at the cost they left at.</DialogDescription></DialogHeader>
        <div className="space-y-2">
          {rows.map((r) => (
            <div key={r.line_id} className="grid grid-cols-[1fr_7rem] items-center gap-3 rounded-md border p-2">
              <div className="min-w-0"><p className="truncate text-sm font-medium">{r.description}</p><p className="text-xs text-muted-foreground">{qty(r.pending)} with them</p></div>
              <Input aria-label={`Returned ${r.description}`} inputMode="decimal" className="num text-right" value={q[r.line_id] ?? ""}
                onChange={(e) => setQ((x) => ({ ...x, [r.line_id]: e.target.value.replace(/[^\d.]/g, "") }))} />
            </div>
          ))}
          <Field label="Date" htmlFor="sr-on" className="max-w-48"><Input id="sr-on" type="date" value={on} onChange={(e) => setOn(e.target.value)} /></Field>
          {over && <p className="text-sm text-destructive">More than they hold.</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!lines.length || over || save.isPending} onClick={() => save.mutate({ on, lines })}>{save.isPending && <Loader2Icon className="animate-spin" />} Put back in stock</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
