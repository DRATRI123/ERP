"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  AlertTriangleIcon, ArrowLeftRightIcon, StarIcon, BanknoteIcon, ClockAlertIcon, StethoscopeIcon, CheckCircle2Icon, GiftIcon, TagIcon, CreditCardIcon, Loader2Icon, MinusIcon, PlusIcon, SaveIcon, SmartphoneIcon, Trash2Icon, WalletIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { PartyPicker } from "@/components/pickers/party-picker";
import { PartyDialog } from "@/features/parties/party-form";
import { DoctorSelect, REGISTER_SCHEDULES, RX_SCHEDULES, ScheduleBadge, SubstitutesDialog } from "@/features/pharmacy/pharmacy";
import { ProductSearch, type ProductSearchHandle } from "@/components/pickers/product-search";
import { EmptyState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useBusiness } from "@/hooks/use-business";
import { useModules } from "@/hooks/use-modules";
import { useDebounced } from "@/hooks/use-debounce";
import { api, ApiError } from "@/lib/api";
import { dec, decStr, money, qty, todayISO } from "@/lib/format";
import { DOC_LABEL, PURCHASE_TYPES, SALES_TYPES, type DocType, type DocumentFull, type DocumentLine, type Party, type Product } from "@/lib/types";
import { useT } from "@/lib/i18n";
import { enqueueBill, estimateTotal, isNetworkError, useOnline } from "@/lib/offline";
import { cn } from "@/lib/utils";

