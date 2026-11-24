"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftIcon, CheckCircle2Icon, CopyPlusIcon, FileDownIcon, MoreHorizontalIcon, PencilIcon, PrinterIcon, SendIcon, WalletIcon, XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/data/confirm";
import { ErrorState } from "@/components/data/states";
import { DocStatusBadge } from "@/components/data/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Attachments } from "@/features/files/attachments";
import { HelpButton } from "@/features/help/help-button";
import { useBusiness } from "@/hooks/use-business";
import { api, ApiError, openFile } from "@/lib/api";
import { date, money, qty, todayISO } from "@/lib/format";
import { DOC_LABEL, type DocType, type DocumentFull } from "@/lib/types";

const CONVERT: Partial<Record<DocType, DocType[]>> = {
  quotation: ["sales_order", "proforma", "sales_invoice"], estimate: ["sales_order", "sales_invoice"],
  proforma: ["sales_invoice"], sales_order: ["delivery_challan", "sales_invoice"], delivery_challan: ["sales_invoice"],
  sales_invoice: ["credit_note", "debit_note"], purchase_order: ["purchase_invoice"], purchase_invoice: ["purchase_return"],
};

function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const { base, id: bizId, can, isOwner } = useBusiness();
  const [printOpen, setPrintOpen] = useState(params.get("print") === "1");
  const [paper, setPaper] = useState("");
  const [cancelOpen, setCancelOpen] = useState(false);
  const [sendOpen, setSendOpen] = useState(false);
  const key = ["biz", bizId, "documents", id];
  const { data: d, isLoading, error, refetch } = useQuery({ queryKey: key, queryFn: () => api<DocumentFull>(`${base}/documents/${id}`), enabled: !!bizId });

  const printHtml = useQuery({
    queryKey: [...key, "print", paper], enabled: printOpen && d?.status === "final",
    queryFn: async () => (await api<Response>(`${base}/documents/${id}/print`, { raw: true, query: { format: "html", paper: paper || undefined } })).text(),
  });
  const preview = printOpen && d?.status === "final" ? (printHtml.data ?? "") : null;
  const showPreview = () => setPrintOpen(true);

  const action = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => api<DocumentFull>(`${base}/documents/${id}/${path}`, { body: body ?? {} }),
    onSuccess: (out, v) => {
      qc.invalidateQueries({ queryKey: ["biz", bizId] });
      if (v.path === "convert") { toast.success(`${DOC_LABEL[out.doc_type]} draft created`); router.push(`/documents/${out.id}`); }
      else toast.success(v.path === "finalize" ? `Finalized as ${out.number}` : "Done");
    },
    onError: (e: ApiError) => toast.error(e.message),
  });

  if (isLoading) return <Skeleton className="h-[480px]" />;
  if (error || !d) return <Card><ErrorState error={error} retry={() => refetch()} /></Card>;

  const sales = !d.doc_type.startsWith("purchase");
  const canEdit = isOwner || can(sales ? "invoices.edit" : "purchases.edit");
  const canCancel = isOwner || can(sales ? "invoices.cancel" : "purchases.approve");
  const canCreate = isOwner || can(sales ? "invoices.create" : "purchases.create");
  const payable = ["sales_invoice", "debit_note", "purchase_invoice"].includes(d.doc_type);
  const tax = d.is_interstate ? [["IGST", d.igst_total]] : [["CGST", d.cgst_total], ["SGST", d.sgst_total]];

  return (
    <>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <Link href={sales ? "/sales" : "/purchases"} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeftIcon className="size-4" /> Back</Link>
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-semibold tracking-tight">
            {DOC_LABEL[d.doc_type]} {d.number ?? <span className="text-muted-foreground">(draft)</span>}
            <DocStatusBadge status={d.status} payment={d.payment_status} />
            <HelpButton topic={sales ? "document" : "purchases"} />
          </h1>
          <p className="text-sm text-muted-foreground">{d.party_name ?? "Walk-in customer"} · {date(d.doc_date)}{d.due_date && ` · due ${date(d.due_date)}`}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {d.status === "draft" && canEdit && <Button variant="outline" asChild><Link href={`${sales ? "/sales" : "/purchases"}/new?draft=${d.id}`}><PencilIcon /> Edit</Link></Button>}
          {d.status === "draft" && canCreate && (
            <Button onClick={() => action.mutate({ path: "finalize", body: { payments: [] } })} disabled={action.isPending}><CheckCircle2Icon /> Finalize</Button>
          )}
          {d.status === "final" && <>
            <Button variant="outline" onClick={() => showPreview()}><PrinterIcon /> Print</Button>
            <Button variant="outline" onClick={() => openFile(`${base}/documents/${id}/print`, { format: "pdf", paper: paper || undefined }, `${(d.number ?? "document").replace(/\//g, "-")}.pdf`)}><FileDownIcon /> PDF</Button>
            {payable && Number(d.balance_due) > 0 && (isOwner || can("payments.create")) && (
              <Button asChild><Link href={`/payments?new=1&party=${d.party_id ?? ""}&doc=${d.id}&dir=${sales ? "in" : "out"}`}><WalletIcon /> Record payment</Link></Button>
            )}
          </>}
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button variant="outline" size="icon" aria-label="More actions"><MoreHorizontalIcon /></Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {canCreate && d.status === "final" && (CONVERT[d.doc_type] ?? []).map((t) => (
                <DropdownMenuItem key={t} onSelect={() => action.mutate({ path: "convert", body: { target: t, doc_date: todayISO() } })}>
                  <CopyPlusIcon /> Create {DOC_LABEL[t].toLowerCase()}
                </DropdownMenuItem>
              ))}
              {d.status === "final" && sales && <DropdownMenuItem onSelect={() => setSendOpen(true)}><SendIcon /> Send to customer</DropdownMenuItem>}
              {d.status !== "cancelled" && canCancel && <><DropdownMenuSeparator /><DropdownMenuItem onSelect={() => setCancelOpen(true)} className="text-destructive"><XCircleIcon className="text-destructive!" /> {d.status === "draft" ? "Delete draft" : "Cancel document"}</DropdownMenuItem></>}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
        <Card className="overflow-hidden">
          <Table>
            <TableHeader><TableRow className="hover:bg-transparent">
              <TableHead>#</TableHead><TableHead>Item</TableHead><TableHead className="text-right">Qty</TableHead>
              <TableHead className="text-right">Rate</TableHead><TableHead className="hidden text-right md:table-cell">Taxable</TableHead>
              <TableHead className="hidden text-right md:table-cell">GST</TableHead><TableHead className="text-right">Amount</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {d.lines.map((l) => (
                <TableRow key={l.id}>
                  <TableCell className="text-muted-foreground">{l.line_no}</TableCell>
                  <TableCell className="whitespace-normal"><p className="font-medium">{l.description}</p><p className="text-xs text-muted-foreground">{l.hsn_sac && `HSN ${l.hsn_sac}`}{(l.batch_no as string) && ` · Batch ${l.batch_no}`}</p></TableCell>
                  <TableCell className="num text-right">{qty(l.quantity)} {l.unit_code}</TableCell>
                  <TableCell className="num text-right">{money(l.unit_price)}{Number(l.discount_percent) > 0 && <span className="block text-xs text-muted-foreground">-{Number(l.discount_percent)}%</span>}</TableCell>
                  <TableCell className="num hidden text-right md:table-cell">{money(l.taxable_value)}</TableCell>
                  <TableCell className="num hidden text-right text-muted-foreground md:table-cell">{Number(l.gst_rate)}%</TableCell>
                  <TableCell className="num text-right font-medium">{money(l.line_total)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {(d.notes as string) && <p className="border-t px-4 py-3 text-sm text-muted-foreground">{d.notes as string}</p>}
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Totals</CardTitle></CardHeader>
            <CardContent className="space-y-2 text-sm">
              <Line label="Taxable value" v={d.taxable_total} />
              {tax.map(([k, v]) => <Line key={k} label={k!} v={v} />)}
              {Number(d.cess_total ?? 0) > 0 && <Line label="Cess" v={d.cess_total} />}
              {Number(d.round_off ?? 0) !== 0 && <Line label="Round off" v={d.round_off} />}
              <div className="flex items-baseline justify-between border-t pt-2"><span className="font-medium">Total</span><span className="num text-2xl font-semibold">{money(d.grand_total)}</span></div>
              {payable && d.status === "final" && <>
                <Line label="Paid" v={d.amount_paid} />
                <div className="flex justify-between font-medium"><span>Balance due</span><span className="num">{money(d.balance_due)}</span></div>
              </>}
            </CardContent>
          </Card>
          <Card>
            <CardContent className="space-y-2 text-sm">
              <Meta k="Place of supply" v={d.place_of_supply ?? "—"} />
              <Meta k="Supply" v={d.is_interstate ? "Inter-state (IGST)" : "Intra-state"} />
              {d.seller_gstin && <Meta k="Our GSTIN" v={d.seller_gstin} />}
              {(d.irn as string) && <Meta k="IRN" v={<Badge variant="blue">e-invoice registered</Badge>} />}
              {(d.cancel_reason as string) && <Meta k="Cancelled" v={d.cancel_reason as string} />}
            </CardContent>
          </Card>
          <Attachments entityType="document" entityId={d.id} folder={sales ? "/Sales" : "/Purchases"} />
        </div>
      </div>

      <Dialog open={preview !== null} onOpenChange={(v) => !v && setPrintOpen(false)}>
        <DialogContent className="max-w-5xl gap-3 p-4">
          <DialogHeader className="flex-row items-center justify-between pr-8">
            <DialogTitle>Print preview</DialogTitle>
            <div className="flex items-center gap-2">
              <NativeSelect className="h-8 w-36" value={paper} onChange={(e) => setPaper(e.target.value)} aria-label="Paper">
                <option value="">Template default</option><option value="a4">A4</option><option value="a5">A5</option>
                <option value="thermal_80">Thermal 80 mm</option><option value="thermal_58">Thermal 58 mm</option>
              </NativeSelect>
              <Button size="sm" onClick={() => (document.getElementById("print-frame") as HTMLIFrameElement | null)?.contentWindow?.print()}><PrinterIcon /> Print</Button>
            </div>
          </DialogHeader>
          {/* The document is rendered by the server with its own strict CSP; sandboxed here (no scripts). */}
          <iframe id="print-frame" title="Document preview" srcDoc={preview ?? ""} sandbox="allow-modals allow-same-origin" className="h-[75vh] w-full rounded-md border bg-white" />
        </DialogContent>
      </Dialog>

      <ConfirmDialog open={cancelOpen} onOpenChange={setCancelOpen} destructive reason={d.status !== "draft"}
        title={d.status === "draft" ? "Delete this draft?" : `Cancel ${DOC_LABEL[d.doc_type].toLowerCase()} ${d.number}?`}
        description={d.status === "draft" ? "The draft is removed. Nothing was posted." : "Stock and accounts are reversed with today's date. The number stays used and the document stays on record."}
        confirm={d.status === "draft" ? "Delete draft" : "Cancel document"}
        onConfirm={async (reason) => {
          if (d.status === "draft") {
            await api(`${base}/documents/${id}`, { method: "DELETE" });
            toast.success("Draft deleted");
            router.push(sales ? "/sales" : "/purchases");
          } else await action.mutateAsync({ path: "cancel", body: { reason, cancel_date: todayISO() } });
        }} />
      <SendDialog open={sendOpen} onOpenChange={setSendOpen} documentId={d.id} />
    </>
  );
}

function SendDialog({ open, onOpenChange, documentId }: { open: boolean; onOpenChange: (v: boolean) => void; documentId: string }) {
  const { base } = useBusiness();
  const [channel, setChannel] = useState("email");
  const [recipient, setRecipient] = useState("");
  const [message, setMessage] = useState("");
  const send = useMutation({
    mutationFn: () => api(`${base}/documents/${documentId}/send`, { body: { channel, recipient: recipient || undefined, message: message || undefined } }),
    onSuccess: () => { toast.success("Queued — the background worker sends it shortly"); onOpenChange(false); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>Send document</DialogTitle></DialogHeader>
        <div className="grid gap-4">
          <Field label="Channel"><NativeSelect value={channel} onChange={(e) => setChannel(e.target.value)}><option value="email">E-mail (PDF attached)</option><option value="whatsapp">WhatsApp</option><option value="sms">SMS</option></NativeSelect></Field>
          <Field label="Recipient" hint="Leave empty to use the customer's saved e-mail or phone."><Input value={recipient} onChange={(e) => setRecipient(e.target.value)} /></Field>
          <Field label="Message (optional)"><Textarea value={message} onChange={(e) => setMessage(e.target.value)} /></Field>
        </div>
        <DialogFooter><Button onClick={() => send.mutate()} disabled={send.isPending}><SendIcon /> Send</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Line({ label, v }: { label: string; v?: string | unknown }) {
  return <div className="flex justify-between"><span className="text-muted-foreground">{label}</span><span className="num">{money(v as string)}</span></div>;
}
function Meta({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-3"><span className="text-muted-foreground">{k}</span><span className="text-right">{v}</span></div>;
}

export default function Page() {
  return <Suspense><DocumentPage /></Suspense>;
}
