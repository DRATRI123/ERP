"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  BanknoteIcon, CheckCircle2Icon, CreditCardIcon, Loader2Icon, MinusIcon, PlusIcon, SaveIcon, SmartphoneIcon, Trash2Icon, WalletIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { PartyPicker } from "@/components/pickers/party-picker";
import { PartyDialog } from "@/features/parties/party-form";
import { ProductSearch, type ProductSearchHandle } from "@/components/pickers/product-search";
import { EmptyState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useBusiness } from "@/hooks/use-business";
import { useDebounced } from "@/hooks/use-debounce";
import { api, ApiError } from "@/lib/api";
import { dec, decStr, money, qty, todayISO } from "@/lib/format";
import { DOC_LABEL, PURCHASE_TYPES, SALES_TYPES, type DocType, type DocumentFull, type Party, type Product } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Line {
  key: string; product_id: string | null; name: string; sku?: string; hsn?: string | null; unit?: string;
  quantity: string; unit_price: string; discount_percent: string; gst_rate?: string; stock?: string | null;
}
interface PayRow { key: string; mode: string; amount: string; reference: string }

const MODES = [
  { v: "upi", label: "UPI", icon: SmartphoneIcon }, { v: "cash", label: "Cash", icon: BanknoteIcon },
  { v: "card", label: "Card", icon: CreditCardIcon }, { v: "bank_transfer", label: "Bank", icon: WalletIcon },
  { v: "cheque", label: "Cheque", icon: WalletIcon },
];
const uid = () => Math.random().toString(36).slice(2, 10);

/** Loads a draft (if editing one), then renders the editor initialised from it. */
export function DocumentEditor({ side, draftId }: { side: "sales" | "purchase"; draftId?: string }) {
  const { base, id: bizId } = useBusiness();
  const draft = useQuery({
    queryKey: ["biz", bizId, "documents", draftId], enabled: !!draftId && !!bizId,
    queryFn: () => api<DocumentFull>(`${base}/documents/${draftId}`),
  });
  if (draftId && !draft.data) return draft.error ? <EmptyState title="Draft not found" /> : <Skeleton className="h-96" />;
  return <Editor side={side} draftId={draftId} draft={draft.data} />;
}

