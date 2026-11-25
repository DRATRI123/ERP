"use client";

import { PrinterIcon, TagIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/data/page-header";
import { EmptyState } from "@/components/data/states";
import { ProductSearch } from "@/components/pickers/product-search";
import { Barcode } from "@/features/inventory/barcode";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useBusiness } from "@/hooks/use-business";
import { code128Bars } from "@/lib/code128";
import type { Product } from "@/lib/types";

/** Sheet sizes in mm. A4 sheets match the common sticker sheets sold in India; rolls are for thermal label printers. */
const LAYOUTS = {
  a4_65: { label: "A4 sheet · 65 labels (38 × 21 mm)", page: [210, 297], cols: 5, w: 38.1, h: 21.2, mx: 4.7, my: 10.7, gx: 2.5, gy: 0 },
  a4_40: { label: "A4 sheet · 40 labels (48 × 25 mm)", page: [210, 297], cols: 4, w: 48.5, h: 25.4, mx: 8, my: 21.5, gx: 0, gy: 0 },
  a4_24: { label: "A4 sheet · 24 labels (64 × 34 mm)", page: [210, 297], cols: 3, w: 63.5, h: 33.9, mx: 7.2, my: 12.9, gx: 2.5, gy: 0 },
  roll_50: { label: "Thermal roll · 50 × 25 mm", page: [50, 25], cols: 1, w: 50, h: 25, mx: 0, my: 0, gx: 0, gy: 0 },
  roll_38: { label: "Thermal roll · 38 × 25 mm", page: [38, 25], cols: 1, w: 38, h: 25, mx: 0, my: 0, gx: 0, gy: 0 },
} as const;
type LayoutKey = keyof typeof LAYOUTS;

interface Pick { product: Product; copies: string }
const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

function barcodeSvg(value: string): string {
  const bars = code128Bars(value);
  const quiet = 10;
  const total = bars.reduce((a, b) => a + b, 0) + quiet * 2;
  let x = quiet;
  const rects: string[] = [];
  bars.forEach((w, i) => { if (i % 2 === 0) rects.push(`<rect x="${x}" y="0" width="${w}" height="40"/>`); x += w; });
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${total} 40" preserveAspectRatio="none"><g fill="#000">${rects.join("")}</g></svg>`;
}

