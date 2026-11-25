"use client";

import { ArrowDownLeftIcon, ArrowUpRightIcon, PlusIcon } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { DataTable, type Column } from "@/components/data/data-table";
import { ConfirmDialog } from "@/components/data/confirm";
import { PageHeader } from "@/components/data/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/input";
import { PaymentDialog } from "@/features/payments/payment-dialog";
import { useBizMutation, useBusiness } from "@/hooks/use-business";
import { date, money, titleCase } from "@/lib/format";

interface Payment { id: string; direction: "in" | "out"; number: string; payment_date: string; mode: string; amount: string; reference: string | null; notes: string | null; status: string; allocations: { document_id: string; amount: string }[]; tds_amount?: string; tds_section?: string | null }

function PaymentsPage() {
  const params = useSearchParams();
  const router = useRouter();
  const { can, isOwner } = useBusiness();
  const [open, setOpen] = useState(params.get("new") === "1");
  const [direction, setDirection] = useState("");
  const [cancelling, setCancelling] = useState<Payment | null>(null);
  const cancel = useBizMutation<{ reason: string }>("POST", () => `/payments/${cancelling?.id}/cancel`,
    { invalidate: [["payments"], ["documents"]], success: "Payment cancelled; its invoices are due again" });
  const cols: Column<Payment>[] = [
    { key: "n", header: "Number", cell: (p) => (
      <span className="flex items-center gap-2 font-medium">
        <span className={p.direction === "in" ? "text-primary" : "text-blue"}>{p.direction === "in" ? <ArrowDownLeftIcon className="size-4" /> : <ArrowUpRightIcon className="size-4" />}</span>{p.number}
      </span>) },
    { key: "d", header: "Date", cell: (p) => date(p.payment_date) },
    { key: "m", header: "Mode", cell: (p) => <Badge variant="outline">{titleCase(p.mode)}</Badge> },
    { key: "r", header: "Reference", cell: (p) => <span className="block max-w-64 truncate">{p.reference ?? p.notes ?? "—"}</span>, hideOnMobile: true },
    { key: "s", header: "Status", cell: (p) => p.status === "cancelled" ? <Badge variant="destructive">Cancelled</Badge> : <Badge>{p.allocations.length ? "Settled" : "Advance"}</Badge> },
    { key: "a", header: "Amount", align: "right", cell: (p) => <span className="font-medium">{p.direction === "out" ? "-" : ""}{money(p.amount)}
      {Number(p.tds_amount ?? 0) > 0 && <span className="block text-xs font-normal text-muted-foreground">+ TDS {money(p.tds_amount)} u/s {p.tds_section}</span>}</span> },
    ...((isOwner || can("payments.create")) ? [{ key: "x", header: "", cell: (p: Payment) => p.status !== "cancelled" &&
      <Button size="sm" variant="ghost" className="text-destructive" onClick={() => setCancelling(p)}>Cancel</Button> }] : []),
  ];
  return (
    <>
      <PageHeader title="Banking & payments" description="Money received from customers and paid to suppliers, by UPI, cash, card or bank."
        actions={(isOwner || can("payments.create")) && <Button onClick={() => setOpen(true)}><PlusIcon /> Record payment</Button>} />
      <DataTable<Payment> queryKey={["payments"]} path="/payments" columns={cols} searchParam={null}
        filters={{ direction: direction || undefined }}
        toolbar={<NativeSelect className="w-44" value={direction} onChange={(e) => setDirection(e.target.value)} aria-label="Direction">
          <option value="">Received & paid</option><option value="in">Received</option><option value="out">Paid</option></NativeSelect>} />
      <ConfirmDialog open={!!cancelling} onOpenChange={(v) => !v && setCancelling(null)} destructive reason confirm="Cancel payment"
        title={`Cancel ${cancelling?.number}?`} description="The amount is taken off the invoices or bills it settled and the books are reversed. The payment stays on record as cancelled."
        onConfirm={(reason) => cancel.mutateAsync({ reason })} />
      <PaymentDialog open={open} onOpenChange={(v) => { setOpen(v); if (!v && params.get("new")) router.replace("/payments"); }}
        initial={params.get("new") ? { direction: (params.get("dir") as "in" | "out") ?? "in", partyId: params.get("party") || undefined, docId: params.get("doc") || undefined } : undefined} />
    </>
  );
}

export default function Page() {
  return <Suspense><PaymentsPage /></Suspense>;
}
