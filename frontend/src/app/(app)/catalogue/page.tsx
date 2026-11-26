"use client";

import { CopyIcon, ExternalLinkIcon, Loader2Icon, MessageCircleIcon, RefreshCwIcon, ShoppingBagIcon, StoreIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/data/confirm";
import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox, Switch } from "@/components/ui/switch";
import { OnlineNotice } from "@/features/system/online-notice";
import { usePublicAddress } from "@/hooks/use-public-address";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { dateTime, money } from "@/lib/format";

interface Settings {
  configured: boolean; token?: string; title?: string; welcome?: string | null; whatsapp_number?: string | null; category_ids?: string[];
  show_stock?: boolean; min_order_value?: string; is_active?: boolean; orders_24h?: number;
}
interface Order { id: string; document_id: string; number: string | null; status: string; grand_total: string; name: string; phone: string; address: string | null; note: string | null; created_at: string; converted: boolean }

export default function CataloguePage() {
  const { can, isOwner } = useBusiness();
  const manage = isOwner || can("business.settings");
  const s = useBizQuery<Settings>(["catalogue"], "/catalogue", undefined, { enabled: manage });
  const orders = useBizQuery<Order[]>(["catalogue", "orders"], "/catalogue/orders");
  return (
    <>
      <PageHeader title="WhatsApp catalogue" description="Share one link: buyers pick products on their phone, the order lands here as a draft sales order and their WhatsApp opens with the summary." />
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <div className="space-y-4">
          {manage && (s.error ? <Card><ErrorState error={s.error} /></Card> : !s.data ? <Skeleton className="h-64" /> : <SettingsCard key={JSON.stringify(s.data)} s={s.data} />)}
          <Card className="overflow-hidden">
            <CardHeader className="pb-3"><CardTitle>Online orders</CardTitle></CardHeader>
            {orders.isLoading ? <CardContent><Skeleton className="h-24" /></CardContent> : !orders.data?.length ? (
              <EmptyState icon={ShoppingBagIcon} title="No orders yet" description="Orders from the catalogue appear here. Open one to check prices and convert it to an invoice." />
            ) : (
              <ul className="divide-y">{orders.data.map((o) => (
                <li key={o.id}><Link href={`/documents/${o.document_id}`} className="flex items-start gap-3 px-4 py-3 hover:bg-accent/50">
                  <div className="min-w-0 flex-1"><p className="font-medium">{o.name} <span className="font-normal text-muted-foreground">· {o.phone}</span></p>
                    <p className="truncate text-xs text-muted-foreground">{dateTime(o.created_at)}{o.address ? ` · ${o.address}` : ""}{o.note ? ` · “${o.note}”` : ""}</p></div>
                  <div className="text-right"><p className="num font-medium">{money(o.grand_total)}</p>
                    <Badge variant={o.status === "draft" ? "warning" : "default"}>{o.status === "draft" ? "To confirm" : "Confirmed"}</Badge></div>
                </Link></li>))}</ul>
            )}
          </Card>
        </div>
        {manage && s.data?.configured && <LinkCard token={s.data.token!} active={!!s.data.is_active} orders24={s.data.orders_24h ?? 0} />}
      </div>
    </>
  );
}