export default function LabelsPage() {
  const { business: current } = useBusiness();
  const [picks, setPicks] = useState<Pick[]>([]);
  const [layout, setLayout] = useState<LayoutKey>("a4_40");
  const [show, setShow] = useState({ shop: true, name: true, price: true, mrp: true, code: true });
  const [skip, setSkip] = useState("0");
  const add = (p: Product) => setPicks((x) => (x.some((y) => y.product.id === p.id) ? x : [...x, { product: p, copies: p.on_hand && Number(p.on_hand) > 0 ? String(Math.min(Math.ceil(Number(p.on_hand)), 200)) : "1" }]));
  const code = (p: Product) => p.barcode || p.sku;
  const total = picks.reduce((a, p) => a + Math.max(0, Math.floor(Number(p.copies) || 0)), 0);

  const print = () => {
    const L = LAYOUTS[layout];
    const one = (p: Product) => {
      let svg = "";
      try { svg = barcodeSvg(code(p)); } catch { svg = ""; }
      return `<div class="l">${show.shop && current?.business_name ? `<div class="s">${esc(current.business_name)}</div>` : ""}${show.name ? `<div class="n">${esc(p.name)}</div>` : ""}
        <div class="b">${svg}</div>${show.code ? `<div class="c">${esc(code(p))}</div>` : ""}
        <div class="p">${show.price && p.selling_price ? `<b>₹${Number(p.selling_price).toFixed(2)}</b>` : ""}${show.mrp && p.mrp ? ` <span>MRP ₹${Number(p.mrp).toFixed(2)}</span>` : ""}</div></div>`;
    };
    const cells = [...Array(Math.max(0, Math.floor(Number(skip) || 0))).fill('<div class="l"></div>'),
      ...picks.flatMap((p) => Array(Math.max(0, Math.floor(Number(p.copies) || 0))).fill(one(p.product)))];
    const roll = L.cols === 1 && L.page[0] === L.w;
    const css = `@page{size:${L.page[0]}mm ${L.page[1]}mm;margin:0}*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;color:#000}
      .sheet{display:grid;grid-template-columns:repeat(${L.cols},${L.w}mm);column-gap:${L.gx}mm;row-gap:${L.gy}mm;padding:${L.my}mm ${L.mx}mm;grid-auto-rows:${L.h}mm}
      .l{width:${L.w}mm;height:${L.h}mm;padding:1mm 1.5mm;overflow:hidden;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;${roll ? "page-break-after:always;" : ""}}
      .s{font-size:6pt;font-weight:bold;white-space:nowrap;overflow:hidden;max-width:100%}.n{font-size:7pt;line-height:1.1;max-height:2.2em;overflow:hidden}
      .b{width:100%;flex:1;min-height:0;max-height:${Math.round(L.h * 0.45)}mm}.b svg{width:100%;height:100%}.c{font-size:6pt;font-family:monospace}.p{font-size:7.5pt}.p span{font-size:6pt}`;
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>Labels</title><style>${css}</style></head><body><div class="${roll ? "" : "sheet"}">${cells.join("")}</div>
      <script>window.onload=function(){setTimeout(function(){window.print()},200)}</script></body></html>`;
    const w = window.open("", "_blank");
    if (!w) { toast.error("Allow pop-ups to print labels"); return; }
    w.document.open(); w.document.write(html); w.document.close();
  };

  return (
    <>
      <PageHeader title="Barcode labels" description="Print price stickers with barcodes on A4 sticker sheets or a thermal label printer. Items without a barcode use their SKU."
        actions={<Button disabled={!total} onClick={print}><PrinterIcon /> Print {total || ""} labels</Button>} />
      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <Card className="space-y-3 p-3 sm:p-4">
          <ProductSearch onPick={add} placeholder="Search or scan products to label…" />
          {!picks.length ? <EmptyState icon={TagIcon} title="No products yet" description="Add products above. The number of labels starts at the stock in hand." /> : (
            <ul className="divide-y rounded-md border">
              {picks.map((p) => (
                <li key={p.product.id} className="grid grid-cols-[1fr_5.5rem_auto] items-center gap-2 p-2">
                  <div className="min-w-0"><p className="truncate text-sm font-medium">{p.product.name}</p>
                    <p className="truncate text-xs text-muted-foreground">{code(p.product)}{p.product.selling_price ? ` · ₹${Number(p.product.selling_price)}` : ""}</p></div>
                  <Input aria-label={`Labels for ${p.product.name}`} inputMode="numeric" className="num text-right" value={p.copies}
                    onChange={(e) => setPicks((x) => x.map((y) => (y.product.id === p.product.id ? { ...y, copies: e.target.value.replace(/\D/g, "").slice(0, 4) } : y)))} />
                  <Button size="icon" variant="ghost" aria-label="Remove" onClick={() => setPicks((x) => x.filter((y) => y.product.id !== p.product.id))}><Trash2Icon /></Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card className="space-y-4 p-3 sm:p-4">
          <Field label="Label size" htmlFor="lb-layout"><NativeSelect id="lb-layout" value={layout} onChange={(e) => setLayout(e.target.value as LayoutKey)}>
            {Object.entries(LAYOUTS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</NativeSelect></Field>
          {LAYOUTS[layout].cols > 1 && <Field label="Skip used labels" htmlFor="lb-skip" hint="Start lower on a half-used sheet"><Input id="lb-skip" inputMode="numeric" className="num" value={skip} onChange={(e) => setSkip(e.target.value.replace(/\D/g, "").slice(0, 2))} /></Field>}
          <div className="space-y-2 text-sm">
            {([["shop", "Shop name"], ["name", "Product name"], ["price", "Selling price"], ["mrp", "MRP"], ["code", "Code under the bars"]] as const).map(([k, l]) => (
              <label key={k} className="flex items-center justify-between gap-2">{l}<Switch checked={show[k]} onCheckedChange={(v) => setShow((s) => ({ ...s, [k]: v }))} /></label>
            ))}
          </div>
          {picks[0] && <div className="rounded-md border bg-white p-2 text-center text-black">
            <p className="text-[11px]">{show.name && picks[0].product.name}</p>
            <div className="flex justify-center overflow-hidden"><Barcode value={code(picks[0].product)} height={36} module={1.2} /></div>
            <p className="text-xs font-semibold">{show.price && picks[0].product.selling_price ? `₹${Number(picks[0].product.selling_price).toFixed(2)}` : ""} {show.mrp && picks[0].product.mrp ? <span className="font-normal">MRP ₹{Number(picks[0].product.mrp).toFixed(2)}</span> : null}</p>
          </div>}
        </Card>
      </div>
    </>
  );
}