interface Line {
  key: string; product_id: string | null; name: string; sku?: string; hsn?: string | null; unit?: string;
  quantity: string; unit_price: string; discount_percent: string; gst_rate?: string; stock?: string | null;
  batched?: boolean; batch_no?: string; expiry_date?: string; mrp?: string;
  priceTyped?: boolean; // only typed rates are sent; otherwise the server prices (price lists, tiers)
  incl?: boolean; // price includes GST (for the offline estimate)
  schedule?: string; rack?: string | null; composition?: string | null; packSize?: number; packLabel?: string | null;
}
const TIERS = [["retail", "Retail"], ["wholesale", "Wholesale"], ["mrp", "MRP"], ["ptr", "PTR (to retailer)"], ["pts", "PTS (to stockist)"]] as const;
/** 23 tablets in strips of 10 -> "2 strip + 3". */
const packs = (q: number, size: number, label?: string | null) => {
  const full = Math.floor(q / size), loose = Math.round((q - full * size) * 1000) / 1000;
  return [full ? `${full} ${label || "pack"}` : "", loose ? `${loose} loose` : ""].filter(Boolean).join(" + ");
};
const isFree = (l: DocumentLine) => !!(l.custom_fields as Record<string, unknown> | undefined)?.scheme_free;
interface AvailableBatch { batch_id: string; batch_no: string; expiry_date: string | null; mrp: string | null; quantity: string; days_left: number | null }
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
  const modules = useModules();
  const tr = useT();
  const pharmacy = modules.on("pharmacy");
  const [docType, setDocType] = useState<DocType>(draft?.doc_type ?? (side === "sales" ? "sales_invoice" : "purchase_invoice"));
  const [party, setParty] = useState<Party | null>(draft?.party_id ? ({ id: draft.party_id, name: draft.party_name ?? "" } as Party) : null);
  const [partyName, setPartyName] = useState(draft && !draft.party_id ? (draft.party_name ?? "") : "");
  const [docDate, setDocDate] = useState(draft?.doc_date ?? todayISO());
  const [dueDate, setDueDate] = useState(draft?.due_date ?? "");
  const [supplierNo, setSupplierNo] = useState((draft?.supplier_invoice_no as string | undefined) ?? "");
  const [notes, setNotes] = useState((draft?.notes as string | undefined) ?? "");
  const [tier, setTier] = useState((draft?.price_tier as string | undefined) ?? "retail");
  const [rx, setRx] = useState({ doctor_id: (draft?.doctor_id as string | null) ?? "", patient_name: (draft?.patient_name as string | null) ?? "",
    patient_address: (draft?.patient_address as string | null) ?? "", rx_number: (draft?.rx_number as string | null) ?? "" });
  const [showRx, setShowRx] = useState(!!draft?.doctor_id || !!draft?.patient_name);
  const [subsFor, setSubsFor] = useState<Line | null>(null);
  const [lines, setLines] = useState<Line[]>(() => (draft?.lines ?? []).filter((l) => !isFree(l)).map((l) => ({ priceTyped: true,
    key: uid(), product_id: l.product_id, name: l.description, hsn: l.hsn_sac, unit: l.unit_code ?? undefined,
    quantity: String(Number(l.quantity)), unit_price: String(Number(l.unit_price)), discount_percent: String(Number(l.discount_percent)), gst_rate: l.gst_rate,
    batched: !!l.batch_no, batch_no: (l.batch_no as string | null) ?? undefined, expiry_date: (l.expiry_date as string | null) ?? undefined,
    mrp: l.mrp ? String(Number(l.mrp)) : undefined })));
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
    ...(side === "sales" ? { price_tier: tier, doctor_id: rx.doctor_id || null, patient_name: rx.patient_name || null,
      patient_address: rx.patient_address || null, rx_number: rx.rx_number || null } : {}),
    lines: lines.map((l) => ({
      product_id: l.product_id ?? undefined, description: l.product_id ? undefined : l.name,
      quantity: l.quantity || "0", unit_price: (l.priceTyped || !l.product_id) && l.unit_price ? l.unit_price : undefined,
      discount_percent: l.discount_percent && l.discount_percent !== "0" ? l.discount_percent : undefined,
      ...(l.product_id ? {} : { gst_rate: l.gst_rate ?? "0" }),
      ...(l.batch_no ? { batch_no: l.batch_no } : {}),
      ...(side === "purchase" && l.batched ? { expiry_date: l.expiry_date || undefined, mrp: l.mrp || undefined } : {}),
    })),
  }), [docType, docDate, dueDate, party, partyName, supplierNo, notes, lines, side, tier, rx]);
  const previewBody = useDebounced(body, 250);
  const validLines = previewBody.lines.length > 0 && previewBody.lines.every((l) => Number(l.quantity) > 0);
  const preview = useQuery({
    queryKey: ["biz", bizId, "doc-preview", previewBody], enabled: validLines,
    queryFn: () => api<DocumentFull>(`${base}/documents/preview`, { body: previewBody }),
    placeholderData: (p) => p, retry: false, staleTime: 60_000,
  });
  const online = useOnline();
  const offline = !online || (!!preview.error && isNetworkError(preview.error));
  const estimate = estimateTotal(lines);
  const totals = lines.length && !offline ? preview.data : undefined;
  const grand = offline ? (lines.length ? `${estimate}.00` : "0") : (totals?.grand_total ?? "0");
  const paid = payments.reduce((s, p) => s + dec(p.amount || "0"), 0n);
  const points = useQuery({
    queryKey: ["biz", bizId, "loyalty", party?.id], enabled: side === "sales" && !!party && modules.on("loyalty"),
    queryFn: () => api<{ points: string; value: string; can_redeem: boolean }>(`${base}/customers/${party!.id}/loyalty`), staleTime: 15_000,
  });
  const pointsValue = points.data?.can_redeem ? dec(points.data.value) : 0n;
  const due = dec(grand) - paid;

  // ------------------------------------------------------------------ editing
  const addProduct = useCallback((p: Product) => {
    setLines((ls) => {
      // Batch-tracked purchases get a new line per scan (each pack may be another batch).
      const i = side === "purchase" && p.batch_tracking ? -1 : ls.findIndex((l) => l.product_id === p.id);
      if (i >= 0) {
        const copy = [...ls];
        const l = copy[i]!;
        copy[i] = { ...l, quantity: String(Number(l.quantity || 0) + 1) };
        return copy;
      }
      const price = side === "sales" ? p.selling_price : (p.purchase_price ?? p.selling_price);
      return [...ls, { key: uid(), product_id: p.id, name: p.name, sku: p.sku, hsn: p.hsn_sac, quantity: "1",
        unit_price: price ? String(Number(price)) : "", discount_percent: "", gst_rate: p.gst_rate, stock: (p.on_hand as string) ?? null,
        batched: p.batch_tracking && p.track_inventory, mrp: side === "purchase" && p.mrp ? String(Number(p.mrp)) : undefined,
        schedule: (p.drug_schedule as string | undefined) ?? undefined, rack: (p.rack as string | null) ?? null,
        composition: (p.composition as string | null) ?? null, packSize: p.pack_size ? Number(p.pack_size) : undefined,
        packLabel: (p.pack_label as string | null) ?? null, incl: p.prices_include_tax }];
    });
  }, [side, setLines]);
  /** Swap a line to a substitute (same salt), keeping its quantity. */
  const swap = (key: string, p: Product) => setLines((ls) => ls.map((l) => (l.key !== key ? l : {
    ...l, product_id: p.id, name: p.name, sku: p.sku, hsn: p.hsn_sac, unit_price: p.selling_price ? String(Number(p.selling_price)) : "",
    priceTyped: false, gst_rate: p.gst_rate, stock: (p.on_hand as string) ?? null, batched: p.batch_tracking && p.track_inventory,
    batch_no: undefined, schedule: (p.drug_schedule as string | undefined) ?? undefined, rack: (p.rack as string | null) ?? null,
    composition: (p.composition as string | null) ?? null, packSize: p.pack_size ? Number(p.pack_size) : undefined, packLabel: (p.pack_label as string | null) ?? null,
  })));
  const update = (key: string, patch: Partial<Line>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const remove = (key: string) => setLines((ls) => ls.filter((l) => l.key !== key));

  // ------------------------------------------------------------------ save / finalize
  /** No internet: keep a sales invoice in the device's queue; it is sent when the connection returns. */
  function queueOffline() {
    if (draftId || docType !== "sales_invoice") throw new Error("You are offline. Only new sales invoices can be saved offline.");
    const paidFull = paid >= dec(grand) && payments.length > 0;
    if (!party && !paidFull) throw new Error("Offline walk-in sales must be paid in full.");
    const ok = enqueueBill(bizId, {
      client_ref: `off-${uid()}${uid()}${uid()}`, label: `${party?.name ?? (partyName || "Walk-in")} · ${lines.length} item${lines.length === 1 ? "" : "s"}`,
      estimate: grand, body, pay_mode: paidFull ? payments[0]!.mode : null,
    });
    if (!ok) throw new Error("This device's storage is full — the bill could not be saved offline.");
    return null;
  }
  const save = useMutation({
    networkMode: "always", // offline, finalize queues the bill on this device
    mutationFn: async (finalize: boolean) => {
      if (finalize && offline) return queueOffline();
      let doc: DocumentFull;
      try {
        doc = draftId
        ? await api<DocumentFull>(`${base}/documents/${draftId}`, { method: "PATCH", body: { ...body, doc_type: undefined } })
        : await api<DocumentFull>(`${base}/documents`, { body: { ...body, client_ref: `ui-${uid()}${uid()}` } });
      } catch (e) {
        if (finalize && isNetworkError(e)) return queueOffline();
        throw e;
      }
      if (!finalize) return doc;
      return api<DocumentFull>(`${base}/documents/${doc.id}/finalize`, {
        body: { payments: payments.filter((p) => Number(p.amount) > 0).map((p) => ({ mode: p.mode, amount: p.amount, reference: p.reference || undefined })) },
      });
    },
    onSuccess: (doc, finalize) => {
      if (doc === null) {
        toast.success("Saved on this device — it will be sent when the internet is back", { description: `About ${money(grand)}` });
        setLines([]); setPayments([]); setParty(null); setPartyName(""); setNotes("");
        search.current?.focus();
        return;
      }
      toast.success(finalize ? `${DOC_LABEL[doc.doc_type]} ${doc.number} saved` : "Draft saved");
      router.push(`/documents/${doc.id}${finalize ? "?print=1" : ""}`);
    },
    onError: (e: Error) => toast.error(e.message, { description: e instanceof ApiError ? Object.values(e.fieldErrors)[0] : undefined }),
  });
  const needsRx = pharmacy && side === "sales" && lines.some((l) => l.schedule && RX_SCHEDULES.has(l.schedule));
  const needsRxNo = pharmacy && side === "sales" && lines.some((l) => l.schedule && REGISTER_SCHEDULES.has(l.schedule));
  const rxMissing = docType === "sales_invoice" && needsRx && (!rx.doctor_id || !rx.patient_name.trim() || (needsRxNo && !rx.rx_number.trim()));
  const missingBatch = side === "purchase" && docType === "purchase_invoice" && lines.some((l) => l.batched && !l.batch_no?.trim());
  const canSave = lines.length > 0 && lines.every((l) => Number(l.quantity) > 0) && !save.isPending && !missingBatch;
  const canFinalize = canSave && !rxMissing;
  const finalizeLabel = tr(docType === "sales_invoice" || docType === "purchase_invoice" ? "Save & finalize" : "Save & issue");

  // ------------------------------------------------------------------ shortcuts
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "F2") { e.preventDefault(); search.current?.focus(); }
      else if (e.key === "F4") { e.preventDefault(); partyBtn.current?.click(); }
      else if (e.key === "F9") { e.preventDefault(); if (canFinalize) save.mutate(true); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (canSave) save.mutate(false); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canSave, canFinalize, save]);
  useEffect(() => { search.current?.focus(); }, []);

  if (!isOwner && !can(side === "sales" ? "invoices.create" : "purchases.create")) {
    return <EmptyState title="You can't create documents here" description="Ask for the create permission." />;
  }

  const lineTotals = new Map((totals?.lines ?? []).filter((l) => !isFree(l)).map((l, i) => [i, l]));
  const freeLines = (totals?.lines ?? []).filter(isFree);
  const tax = totals ? (totals.is_interstate ? [["IGST", totals.igst_total]] : [["CGST", totals.cgst_total], ["SGST", totals.sgst_total]]) : [];

  return (
    <div className="grid gap-4 pb-16 sm:gap-5 xl:grid-cols-[1fr_360px] xl:pb-0">
      <div className="min-w-0 space-y-4">
        <Card>
          <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label={tr("Document")}>
              <NativeSelect value={docType} onChange={(e) => setDocType(e.target.value as DocType)} disabled={!!draftId}>
                {types.map((t) => <option key={t} value={t}>{tr(DOC_LABEL[t])}</option>)}
              </NativeSelect>
            </Field>
            <Field label={<>{tr(side === "sales" ? "Customer" : "Supplier")} <kbd className="rounded border px-1 text-[10px] text-muted-foreground">F4</kbd></>} className="sm:col-span-2">
              <PartyPicker ref={partyBtn} kind={side === "sales" ? "customers" : "suppliers"} value={party} onChange={setParty}
                placeholder={tr(side === "sales" ? "Walk-in customer" : "Choose supplier")}
                onCreate={(isOwner || can(side === "sales" ? "customers.manage" : "suppliers.manage")) ? setNewParty : undefined} />
            </Field>
            <Field label={tr("Date")} htmlFor="doc-date"><Input id="doc-date" type="date" value={docDate} onChange={(e) => setDocDate(e.target.value)} /></Field>
            {side === "sales" && !party && (
              <Field label={tr("Walk-in name (optional)")} className="sm:col-span-2"><Input value={partyName} onChange={(e) => setPartyName(e.target.value)} placeholder="Printed on the bill" /></Field>
            )}
            {side === "purchase" && (
              <Field label={tr("Supplier bill no.")} htmlFor="doc-supplier-no"><Input id="doc-supplier-no" value={supplierNo} onChange={(e) => setSupplierNo(e.target.value)} /></Field>
            )}
            {side === "sales" && (
              <Field label={tr("Rate")} htmlFor="doc-tier" hint={tier === "ptr" || tier === "pts" ? "Pharma trade rate" : undefined}>
                <NativeSelect id="doc-tier" value={tier} onChange={(e) => setTier(e.target.value)}>{TIERS.filter(([v]) => pharmacy || (v !== "ptr" && v !== "pts")).map(([v, l]) => <option key={v} value={v}>{tr(l)}</option>)}</NativeSelect>
              </Field>
            )}
            {side === "sales" && party && <CreditPanel partyId={party.id} />}
            {party && <Field label={tr("Due date")} htmlFor="doc-due"><Input id="doc-due" type="date" value={dueDate} min={docDate} onChange={(e) => setDueDate(e.target.value)} /></Field>}
          </CardContent>
        </Card>

        {pharmacy && side === "sales" && (needsRx || showRx) && (
          <Card className={cn(rxMissing && "border-warning/60")}>
            <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div className="flex items-center gap-2 sm:col-span-2 lg:col-span-4">
                <StethoscopeIcon className="size-4 text-primary" />
                <p className="text-sm font-medium">Prescription</p>
                {needsRx && <p className="text-xs text-muted-foreground">Required: this bill has {needsRxNo ? "a Schedule H1 / X / narcotic drug — doctor, patient and Rx number go in the register" : "a Schedule H drug — enter the doctor and patient"}.</p>}
              </div>
              <Field label="Doctor" required={needsRx} htmlFor="rx-doctor" className="sm:col-span-2"><DoctorSelect id="rx-doctor" value={rx.doctor_id} onChange={(v) => setRx((x) => ({ ...x, doctor_id: v }))} /></Field>
              <Field label="Patient name" required={needsRx} htmlFor="rx-patient"><Input id="rx-patient" value={rx.patient_name} onChange={(e) => setRx((x) => ({ ...x, patient_name: e.target.value }))} /></Field>
              <Field label="Rx no." required={needsRxNo} htmlFor="rx-no"><Input id="rx-no" value={rx.rx_number} onChange={(e) => setRx((x) => ({ ...x, rx_number: e.target.value }))} /></Field>
              <Field label="Patient address" htmlFor="rx-addr" className="sm:col-span-2 lg:col-span-4"><Input id="rx-addr" value={rx.patient_address} onChange={(e) => setRx((x) => ({ ...x, patient_address: e.target.value }))} placeholder="For the H1 / X register" /></Field>
            </CardContent>
          </Card>
        )}

        <Card className="overflow-visible">
          <div className="border-b p-3"><ProductSearch ref={search} onPick={addProduct} /></div>
          {lines.length === 0 ? (
            <EmptyState title={tr("Add items to start")} description="Scan a barcode or type a product name, SKU or HSN, then press Enter. F2 jumps back here." />
          ) : (
            <div className="md:overflow-x-auto">
              <table className="w-full text-sm max-md:block">
                <thead className="bg-muted/40 text-xs text-muted-foreground uppercase max-md:hidden">
                  <tr>
                    <th className="w-8 px-3 py-2 text-left">#</th><th className="px-3 py-2 text-left">{tr("Item")}</th>
                    <th className="w-36 px-2 py-2 text-center">{tr("Qty")}</th><th className="w-32 px-2 py-2 text-right">{tr("Rate")}</th>
                    <th className="w-20 px-2 py-2 text-right">{tr("Disc %")}</th><th className="hidden w-16 px-2 py-2 text-right md:table-cell">GST</th>
                    <th className="w-32 px-3 py-2 text-right">{tr("Amount")}</th><th className="w-10" />
                  </tr>
                </thead>
                <tbody className="divide-y max-md:block">
                  {lines.map((l, i) => {
                    const t = lineTotals.get(i);
                    const low = l.stock !== undefined && l.stock !== null && side === "sales" && Number(l.stock) < Number(l.quantity || 0);
                    return (
                      <tr key={l.key} className="align-middle max-md:grid max-md:grid-cols-3 max-md:gap-x-2 max-md:gap-y-2 max-md:px-3 max-md:py-3">
                        <td className="px-3 py-2 text-muted-foreground max-md:hidden">{i + 1}</td>
                        <td className="px-3 py-2 max-md:col-span-3 max-md:p-0">
                          {l.product_id ? <p className="flex flex-wrap items-center gap-1.5 font-medium">{l.name} {pharmacy && <ScheduleBadge schedule={l.schedule} />}
                            {side === "sales" && (t?.custom_fields as Record<string, unknown> | undefined)?.near_expiry === true && (
                              <Badge variant="warning" className="h-5 gap-1 px-1.5 text-[10px]"><ClockAlertIcon className="size-3" /> Near expiry −{Number(t?.discount_percent)}%</Badge>)}</p> : (
                            <Input value={l.name} onChange={(e) => update(l.key, { name: e.target.value })} placeholder="Description" className="h-8" />
                          )}
                          <p className="text-xs text-muted-foreground">{[l.sku, l.rack && `Rack ${l.rack}`, l.hsn && `HSN ${l.hsn}`].filter(Boolean).join(" · ")}
                            {low && <span className="ml-1 text-destructive">· only {qty(l.stock)} in stock</span>}
                            {pharmacy && side === "sales" && l.product_id && l.composition && (
                              <button type="button" onClick={() => setSubsFor(l)} className="ml-1.5 inline-flex items-center gap-0.5 text-primary hover:underline">
                                <ArrowLeftRightIcon className="size-3" /> Substitutes</button>)}</p>
                          {l.batched && l.product_id && (side === "purchase" ? (
                            <div className="mt-1.5 flex flex-wrap gap-1.5">
                              <Input value={l.batch_no ?? ""} onChange={(e) => update(l.key, { batch_no: e.target.value })} placeholder="Batch no." aria-label="Batch number"
                                className={cn("h-7 w-28 text-xs", !l.batch_no && docType === "purchase_invoice" && "border-warning")} />
                              <Input type="date" value={l.expiry_date ?? ""} onChange={(e) => update(l.key, { expiry_date: e.target.value })} aria-label="Expiry date" title="Expiry date" className="h-7 w-36 text-xs" />
                              <Input value={l.mrp ?? ""} inputMode="decimal" onChange={(e) => update(l.key, { mrp: e.target.value.replace(/[^\d.]/g, "") })} placeholder="MRP" aria-label="MRP of this batch" className="num h-7 w-20 text-xs" />
                            </div>
                          ) : <BatchSelect productId={l.product_id} value={l.batch_no ?? ""} onChange={(b) => update(l.key, { batch_no: b || undefined })} />)}
                        </td>
                        <td className="px-2 py-2 max-md:p-0">
                          <span className="text-[10px] text-muted-foreground uppercase md:hidden">{tr("Qty")}</span>
                          <div className="flex items-center justify-center gap-1 max-md:justify-start">
                            <Button type="button" variant="ghost" size="icon-sm" className="size-7" onClick={() => update(l.key, { quantity: String(Math.max(1, Number(l.quantity || 0) - 1)) })} aria-label="Decrease"><MinusIcon /></Button>
                            <Input value={l.quantity} inputMode="decimal" onChange={(e) => update(l.key, { quantity: e.target.value.replace(/[^\d.]/g, "") })} className="num h-8 w-14 px-1 text-center" aria-label="Quantity" />
                            <Button type="button" variant="ghost" size="icon-sm" className="size-7" onClick={() => update(l.key, { quantity: String(Number(l.quantity || 0) + 1) })} aria-label="Increase"><PlusIcon /></Button>
                          </div>
                          {l.packSize && l.packSize > 1 && (
                            <div className="mt-1 flex items-center justify-center gap-1.5 text-[11px] text-muted-foreground max-md:justify-start">
                              <span className="whitespace-nowrap">{packs(Number(l.quantity || 0), l.packSize, l.packLabel) || "—"}</span>
                              <button type="button" className="rounded border px-1 whitespace-nowrap text-primary hover:bg-accent" onClick={() => update(l.key, { quantity: String(Number(l.quantity || 0) + l.packSize!) })}>+1 {l.packLabel || "pack"}</button>
                            </div>
                          )}
                        </td>
                        <td className="px-2 py-2 max-md:p-0"><span className="text-[10px] text-muted-foreground uppercase md:hidden">{tr("Rate")}</span><Input value={l.priceTyped || !t ? l.unit_price : String(Number(t.unit_price))} inputMode="decimal" onChange={(e) => update(l.key, { unit_price: e.target.value.replace(/[^\d.]/g, ""), priceTyped: true })} className="num h-8 text-right" aria-label="Rate" />
                          {typeof t?.custom_fields === "object" && (t.custom_fields as Record<string, string>)?.scheme && <p className="mt-0.5 text-right text-[11px] text-primary">{(t.custom_fields as Record<string, string>).scheme}</p>}</td>
                        <td className="px-2 py-2 max-md:p-0"><span className="text-[10px] text-muted-foreground uppercase md:hidden">{tr("Disc %")}</span><Input value={l.discount_percent} inputMode="decimal" placeholder="0" onChange={(e) => update(l.key, { discount_percent: e.target.value.replace(/[^\d.]/g, "") })} className="num h-8 text-right" aria-label="Discount percent" /></td>
                        <td className="num hidden px-2 py-2 text-right text-muted-foreground md:table-cell">{l.gst_rate ? `${Number(l.gst_rate)}%` : "—"}</td>
                        <td className="num px-3 py-2 text-right font-medium max-md:col-span-2 max-md:self-center max-md:p-0 max-md:text-left max-md:text-base">{t ? money(t.line_total) : preview.isFetching ? <Skeleton className="ml-auto h-4 w-16 max-md:ml-0" /> : "—"}</td>
                        <td className="px-1 max-md:justify-self-end max-md:p-0"><Button type="button" variant="ghost" size="icon-sm" onClick={() => remove(l.key)} aria-label="Remove line"><Trash2Icon className="text-muted-foreground" /></Button></td>
                      </tr>
                    );
                  })}
                  {freeLines.map((f, i) => (
                    <tr key={`free-${i}`} className="bg-primary-soft/40 max-md:flex max-md:flex-wrap max-md:items-center max-md:gap-2 max-md:px-3 max-md:py-2">
                      <td className="px-3 py-2 text-primary"><GiftIcon className="size-4" /></td>
                      <td className="px-3 py-2 font-medium text-primary" colSpan={2}>{f.description}</td>
                      <td className="num px-2 py-2 text-center" colSpan={2}>{qty(f.quantity)} free</td>
                      <td className="hidden md:table-cell" /><td className="num px-3 py-2 text-right text-muted-foreground">₹0.00</td><td />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="flex items-center justify-between border-t px-3 py-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setLines((ls) => [...ls, { key: uid(), product_id: null, name: "", quantity: "1", unit_price: "", discount_percent: "", gst_rate: "18" }])}>
              <PlusIcon /> {tr("Add service / custom line")}
            </Button>
            <span className="text-xs text-muted-foreground">{lines.length} item{lines.length === 1 ? "" : "s"} · {qty(lines.reduce((s, l) => s + Number(l.quantity || 0), 0))} units</span>
          </div>
        </Card>
        {missingBatch && <p className="text-sm text-amber-700 dark:text-warning">Enter the batch number for each batch-tracked item (printed on the pack).</p>}
        <Card><CardContent><Field label={tr("Notes (printed)")}><Textarea value={notes} onChange={(e) => setNotes(e.target.value)} className="min-h-14" /></Field></CardContent></Card>
      </div>

      <div className="xl:sticky xl:top-20 xl:self-start">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>{tr("Summary")}</CardTitle>
            {preview.isFetching ? <Loader2Icon className="size-4 animate-spin text-muted-foreground" /> : totals && <Badge variant="blue">{totals.is_interstate ? "Inter-state · IGST" : "Intra-state · CGST+SGST"}</Badge>}
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {offline ? <p className="rounded-md bg-warning/15 px-2 py-1.5 text-xs text-amber-800 dark:text-warning">Offline: the total below is approximate. Save & finalize keeps the bill on this device and sends it when you are back online.</p>
              : preview.error && <p className="rounded-md bg-destructive/5 px-2 py-1.5 text-xs text-destructive">{(preview.error as ApiError).message}</p>}
            <Row label={tr("Subtotal")} value={totals?.subtotal} />
            {totals && Number(totals.discount_total) > 0 && <Row label={tr("Discount")} value={`-${totals.discount_total}`} />}
            <Row label={tr("Taxable value")} value={totals?.taxable_total} />
            {tax.map(([k, v]) => <Row key={k} label={k!} value={v} />)}
            {totals && Number(totals.cess_total) > 0 && <Row label="Cess" value={totals.cess_total} />}
            {totals && Number(totals.round_off) !== 0 && <Row label={tr("Round off")} value={totals.round_off} />}
            <div className="flex items-baseline justify-between border-t pt-3">
              <span className="font-medium">{tr("Total")}</span>
              <span className="num text-3xl font-semibold tracking-tight">{money(grand)}</span>
            </div>
          </CardContent>
          {(docType === "sales_invoice" || docType === "purchase_invoice") && (
            <CardContent className="space-y-3 border-t">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium">{tr(side === "sales" ? "Payment received" : "Payment made")}</p>
                {payments.length > 0 && <Button variant="link" size="sm" className="h-auto p-0" onClick={() => setPayments([])}>{tr("Clear")}</Button>}
              </div>
              <div className="grid grid-cols-3 gap-2">
                {MODES.slice(0, 3).map((m) => (
                  <Button key={m.v} type="button" variant="outline" size="sm" disabled={dec(grand) <= 0n}
                    onClick={() => setPayments([{ key: uid(), mode: m.v, amount: decStr(dec(grand)), reference: "" }])}>
                    <m.icon /> {tr(m.label)}
                  </Button>
                ))}
              </div>
              {pointsValue > 0n && dec(grand) > 0n && !payments.some((p) => p.mode === "loyalty") && (
                <Button type="button" variant="outline" size="sm" className="w-full border-dashed" onClick={() => {
                  const use = pointsValue < dec(grand) ? pointsValue : dec(grand);
                  const rest = dec(grand) - use;
                  setPayments([{ key: uid(), mode: "loyalty", amount: decStr(use), reference: "" }, ...(rest > 0n ? [{ key: uid(), mode: "cash", amount: decStr(rest), reference: "" }] : [])]);
                }}><StarIcon /> Use {Number(points.data!.points).toLocaleString("en-IN")} points ({money(decStr(pointsValue))})</Button>
              )}
              {payments.map((p) => (
                <div key={p.key} className="flex gap-2">
                  <NativeSelect value={p.mode} className="w-28" onChange={(e) => setPayments((ps) => ps.map((x) => (x.key === p.key ? { ...x, mode: e.target.value } : x)))}>
                    {MODES.map((m) => <option key={m.v} value={m.v}>{m.label}</option>)}
                    {(p.mode === "loyalty" || pointsValue > 0n) && <option value="loyalty">Points</option>}
                  </NativeSelect>
                  <Input value={p.amount} inputMode="decimal" className="num text-right" aria-label="Amount"
                    onChange={(e) => setPayments((ps) => ps.map((x) => (x.key === p.key ? { ...x, amount: e.target.value.replace(/[^\d.]/g, "") } : x)))} />
                  <Button variant="ghost" size="icon" onClick={() => setPayments((ps) => ps.filter((x) => x.key !== p.key))} aria-label="Remove payment"><Trash2Icon /></Button>
                </div>
              ))}
              {payments.length > 0 && payments.length < 5 && (
                <Button variant="ghost" size="sm" onClick={() => setPayments((ps) => [...ps, { key: uid(), mode: "cash", amount: due > 0n ? decStr(due) : "", reference: "" }])}>
                  <PlusIcon /> {tr("Split payment")}
                </Button>
              )}
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">{tr("Balance due")}</span>
                <span className={cn("num font-medium", due > 0n ? "text-amber-600 dark:text-warning" : "text-primary")}>{money(decStr(due > 0n ? due : 0n))}</span>
              </div>
              {side === "sales" && !party && due > 0n && lines.length > 0 && (
                <p className="text-xs text-muted-foreground">Walk-in sales must be paid in full, or choose a customer.</p>
              )}
            </CardContent>
          )}
          <CardContent className="grid gap-2 border-t">
            {rxMissing && <p className="text-xs text-amber-700 dark:text-warning">Enter the prescription details above to finalize.</p>}
            {pharmacy && side === "sales" && !needsRx && !showRx && <Button variant="link" size="sm" className="h-auto justify-start p-0" onClick={() => setShowRx(true)}><StethoscopeIcon /> Add doctor / prescription</Button>}
            <Button size="lg" disabled={!canFinalize} onClick={() => save.mutate(true)}>
              {save.isPending ? <Loader2Icon className="animate-spin" /> : <CheckCircle2Icon />} {finalizeLabel} <kbd className="ml-auto rounded bg-white/15 px-1.5 text-[11px]">F9</kbd>
            </Button>
            <Button variant="outline" disabled={!canSave} onClick={() => save.mutate(false)}>
              <SaveIcon /> {tr("Save draft")} <kbd className="ml-auto rounded border px-1.5 text-[11px] text-muted-foreground">Ctrl S</kbd>
            </Button>
          </CardContent>
        </Card>
      </div>
      {lines.length > 0 && (
        <div className="no-print fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-30 flex items-center gap-3 border-t bg-background/95 px-3 py-2 backdrop-blur xl:hidden">
          <div className="min-w-0 flex-1"><p className="text-[11px] text-muted-foreground">{lines.length} item{lines.length === 1 ? "" : "s"} · total</p>
            <p className="num truncate text-lg font-semibold">{money(grand)}</p></div>
          <Button variant="outline" size="sm" disabled={!canSave} onClick={() => save.mutate(false)} aria-label="Save draft"><SaveIcon /></Button>
          <Button disabled={!canFinalize} onClick={() => save.mutate(true)}>
            {save.isPending ? <Loader2Icon className="animate-spin" /> : <CheckCircle2Icon />} {finalizeLabel}</Button>
        </div>
      )}
      {subsFor?.product_id && (
        <SubstitutesDialog open product={{ id: subsFor.product_id, name: subsFor.name, composition: subsFor.composition }}
          onOpenChange={(v) => !v && setSubsFor(null)} onPick={(p) => swap(subsFor.key, p)} />
      )}
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

/** Sales: which batch to sell. "Auto" lets the server pick the earliest expiry (and split lines). */
function BatchSelect({ productId, value, onChange }: { productId: string; value: string; onChange: (batch: string) => void }) {
  const { base, id: bizId } = useBusiness();
  const q = useQuery({
    queryKey: ["biz", bizId, "batches-available", productId],
    queryFn: () => api<AvailableBatch[]>(`${base}/products/${productId}/batches/available`), staleTime: 30_000,
  });
  const soon = (b: AvailableBatch) => b.days_left !== null && b.days_left <= 30;
  return (
    <NativeSelect value={value} onChange={(e) => onChange(e.target.value)} aria-label="Batch" className="mt-1.5 h-7 max-w-80 text-xs">
      <option value="">Auto (first expiry){q.data?.[0] ? ` · ${q.data[0].batch_no}` : ""}</option>
      {q.data?.map((b) => (
        <option key={b.batch_id} value={b.batch_no}>
          {b.batch_no} · {b.expiry_date ? `exp ${b.expiry_date}` : "no expiry"} · {qty(b.quantity)} left{b.mrp ? ` · MRP ${money(b.mrp)}` : ""}{soon(b) ? " ⚠" : ""}
        </option>
      ))}
    </NativeSelect>
  );
}

interface Credit { credit_limit: string | null; outstanding: string; available: string | null; overdue: string; oldest_overdue_days: number | null;
  credit_hold: boolean; credit_days: number | null; price_list: { id: string; name: string } | null }

/** Sales: the customer's dues, limit, hold and price list, shown as soon as they are picked. */
function CreditPanel({ partyId }: { partyId: string }) {
  const { base, id: bizId } = useBusiness();
  const q = useQuery({ queryKey: ["biz", bizId, "credit", partyId], queryFn: () => api<Credit>(`${base}/parties/${partyId}/credit`), staleTime: 15_000 });
  const c = q.data;
  if (!c) return null;
  const over = c.available !== null && Number(c.available) <= 0;
  const warn = c.credit_hold || over || Number(c.overdue) > 0;
  return (
    <div className={cn("flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border px-3 py-2 text-sm sm:col-span-2 lg:col-span-4",
      warn ? "border-warning/60 bg-warning/10" : "bg-muted/40")}>
      {warn && <AlertTriangleIcon className="size-4 text-amber-600" />}
      {c.credit_hold && <Badge variant="destructive">Credit hold — full payment only</Badge>}
      <span>Dues <b className="num">{money(c.outstanding)}</b></span>
      {Number(c.overdue) > 0 && <span className="text-destructive">Overdue <b className="num">{money(c.overdue)}</b>{c.oldest_overdue_days ? ` (oldest ${c.oldest_overdue_days} days)` : ""}</span>}
      {c.credit_limit !== null && <span>Limit {money(c.credit_limit)} · <span className={over ? "font-medium text-destructive" : ""}>available {money(c.available)}</span></span>}
      {c.price_list && <span className="flex items-center gap-1 text-primary"><TagIcon className="size-3.5" /> {c.price_list.name} prices</span>}
    </div>
  );
}
