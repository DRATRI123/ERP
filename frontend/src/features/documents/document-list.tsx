"use client";

import { DownloadIcon, PlusIcon, FileUpIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { DataTable, type Column } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { DocStatusBadge } from "@/components/data/status-badge";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBusiness } from "@/hooks/use-business";
import { openFile } from "@/lib/api";
import { date, money, monthStartISO, todayISO } from "@/lib/format";
import { DOC_LABEL, PURCHASE_TYPES, SALES_TYPES, type DocType, type DocumentSummary } from "@/lib/types";

export function DocumentList({ side }: { side: "sales" | "purchase" }) {
  const router = useRouter();
  const { can, isOwner, base } = useBusiness();
  const types = side === "sales" ? SALES_TYPES : PURCHASE_TYPES;
  const [docType, setDocType] = useState<DocType | "all">(side === "sales" ? "sales_invoice" : "purchase_invoice");
  const [status, setStatus] = useState("");
  const [payment, setPayment] = useState("");
  const columns: Column<DocumentSummary>[] = [
    { key: "number", header: "Number", cell: (d) => <span className="font-medium">{d.number ?? <span className="text-muted-foreground">Draft</span>}</span> },
    { key: "date", header: "Date", cell: (d) => date(d.doc_date), sortable: true, sortValue: (d) => d.doc_date },
    { key: "party", header: side === "sales" ? "Customer" : "Supplier", cell: (d) => d.party_name ?? <span className="text-muted-foreground">Walk-in</span>, sortable: true, sortValue: (d) => d.party_name ?? "" },
    { key: "type", header: "Type", cell: (d) => DOC_LABEL[d.doc_type], hideOnMobile: true },
    { key: "status", header: "Status", cell: (d) => <DocStatusBadge status={d.status} payment={d.payment_status} /> },
    { key: "due", header: "Due", align: "right", hideOnMobile: true, cell: (d) => Number(d.balance_due) > 0 ? money(d.balance_due) : "—" },
    { key: "total", header: "Total", align: "right", cell: (d) => <span className="font-medium">{money(d.grand_total)}</span>, sortable: true, sortValue: (d) => Number(d.grand_total) },
  ];
  const canCreate = isOwner || can(side === "sales" ? "invoices.create" : "purchases.create");
  return (
    <>
      <PageHeader
        title={side === "sales" ? "Sales & invoices" : "Purchases"}
        description={side === "sales" ? "Invoices, quotations, orders, challans and returns." : "Purchase bills, orders and returns to suppliers."}
        actions={<>
          {(isOwner || can("data.export", "gst.view")) && side === "sales" && (
            <Button variant="outline" onClick={() => openFile(`${base}/data/export/sales-register`, { format: "xlsx", date_from: monthStartISO(), date_to: todayISO() }, "sales-register.xlsx")}><DownloadIcon /> Export this month</Button>
          )}
          {canCreate && side === "purchase" && <Button asChild variant="outline"><Link href="/purchases/import"><FileUpIcon /> Import supplier bill</Link></Button>}
          {canCreate && <Button asChild><Link href={side === "sales" ? "/sales/new" : "/purchases/new"}><PlusIcon /> {side === "sales" ? "New invoice" : "New purchase"}</Link></Button>}
        </>}
      />
      <Tabs value={docType} onValueChange={(v) => setDocType(v as DocType | "all")} className="mb-4">
        <TabsList>
          {types.map((t) => <TabsTrigger key={t} value={t}>{DOC_LABEL[t]}</TabsTrigger>)}
          <TabsTrigger value="all">All</TabsTrigger>
        </TabsList>
      </Tabs>
      <DataTable<DocumentSummary>
        queryKey={["documents", side]} path="/documents" columns={columns}
        searchPlaceholder="Number, party or reference…"
        filters={{ doc_type: docType === "all" ? types : docType, status_filter: status || undefined, payment_status: payment || undefined }}
        toolbar={<>
          <NativeSelect className="w-36" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="">Any status</option><option value="draft">Draft</option><option value="final">Final</option><option value="cancelled">Cancelled</option>
          </NativeSelect>
          <NativeSelect className="w-36" value={payment} onChange={(e) => setPayment(e.target.value)} aria-label="Payment">
            <option value="">Any payment</option><option value="unpaid">Unpaid</option><option value="partial">Partly paid</option><option value="paid">Paid</option>
          </NativeSelect>
        </>}
        onRowClick={(d) => router.push(`/documents/${d.id}`)}
      />
    </>
  );
}
