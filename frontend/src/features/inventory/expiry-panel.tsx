"use client";

import { AlarmClockIcon, CalendarX2Icon, Loader2Icon, PackageXIcon, Undo2Icon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { StaticTable } from "@/components/data/data-table";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/input";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money, qty } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Bucket { batches: number; quantity: string; value: string | null }
type Summary = Record<"expired" | "d30" | "d60" | "d90", Bucket>;
interface Expiring { product_id: string; sku: string; product_name: string; warehouse_code: string; batch_no: string | null; expiry_date: string | null; quantity: string; value: string | null }
interface ReturnLine { product_id: string; description: string; batch_no: string; expiry_date: string; quantity: string; unit_price: string; expired: boolean }
interface ReturnGroup { purchase_invoice_id: string; number: string; doc_date: string; supplier_name: string | null; lines: ReturnLine[] }

const BUCKETS: { key: keyof Summary; label: string; tone: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { key: "expired", label: "Expired", tone: "bg-destructive/10 text-destructive", icon: CalendarX2Icon },
  { key: "d30", label: "Within 30 days", tone: "bg-warning/15 text-amber-700 dark:text-warning", icon: AlarmClockIcon },
  { key: "d60", label: "31–60 days", tone: "bg-blue-soft text-blue", icon: AlarmClockIcon },
  { key: "d90", label: "61–90 days", tone: "bg-secondary text-secondary-foreground", icon: AlarmClockIcon },
];

function daysLeft(d: string | null): number | null {
  if (!d) return null;
  return Math.round((new Date(d).getTime() - new Date(new Date().toISOString().slice(0, 10)).getTime()) / 86_400_000);
}

export function ExpiryPanel() {
  const router = useRouter();
  const { can, isOwner } = useBusiness();
  const [within, setWithin] = useState(90);
  const [returnWithin, setReturnWithin] = useState(0);
  const summary = useBizQuery<Summary>(["expiry-summary"], "/inventory/expiry-summary");
  const list = useBizQuery<Expiring[]>(["expiring", within], "/inventory/expiring", { within_days: within });
  const returns = useBizQuery<ReturnGroup[]>(["expiry-returns", returnWithin], "/inventory/expiry-returns", { within_days: returnWithin });
  const canReturn = isOwner || can("purchases.create");
  const draft = useBizMutation<{ purchase_invoice_id: string; within_days: number }, { id: string }>("POST", "/inventory/expiry-returns", {
    invalidate: [["expiry-returns"]], success: "Debit note drafted — review and finalize it",
    onSuccess: (d) => router.push(`/documents/${d.id}`),
  });

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {BUCKETS.map((b) => {
          const v = summary.data?.[b.key];
          return (
            <Card key={b.key}><CardContent className="flex items-start gap-3">
              <span className={cn("flex size-10 shrink-0 items-center justify-center rounded-lg", b.tone)}><b.icon className="size-5" /></span>
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase">{b.label}</p>
                <p className="num text-2xl font-semibold">{summary.isLoading ? "…" : v?.batches ?? 0} <span className="text-sm font-normal text-muted-foreground">batches</span></p>
                {v && v.batches > 0 && <p className="text-xs text-muted-foreground">{qty(v.quantity)} units{v.value !== null && ` · ${money(v.value)}`}</p>}
              </div>
            </CardContent></Card>
          );
        })}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="overflow-hidden">
          <CardHeader className="flex-row items-center justify-between pb-3">
            <CardTitle>Expiring batches</CardTitle>
            <NativeSelect className="h-8 w-40" value={within} onChange={(e) => setWithin(Number(e.target.value))} aria-label="Window">
              {[30, 60, 90, 180].map((n) => <option key={n} value={n}>Within {n} days</option>)}</NativeSelect>
          </CardHeader>
          {list.isLoading ? <TableSkeleton rows={4} cols={4} /> : list.error ? <ErrorState error={list.error} /> : !list.data?.length
            ? <EmptyState icon={AlarmClockIcon} title="Nothing expiring" description={`No stock expires in the next ${within} days.`} /> : (
            <StaticTable rows={list.data} rowKey={(r, i) => `${r.product_id}-${r.batch_no}-${r.warehouse_code}-${i}`} columns={[
              { key: "p", header: "Product", cell: (r) => <div><p className="font-medium">{r.product_name}</p><p className="text-xs text-muted-foreground">{r.sku} · {r.warehouse_code}</p></div> },
              { key: "b", header: "Batch", cell: (r) => r.batch_no ?? "—" },
              { key: "e", header: "Expiry", cell: (r) => {
                const d = daysLeft(r.expiry_date);
                return <div><p>{date(r.expiry_date)}</p>{d !== null && <Badge variant={d < 0 ? "destructive" : d <= 30 ? "warning" : "secondary"}>{d < 0 ? `expired ${-d}d ago` : `${d} days`}</Badge>}</div>;
              } },
              { key: "q", header: "Qty", align: "right", cell: (r) => qty(r.quantity) },
            ]} />
          )}
          <p className="border-t px-4 py-2 text-xs text-muted-foreground">Billing sells the earliest-expiring batch first and refuses expired batches. Get alerts by adding an automation rule on “batch nears expiry”.</p>
        </Card>

        <Card className="overflow-hidden">
          <CardHeader className="flex-row items-center justify-between pb-3">
            <CardTitle>Return to supplier</CardTitle>
            <NativeSelect className="h-8 w-44" value={returnWithin} onChange={(e) => setReturnWithin(Number(e.target.value))} aria-label="Include">
              <option value={0}>Expired only</option><option value={30}>+ expiring in 30 days</option><option value={60}>+ expiring in 60 days</option><option value={90}>+ expiring in 90 days</option></NativeSelect>
          </CardHeader>
          {returns.isLoading ? <TableSkeleton rows={3} cols={3} /> : returns.error ? <ErrorState error={returns.error} /> : !returns.data?.length
            ? <EmptyState icon={PackageXIcon} title="Nothing to return" description="Stock bought on a purchase bill appears here as it expires." /> : (
            <ul className="divide-y">
              {returns.data.map((g) => (
                <li key={g.purchase_invoice_id} className="space-y-2 px-4 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <div><p className="font-medium">{g.supplier_name ?? "Supplier"}</p><p className="text-xs text-muted-foreground">Bill {g.number} · {date(g.doc_date)}</p></div>
                    {canReturn && <Button size="sm" variant="outline" disabled={draft.isPending}
                      onClick={() => draft.mutate({ purchase_invoice_id: g.purchase_invoice_id, within_days: returnWithin })}>
                      {draft.isPending && draft.variables?.purchase_invoice_id === g.purchase_invoice_id ? <Loader2Icon className="animate-spin" /> : <Undo2Icon />} Draft debit note</Button>}
                  </div>
                  <ul className="space-y-1 text-sm">
                    {g.lines.map((l) => (
                      <li key={`${l.product_id}-${l.batch_no}`} className="flex justify-between gap-2">
                        <span className="truncate">{l.description} <span className="text-muted-foreground">· {l.batch_no} · {date(l.expiry_date)}</span>
                          {l.expired && <Badge variant="destructive" className="ml-1">expired</Badge>}</span>
                        <span className="num shrink-0">{qty(l.quantity)} × {money(l.unit_price)}</span>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