function Editor({ side, draftId, draft }: { side: "sales" | "purchase"; draftId?: string; draft?: DocumentFull }) {
  const router = useRouter();
  const { base, id: bizId, can, isOwner } = useBusiness();
  const types = side === "sales" ? SALES_TYPES : PURCHASE_TYPES;
  const [docType, setDocType] = useState<DocType>(draft?.doc_type ?? (side === "sales" ? "sales_invoice" : "purchase_invoice"));
  const [party, setParty] = useState<Party | null>(draft?.party_id ? ({ id: draft.party_id, name: draft.party_name ?? "" } as Party) : null);
  const [partyName, setPartyName] = useState(draft && !draft.party_id ? (draft.party_name ?? "") : "");
  const [docDate, setDocDate] = useState(draft?.doc_date ?? todayISO());
  const [dueDate, setDueDate] = useState(draft?.due_date ?? "");
  const [supplierNo, setSupplierNo] = useState((draft?.supplier_invoice_no as string | undefined) ?? "");
  const [notes, setNotes] = useState((draft?.notes as string | undefined) ?? "");
  const [lines, setLines] = useState<Line[]>(() => (draft?.lines ?? []).map((l) => ({
    key: uid(), product_id: l.product_id, name: l.description, hsn: l.hsn_sac, unit: l.unit_code ?? undefined,
    quantity: String(Number(l.quantity)), unit_price: String(Number(l.unit_price)), discount_percent: String(Number(l.discount_percent)), gst_rate: l.gst_rate })));
  const [payments, setPayments] = useState<PayRow[]>([]);
  const [newParty, setNewParty] = useState<string | null>(null);
  const search = useRef<ProductSearchHandle>(null);
  const partyBtn = useRef<HTMLButtonElement>(null);

  // ------------------------------------------------------------------ server-side totals
  const body = useMemo(() => ({
    doc_type: docType, doc_date: docDate, due_date: dueDate || undefined,
    party_id: party?.id, party_name: party ? undefined : partyName || undefined,
    supplier_invoice_no: side === "purchase" ? supplierNo || undefined : undefined,
    notes: notes || undefined,
    lines: lines.map((l) => ({
      product_id: l.product_id ?? undefined, description: l.product_id ? undefined : l.name,
      quantity: l.quantity || "0", unit_price: l.unit_price || undefined,
      discount_percent: l.discount_percent && l.discount_percent !== "0" ? l.discount_percent : undefined,
      ...(l.product_id ? {} : { gst_rate: l.gst_rate ?? "0" }),
    })),
  }), [docType, docDate, dueDate, party, partyName, supplierNo, notes, lines, side]);
  const previewBody = useDebounced(body, 250);
  const validLines = previewBody.lines.length > 0 && previewBody.lines.every((l) => Number(l.quantity) > 0);
  const preview = useQuery({
    queryKey: ["biz", bizId, "doc-preview", previewBody], enabled: validLines,
    queryFn: () => api<DocumentFull>(`${base}/documents/preview`, { body: previewBody }),
    placeholderData: (p) => p, retry: false, staleTime: 60_000,
  });
  const totals = lines.length ? preview.data : undefined;
  const grand = totals?.grand_total ?? "0";
  const paid = payments.reduce((s, p) => s + dec(p.amount || "0"), 0n);
  const due = dec(grand) - paid;

  // ------------------------------------------------------------------ editing
  const addProduct = useCallback((p: Product) => {
    setLines((ls) => {
      const i = ls.findIndex((l) => l.product_id === p.id);
      if (i >= 0) {
        const copy = [...ls];
        const l = copy[i]!;
        copy[i] = { ...l, quantity: String(Number(l.quantity || 0) + 1) };
        return copy;
      }
      const price = side === "sales" ? p.selling_price : (p.purchase_price ?? p.selling_price);
      return [...ls, { key: uid(), product_id: p.id, name: p.name, sku: p.sku, hsn: p.hsn_sac, quantity: "1",
        unit_price: price ? String(Number(price)) : "", discount_percent: "", gst_rate: p.gst_rate, stock: (p.on_hand as string) ?? null }];
    });
  }, [side]);
  const update = (key: string, patch: Partial<Line>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const remove = (key: string) => setLines((ls) => ls.filter((l) => l.key !== key));

  // ------------------------------------------------------------------ save / finalize
  const save = useMutation({
    mutationFn: async (finalize: boolean) => {
      const doc = draftId
        ? await api<DocumentFull>(`${base}/documents/${draftId}`, { method: "PATCH", body: { ...body, doc_type: undefined } })
        : await api<DocumentFull>(`${base}/documents`, { body: { ...body, client_ref: `ui-${uid()}${uid()}` } });
      if (!finalize) return doc;
      return api<DocumentFull>(`${base}/documents/${doc.id}/finalize`, {
        body: { payments: payments.filter((p) => Number(p.amount) > 0).map((p) => ({ mode: p.mode, amount: p.amount, reference: p.reference || undefined })) },
      });
    },
    onSuccess: (doc, finalize) => {
      toast.success(finalize ? `${DOC_LABEL[doc.doc_type]} ${doc.number} saved` : "Draft saved");
      router.push(`/documents/${doc.id}${finalize ? "?print=1" : ""}`);
    },
    onError: (e: ApiError) => toast.error(e.message, { description: Object.values(e.fieldErrors)[0] }),
  });
  const canSave = lines.length > 0 && lines.every((l) => Number(l.quantity) > 0) && !save.isPending;
  const finalizeLabel = docType === "sales_invoice" || docType === "purchase_invoice" ? "Save & finalize" : "Save & issue";

  // ------------------------------------------------------------------ shortcuts
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "F2") { e.preventDefault(); search.current?.focus(); }
      else if (e.key === "F4") { e.preventDefault(); partyBtn.current?.click(); }
      else if (e.key === "F9") { e.preventDefault(); if (canSave) save.mutate(true); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (canSave) save.mutate(false); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canSave, save]);
  useEffect(() => { search.current?.focus(); }, []);

  if (!isOwner && !can(side === "sales" ? "invoices.create" : "purchases.create")) {
    return <EmptyState title="You can't create documents here" description="Ask for the create permission." />;
  }

  const lineTotals = new Map((totals?.lines ?? []).map((l, i) => [i, l]));
  const tax = totals ? (totals.is_interstate ? [["IGST", totals.igst_total]] : [["CGST", totals.cgst_total], ["SGST", totals.sgst_total]]) : [];

  return (
    <div className="grid gap-5 xl:grid-cols-[1fr_360px]">
      <div className="min-w-0 space-y-4">
        <Card>
          <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Document">
              <NativeSelect value={docType} onChange={(e) => setDocType(e.target.value as DocType)} disabled={!!draftId}>
                {types.map((t) => <option key={t} value={t}>{DOC_LABEL[t]}</option>)}
              </NativeSelect>
            </Field>
            <Field label={<>{side === "sales" ? "Customer" : "Supplier"} <kbd className="rounded border px-1 text-[10px] text-muted-foreground">F4</kbd></>} className="sm:col-span-2">
              <PartyPicker ref={partyBtn} kind={side === "sales" ? "customers" : "suppliers"} value={party} onChange={setParty}
                placeholder={side === "sales" ? "Walk-in customer" : "Choose supplier"}
                onCreate={(isOwner || can(side === "sales" ? "customers.manage" : "suppliers.manage")) ? setNewParty : undefined} />
            </Field>
            <Field label="Date"><Input type="date" value={docDate} onChange={(e) => setDocDate(e.target.value)} /></Field>
            {side === "sales" && !party && (
              <Field label="Walk-in name (optional)" className="sm:col-span-2"><Input value={partyName} onChange={(e) => setPartyName(e.target.value)} placeholder="Printed on the bill" /></Field>
            )}
            {side === "purchase" && (
              <Field label="Supplier bill no."><Input value={supplierNo} onChange={(e) => setSupplierNo(e.target.value)} /></Field>
            )}
            {party && <Field label="Due date"><Input type="date" value={dueDate} min={docDate} onChange={(e) => setDueDate(e.target.value)} /></Field>}
          </CardContent>
        </Card>

        <Card className="overflow-visible">
          <div className="border-b p-3"><ProductSearch ref={search} onPick={addProduct} /></div>
          {lines.length === 0 ? (
            <EmptyState title="Add items to start" description="Scan a barcode or type a product name, SKU or HSN, then press Enter. F2 jumps back here." />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/40 text-xs text-muted-foreground uppercase">
                  <tr>
                    <th className="w-8 px-3 py-2 text-left">#</th><th className="px-3 py-2 text-left">Item</th>
                    <th className="w-28 px-2 py-2 text-center">Qty</th><th className="w-32 px-2 py-2 text-right">Rate</th>
                    <th className="w-20 px-2 py-2 text-right">Disc %</th><th className="hidden w-16 px-2 py-2 text-right md:table-cell">GST</th>
                    <th className="w-32 px-3 py-2 text-right">Amount</th><th className="w-10" />
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {lines.map((l, i) => {
                    const t = lineTotals.get(i);
                    const low = l.stock !== undefined && l.stock !== null && side === "sales" && Number(l.stock) < Number(l.quantity || 0);
                    return (
                      <tr key={l.key} className="align-middle">
                        <td className="px-3 py-2 text-muted-foreground">{i + 1}</td>
                        <td className="px-3 py-2">
                          {l.product_id ? <p className="font-medium">{l.name}</p> : (
                            <Input value={l.name} onChange={(e) => update(l.key, { name: e.target.value })} placeholder="Description" className="h-8" />
                          )}
                          <p className="text-xs text-muted-foreground">{[l.sku, l.hsn && `HSN ${l.hsn}`].filter(Boolean).join(" · ")}
                            {low && <span className="ml-1 text-destructive">· only {qty(l.stock)} in stock</span>}</p>
                        </td>
                        <td className="px-2 py-2">
                          <div className="flex items-center justify-center gap-1">
                            <Button type="button" variant="ghost" size="icon-sm" className="size-7" onClick={() => update(l.key, { quantity: String(Math.max(1, Number(l.quantity || 0) - 1)) })} aria-label="Decrease"><MinusIcon /></Button>
                            <Input value={l.quantity} inputMode="decimal" onChange={(e) => update(l.key, { quantity: e.target.value.replace(/[^\d.]/g, "") })} className="num h-8 w-14 px-1 text-center" aria-label="Quantity" />
                            <Button type="button" variant="ghost" size="icon-sm" className="size-7" onClick={() => update(l.key, { quantity: String(Number(l.quantity || 0) + 1) })} aria-label="Increase"><PlusIcon /></Button>
                          </div>
                        </td>
                        <td className="px-2 py-2"><Input value={l.unit_price} inputMode="decimal" onChange={(e) => update(l.key, { unit_price: e.target.value.replace(/[^\d.]/g, "") })} className="num h-8 text-right" aria-label="Rate" /></td>
                        <td className="px-2 py-2"><Input value={l.discount_percent} inputMode="decimal" placeholder="0" onChange={(e) => update(l.key, { discount_percent: e.target.value.replace(/[^\d.]/g, "") })} className="num h-8 text-right" aria-label="Discount percent" /></td>
                        <td className="num hidden px-2 py-2 text-right text-muted-foreground md:table-cell">{l.gst_rate ? `${Number(l.gst_rate)}%` : "—"}</td>
                        <td className="num px-3 py-2 text-right font-medium">{t ? money(t.line_total) : preview.isFetching ? <Skeleton className="ml-auto h-4 w-16" /> : "—"}</td>
                        <td className="px-1"><Button type="button" variant="ghost" size="icon-sm" onClick={() => remove(l.key)} aria-label="Remove line"><Trash2Icon className="text-muted-foreground" /></Button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div className="flex items-center justify-between border-t px-3 py-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setLines((ls) => [...ls, { key: uid(), product_id: null, name: "", quantity: "1", unit_price: "", discount_percent: "", gst_rate: "18" }])}>
              <PlusIcon /> Add service / custom line
            </Button>
            <span className="text-xs text-muted-foreground">{lines.length} item{lines.length === 1 ? "" : "s"} · {qty(lines.reduce((s, l) => s + Number(l.quantity || 0), 0))} units</span>
          </div>
        </Card>
        <Card><CardContent><Field label="Notes (printed)"><Textarea value={notes} onChange={(e) => setNotes(e.target.value)} className="min-h-14" /></Field></CardContent></Card>
      </div>

      <div className="xl:sticky xl:top-20 xl:self-start">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Summary</CardTitle>
            {preview.isFetching ? <Loader2Icon className="size-4 animate-spin text-muted-foreground" /> : totals && <Badge variant="blue">{totals.is_interstate ? "Inter-state · IGST" : "Intra-state · CGST+SGST"}</Badge>}
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {preview.error && <p className="rounded-md bg-destructive/5 px-2 py-1.5 text-xs text-destructive">{(preview.error as ApiError).message}</p>}
            <Row label="Subtotal" value={totals?.subtotal} />
            {totals && Number(totals.discount_total) > 0 && <Row label="Discount" value={`-${totals.discount_total}`} />}
            <Row label="Taxable value" value={totals?.taxable_total} />
            {tax.map(([k, v]) => <Row key={k} label={k!} value={v} />)}
            {totals && Number(totals.cess_total) > 0 && <Row label="Cess" value={totals.cess_total} />}
            {totals && Number(totals.round_off) !== 0 && <Row label="Round off" value={totals.round_off} />}
            <div className="flex items-baseline justify-between border-t pt-3">
              <span className="font-medium">Total</span>
              <span className="num text-3xl font-semibold tracking-tight">{money(grand)}</span>
            </div>
          </CardContent>
          {(docType === "sales_invoice" || docType === "purchase_invoice") && (
            <CardContent className="space-y-3 border-t">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium">{side === "sales" ? "Payment received" : "Payment made"}</p>
                {payments.length > 0 && <Button variant="link" size="sm" className="h-auto p-0" onClick={() => setPayments([])}>Clear</Button>}
              </div>
              <div className="grid grid-cols-3 gap-2">
                {MODES.slice(0, 3).map((m) => (
                  <Button key={m.v} type="button" variant="outline" size="sm" disabled={dec(grand) <= 0n}
                    onClick={() => setPayments([{ key: uid(), mode: m.v, amount: decStr(dec(grand)), reference: "" }])}>
                    <m.icon /> {m.label}
                  </Button>
                ))}
              </div>
              {payments.map((p) => (
                <div key={p.key} className="flex gap-2">
                  <NativeSelect value={p.mode} className="w-28" onChange={(e) => setPayments((ps) => ps.map((x) => (x.key === p.key ? { ...x, mode: e.target.value } : x)))}>
                    {MODES.map((m) => <option key={m.v} value={m.v}>{m.label}</option>)}
                  </NativeSelect>
                  <Input value={p.amount} inputMode="decimal" className="num text-right" aria-label="Amount"
                    onChange={(e) => setPayments((ps) => ps.map((x) => (x.key === p.key ? { ...x, amount: e.target.value.replace(/[^\d.]/g, "") } : x)))} />
                  <Button variant="ghost" size="icon" onClick={() => setPayments((ps) => ps.filter((x) => x.key !== p.key))} aria-label="Remove payment"><Trash2Icon /></Button>
                </div>
              ))}
              {payments.length > 0 && payments.length < 5 && (
                <Button variant="ghost" size="sm" onClick={() => setPayments((ps) => [...ps, { key: uid(), mode: "cash", amount: due > 0n ? decStr(due) : "", reference: "" }])}>
                  <PlusIcon /> Split payment
                </Button>
              )}
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Balance due</span>
                <span className={cn("num font-medium", due > 0n ? "text-amber-600 dark:text-warning" : "text-primary")}>{money(decStr(due > 0n ? due : 0n))}</span>
              </div>
              {side === "sales" && !party && due > 0n && lines.length > 0 && (
                <p className="text-xs text-muted-foreground">Walk-in sales must be paid in full, or choose a customer.</p>
              )}
            </CardContent>
          )}
          <CardContent className="grid gap-2 border-t">
            <Button size="lg" disabled={!canSave} onClick={() => save.mutate(true)}>
              {save.isPending ? <Loader2Icon className="animate-spin" /> : <CheckCircle2Icon />} {finalizeLabel} <kbd className="ml-auto rounded bg-white/15 px-1.5 text-[11px]">F9</kbd>
            </Button>
            <Button variant="outline" disabled={!canSave} onClick={() => save.mutate(false)}>
              <SaveIcon /> Save draft <kbd className="ml-auto rounded border px-1.5 text-[11px] text-muted-foreground">Ctrl S</kbd>
            </Button>
          </CardContent>
        </Card>
      </div>
      <PartyDialog kind={side === "sales" ? "customers" : "suppliers"} open={newParty !== null} initialName={newParty ?? undefined}
        onOpenChange={(v) => !v && setNewParty(null)} onSaved={(p) => { setParty(p); setNewParty(null); }} />
    </div>
  );
}

function Row({ label, value }: { label: string; value?: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="num">{value !== undefined ? money(value) : "—"}</span>
    </div>
  );
}