function SettingsCard({ s }: { s: Settings }) {
  const cats = useBizQuery<{ id: string; name: string }[]>(["categories"], "/categories");
  const [d, setD] = useState({
    title: s.title ?? "", welcome: s.welcome ?? "", whatsapp_number: s.whatsapp_number ? s.whatsapp_number.slice(-10) : "",
    category_ids: s.category_ids ?? [], show_stock: s.show_stock ?? true, min_order_value: s.min_order_value ? String(Number(s.min_order_value)) : "0",
    is_active: s.is_active ?? true,
  });
  const save = useBizMutation<typeof d>("PUT", "/catalogue", { invalidate: [["catalogue"]], success: s.configured ? "Catalogue saved" : "Catalogue created — share the link" });
  return (
    <Card>
      <CardHeader className="pb-3"><CardTitle>{s.configured ? "Settings" : "Set up your catalogue"}</CardTitle></CardHeader>
      <CardContent>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate({ ...d, whatsapp_number: d.whatsapp_number || "" }); }}>
          <Field label="Title" htmlFor="ct-title"><Input id="ct-title" value={d.title} maxLength={120} onChange={(e) => setD({ ...d, title: e.target.value })} placeholder="Order Ayurvedic medicines" /></Field>
          <Field label="Your WhatsApp number" htmlFor="ct-wa" hint="Orders open a chat to this number"><Input id="ct-wa" inputMode="tel" value={d.whatsapp_number} onChange={(e) => setD({ ...d, whatsapp_number: e.target.value })} placeholder="98765 43210" /></Field>
          <Field label="Welcome text" htmlFor="ct-wel" className="sm:col-span-2"><Textarea id="ct-wel" rows={2} value={d.welcome} maxLength={1000} onChange={(e) => setD({ ...d, welcome: e.target.value })} placeholder="Free delivery in Pune above ₹999. Orders before 4 pm ship the same day." /></Field>
          <Field label="Minimum order (₹)" htmlFor="ct-min"><Input id="ct-min" inputMode="decimal" value={d.min_order_value} onChange={(e) => setD({ ...d, min_order_value: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
          <div className="flex flex-col justify-end gap-2 pb-1">
            <label className="flex items-center gap-2 text-sm"><Switch checked={d.show_stock} onCheckedChange={(v) => setD({ ...d, show_stock: v })} /> Show in stock / out of stock</label>
            <label className="flex items-center gap-2 text-sm"><Switch checked={d.is_active} onCheckedChange={(v) => setD({ ...d, is_active: v })} /> Catalogue is open</label>
          </div>
          <fieldset className="sm:col-span-2"><legend className="mb-2 text-sm font-medium">Categories to show <span className="font-normal text-muted-foreground">(none ticked = all products with a selling price)</span></legend>
            <div className="grid gap-2 sm:grid-cols-3">{cats.data?.map((c) => (
              <label key={c.id} className="flex items-center gap-2 text-sm"><Checkbox checked={d.category_ids.includes(c.id)}
                onCheckedChange={(v) => setD({ ...d, category_ids: v ? [...d.category_ids, c.id] : d.category_ids.filter((x) => x !== c.id) })} /> {c.name}</label>))}
              {!cats.data?.length && <p className="text-sm text-muted-foreground">No categories yet — every product with a selling price is shown.</p>}</div>
          </fieldset>
          <div className="sm:col-span-2"><Button type="submit" disabled={save.isPending}>{save.isPending ? <Loader2Icon className="animate-spin" /> : <StoreIcon />} {s.configured ? "Save" : "Create catalogue"}</Button></div>
        </form>
      </CardContent>
    </Card>
  );
}

function LinkCard({ token, active, orders24 }: { token: string; active: boolean; orders24: number }) {
  const { base: origin } = usePublicAddress();
  const url = `${origin}/shop/${token}`;
  const qr = useBizQuery<{ qr: string }>(["catalogue", "qr", token, origin], "/catalogue/qr", { origin }, { enabled: !!origin });
  const [rotating, setRotating] = useState(false);
  const rotate = useBizMutation("POST", "/catalogue/rotate", { invalidate: [["catalogue"]], success: "New link created; the old one no longer works" });
  const share = `https://wa.me/?text=${encodeURIComponent(`Order from us on WhatsApp: ${url}`)}`;
  return (
    <Card className="self-start">
      <CardHeader className="flex-row items-center justify-between pb-3"><CardTitle>Your link</CardTitle>
        <Badge variant={active ? "default" : "secondary"}>{active ? "Open" : "Closed"}</Badge></CardHeader>
      <CardContent className="space-y-4">
        <OnlineNotice what="catalogue" />
        {/* eslint-disable-next-line @next/next/no-img-element -- an inline SVG data URL; nothing to optimise */}
        {qr.data ? <img src={qr.data.qr} alt="QR code of the catalogue link" className="mx-auto size-48 rounded-lg border bg-white p-2" /> : <Skeleton className="mx-auto size-48" />}
        <div className="flex gap-2"><Input readOnly value={url} aria-label="Catalogue link" className="text-xs" onFocus={(e) => e.target.select()} />
          <Button size="icon" variant="outline" aria-label="Copy link" onClick={() => navigator.clipboard.writeText(url).then(() => toast.success("Link copied"))}><CopyIcon /></Button></div>
        <div className="grid grid-cols-2 gap-2">
          <a href={share} target="_blank" rel="noreferrer" className="inline-flex items-center justify-center gap-2 rounded-md bg-[#25D366] px-3 py-2 text-sm font-medium text-white"><MessageCircleIcon className="size-4" /> Share</a>
          <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm"><ExternalLinkIcon className="size-4" /> Open</a>
        </div>
        <p className="text-sm text-muted-foreground">{orders24} order{orders24 === 1 ? "" : "s"} in the last 24 hours. Print the QR for your counter.</p>
        <Button variant="ghost" size="sm" className="text-destructive" onClick={() => setRotating(true)}><RefreshCwIcon /> Replace link</Button>
      </CardContent>
      <ConfirmDialog open={rotating} onOpenChange={setRotating} destructive confirm="Replace link" title="Replace the catalogue link?"
        description="Anyone with the old link (or QR) can no longer order. Share the new one." onConfirm={() => rotate.mutateAsync(undefined)} />
    </Card>
  );
}
