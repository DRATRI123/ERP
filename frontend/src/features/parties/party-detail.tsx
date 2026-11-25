"use client";

import { ArrowLeftIcon, FilePlus2Icon, MailIcon, PencilIcon, PhoneIcon, StarIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { StaticTable } from "@/components/data/data-table";
import { PeriodPicker, PRESETS } from "@/components/pickers/period";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState } from "@/components/data/states";
import { DocStatusBadge } from "@/components/data/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PartyDialog } from "@/features/parties/party-form";
import { Attachments } from "@/features/files/attachments";
import { useModules } from "@/hooks/use-modules";
import { BalanceConfirmationCard } from "@/features/accounting/finance-panels";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money } from "@/lib/format";
import { DOC_LABEL, type DocumentSummary, type Party } from "@/lib/types";
import { HandCoinsIcon, AlarmClockIcon } from "lucide-react";

interface Outstanding { outstanding: string; overdue: string; documents: DocumentSummary[] }
interface Ledger { opening: string; lines: { date: string; entry_id: string; source_ref: string | null; narration: string; debit: string; credit: string; balance: string }[] }

export function PartyDetail({ kind }: { kind: "customers" | "suppliers" }) {
  const { id } = useParams<{ id: string }>();
  const { can, isOwner } = useBusiness();
  const [edit, setEdit] = useState(false);
  const [period, setPeriod] = useState(PRESETS.this_fy!());
  const { data: p, isLoading, error, refetch } = useBizQuery<Party>([kind, id], `/${kind}/${id}`);
  const balPerm = kind === "customers" ? "customer_balances.view" : "supplier_balances.view";
  const seeBal = isOwner || can(balPerm);
  const out = useBizQuery<Outstanding>(["outstanding", id], `/parties/${id}/outstanding`, undefined, { enabled: seeBal });
  const loyaltyOn = useModules().on("loyalty");
  const pts = useBizQuery<{ points: string; value: string }>(["loyalty", id], `/customers/${id}/loyalty`, undefined, { enabled: kind === "customers" && loyaltyOn });
  const ledger = useBizQuery<Ledger>(["party-ledger", id], "/accounting/reports/party-ledger",
    { party_id: id, date_from: period.from, date_to: period.to, side: kind === "customers" ? "receivable" : "payable" }, { enabled: seeBal });
  if (isLoading) return <Skeleton className="h-96" />;
  if (error || !p) return <Card><ErrorState error={error} retry={() => refetch()} /></Card>;
  const sales = kind === "customers";
  return (
    <>
      <Link href={`/${kind}`} className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeftIcon className="size-4" /> {sales ? "Customers" : "Suppliers"}</Link>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">{p.name}</h1>
          <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
            {p.gstin ? <Badge variant="blue" className="font-mono">{p.gstin}</Badge> : <Badge variant="secondary">Unregistered</Badge>}
            {p.phone && <a href={`tel:${p.phone}`} className="flex items-center gap-1 hover:text-foreground"><PhoneIcon className="size-3.5" />{p.phone}</a>}
            {p.email && <a href={`mailto:${p.email}`} className="flex items-center gap-1 hover:text-foreground"><MailIcon className="size-3.5" />{p.email}</a>}
          </div>
        </div>
        <div className="flex gap-2">
          {(isOwner || can(`${kind}.manage`)) && <Button variant="outline" onClick={() => setEdit(true)}><PencilIcon /> Edit</Button>}
          {(isOwner || can(sales ? "invoices.create" : "purchases.create")) && <Button asChild><Link href={sales ? "/sales/new" : "/purchases/new"}><FilePlus2Icon /> New {sales ? "invoice" : "bill"}</Link></Button>}
        </div>
      </div>
      {seeBal && <div className="mb-4 sm:mb-6 grid grid-cols-2 gap-3 sm:gap-4 sm:grid-cols-3">
        <StatCard label={sales ? "To receive" : "To pay"} icon={HandCoinsIcon} loading={out.isLoading} value={money(out.data?.outstanding)} />
        <StatCard label="Overdue" icon={AlarmClockIcon} tone={Number(out.data?.overdue ?? 0) > 0 ? "red" : "green"} loading={out.isLoading} value={money(out.data?.overdue)} />
        <StatCard label="Credit terms" icon={HandCoinsIcon} tone="blue" value={p.credit_limit ? money(p.credit_limit) : "No limit"} sub={p.credit_days ? `${p.credit_days} days` : "Due on receipt"} />
        {sales && loyaltyOn && <StatCard label="Loyalty points" icon={StarIcon} tone="violet" loading={pts.isLoading}
          value={pts.data ? Number(pts.data.points).toLocaleString("en-IN") : "—"} sub={pts.data ? `worth ${money(pts.data.value)}` : undefined} />}
      </div>}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="overflow-hidden">
          <CardHeader className="pb-3"><CardTitle>Open {sales ? "invoices" : "bills"}</CardTitle></CardHeader>
          {!seeBal ? <EmptyState title="Balances are hidden" description={`Needs ${balPerm}.`} /> : !out.data?.documents.length ? <EmptyState title="Nothing outstanding" /> : (
            <StaticTable rows={out.data.documents} rowKey={(d) => d.id} columns={[
              { key: "n", header: "Number", cell: (d) => <Link href={`/documents/${d.id}`} className="font-medium text-blue hover:underline">{d.number}</Link> },
              { key: "t", header: "Type", cell: (d) => DOC_LABEL[d.doc_type], hideOnMobile: true },
              { key: "d", header: "Due", cell: (d) => date(d.due_date ?? d.doc_date) },
              { key: "s", header: "", cell: (d) => <DocStatusBadge status={d.status} payment={d.payment_status} /> },
              { key: "b", header: "Balance", align: "right", cell: (d) => money(d.balance_due) },
            ]} />
          )}
        </Card>
        <Card className="overflow-hidden">
          <CardHeader className="gap-3 pb-3"><CardTitle>Ledger</CardTitle><PeriodPicker value={period} onChange={setPeriod} /></CardHeader>
          {ledger.isLoading ? <CardContent><Skeleton className="h-40" /></CardContent> : ledger.error ? <ErrorState error={ledger.error} /> : ledger.data && (
            <StaticTable rows={ledger.data.lines} rowKey={(l, i) => `${l.entry_id}-${i}`}
              footer={undefined}
              columns={[
                { key: "d", header: "Date", cell: (l) => date(l.date) },
                { key: "r", header: "Particulars", cell: (l) => <span className="block max-w-56 truncate">{l.source_ref ?? l.narration}</span> },
                { key: "dr", header: "Debit", align: "right", cell: (l) => Number(l.debit) ? money(l.debit) : "" },
                { key: "cr", header: "Credit", align: "right", cell: (l) => Number(l.credit) ? money(l.credit) : "" },
                { key: "b", header: "Balance", align: "right", cell: (l) => money(l.balance) },
              ]} />
          )}
          {ledger.data && <p className="border-t px-4 py-2 text-xs text-muted-foreground">Opening balance {money(ledger.data.opening)}</p>}
        </Card>
      </div>
      <div className="mt-4"><BalanceConfirmationCard partyId={p.id} phone={p.phone} name={p.name} /></div>
      <Attachments entityType="party" entityId={p.id} folder={sales ? "/Customers" : "/Suppliers"} className="mt-4" />
      <PartyDialog kind={kind} open={edit} onOpenChange={setEdit} party={p} />
    </>
  );
}
