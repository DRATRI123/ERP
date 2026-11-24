"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ProductSearch } from "@/components/pickers/product-search";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";
import { qty, todayISO } from "@/lib/format";
import type { Product } from "@/lib/types";

export type StockMode = "adjustment" | "transfer" | "opening";
interface Row { product: Product; quantity: string; unit_cost: string; batch_no: string; expiry_date: string }
const REASONS = ["damage", "expiry", "theft", "sample", "internal_use", "correction", "found", "other"];
const TITLE: Record<StockMode, string> = { adjustment: "Stock adjustment", transfer: "Transfer between warehouses", opening: "Opening stock" };

export function StockDialog({ mode, open, onOpenChange }: { mode: StockMode; open: boolean; onOpenChange: (v: boolean) => void }) {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const whs = useBizQuery<{ id: string; code: string; name: string }[]>(["warehouses"], "/warehouses", undefined, { enabled: open });
  const [wh, setWh] = useState("");
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("correction");
  const [docDate, setDocDate] = useState(todayISO());
  const [reference, setReference] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const from = wh || whs.data?.[0]?.id || "";

  const save = useMutation({
    mutationFn: () => {
      const lines = rows.map((r) => ({
        product_id: r.product.id, quantity: r.quantity, batch_no: r.batch_no || undefined,
        ...(mode !== "transfer" && r.unit_cost ? { unit_cost: r.unit_cost } : {}),
        ...(mode !== "transfer" && r.expiry_date ? { expiry_date: r.expiry_date } : {}),
      }));
      const body = mode === "transfer"
        ? { doc_date: docDate, reference: reference || undefined, from_warehouse_id: from, to_warehouse_id: to, lines }
        : { doc_date: docDate, reference: reference || undefined, warehouse_id: from, lines, ...(mode === "adjustment" ? { reason } : {}) };
      const path = mode === "transfer" ? "transfers" : mode === "opening" ? "opening-stock" : "adjustments";
      return api<{ number: string }>(`${base}/inventory/${path}`, { body });
    },
    onSuccess: (d) => { toast.success(`${TITLE[mode]} ${d.number} posted`); qc.invalidateQueries({ queryKey: ["biz", id] }); setRows([]); onOpenChange(false); },
    onError: (e: ApiError) => toast.error(e.message),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent wide>
        <DialogHeader>
          <DialogTitle>{TITLE[mode]}</DialogTitle>
          <DialogDescription>
            {mode === "adjustment" ? "Use a negative quantity to remove stock (damage, expiry…), positive to add (found)." :
              mode === "transfer" ? "Moves stock at its current cost; nothing is sold." : "Stock you already had when you started using the ERP, at its cost."}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label={mode === "transfer" ? "From" : "Warehouse"}>
            <NativeSelect value={from} onChange={(e) => setWh(e.target.value)}>{whs.data?.map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}</NativeSelect>
          </Field>
          {mode === "transfer" && (
            <Field label="To"><NativeSelect value={to} onChange={(e) => setTo(e.target.value)}><option value="">Choose…</option>
              {whs.data?.filter((w) => w.id !== from).map((w) => <option key={w.id} value={w.id}>{w.code} — {w.name}</option>)}</NativeSelect></Field>
          )}
          {mode === "adjustment" && (
            <Field label="Reason"><NativeSelect value={reason} onChange={(e) => setReason(e.target.value)}>{REASONS.map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}</NativeSelect></Field>
          )}
          <Field label="Date"><Input type="date" value={docDate} onChange={(e) => setDocDate(e.target.value)} /></Field>
          <Field label="Reference"><Input value={reference} onChange={(e) => setReference(e.target.value)} /></Field>
        </div>
        <ProductSearch placeholder="Add a product (scan or search)" onPick={(p) => setRows((rs) => rs.some((r) => r.product.id === p.id) ? rs :
          [...rs, { product: p, quantity: mode === "adjustment" ? "-1" : "1", unit_cost: p.purchase_price ? String(Number(p.purchase_price)) : "", batch_no: "", expiry_date: "" }])} />
        {rows.length > 0 && (
          <div className="divide-y rounded-lg border">
            {rows.map((r, i) => (
              <div key={r.product.id} className="grid items-end gap-2 p-3 sm:grid-cols-[1fr_100px_110px_110px_130px_auto]">
                <div className="min-w-0 self-center"><p className="truncate text-sm font-medium">{r.product.name}</p>
                  <p className="text-xs text-muted-foreground">{r.product.sku} · {qty(r.product.on_hand as string)} on hand</p></div>
                <Field label="Qty"><Input value={r.quantity} className="num" onChange={(e) => setRows((rs) => rs.map((x, j) => j === i ? { ...x, quantity: e.target.value } : x))} /></Field>
                {mode !== "transfer" ? <Field label="Unit cost"><Input value={r.unit_cost} className="num" onChange={(e) => setRows((rs) => rs.map((x, j) => j === i ? { ...x, unit_cost: e.target.value } : x))} /></Field> : <span />}
                {r.product.batch_tracking ? <Field label="Batch"><Input value={r.batch_no} onChange={(e) => setRows((rs) => rs.map((x, j) => j === i ? { ...x, batch_no: e.target.value } : x))} /></Field> : <span />}
                {r.product.batch_tracking && mode !== "transfer" ? <Field label="Expiry"><Input type="date" value={r.expiry_date} onChange={(e) => setRows((rs) => rs.map((x, j) => j === i ? { ...x, expiry_date: e.target.value } : x))} /></Field> : <span />}
                <Button variant="ghost" size="icon" onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))} aria-label="Remove"><Trash2Icon /></Button>
              </div>
            ))}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={!rows.length || save.isPending || (mode === "transfer" && !to)} onClick={() => save.mutate()}>
            {save.isPending && <Loader2Icon className="animate-spin" />} Post {mode === "transfer" ? "transfer" : mode === "opening" ? "opening stock" : "adjustment"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
