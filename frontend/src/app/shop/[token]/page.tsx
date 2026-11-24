"use client";

import { CheckCircle2Icon, Loader2Icon, MessageCircleIcon, MinusIcon, PlusIcon, SearchIcon, ShoppingBagIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { money } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Item { id: string; name: string; sku: string; price: string; mrp: string | null; prices_include_tax: boolean; gst_rate: string; category: string | null; in_stock: boolean | null }
interface Catalogue { business: string; title: string; welcome: string | null; whatsapp: boolean; min_order_value: string; phone: string | null; products: Item[] }
interface Placed { reference: string; grand_total: string; lines: number; whatsapp_url: string | null }

/** The buyer's page: no login, no app chrome — opened from a WhatsApp link on a phone. */
export default function ShopPage() {
  const { token } = useParams<{ token: string }>();
  const catalogue = useQuery<Catalogue, Error>({
    queryKey: ["public-catalogue", token], retry: false, staleTime: 60_000,
    queryFn: async () => {
      const r = await fetch(`/api/v1/public/catalogue/${encodeURIComponent(token)}`);
      if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.error?.message ?? "This catalogue is not available");
      return r.json();
    },
  });
  const data = catalogue.data ?? null;
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [cart, setCart] = useState<Record<string, number>>({});
  const [step, setStep] = useState<"browse" | "details" | "done">("browse");
  const [form, setForm] = useState({ name: "", phone: "", address: "", note: "", website: "" });
  const [sending, setSending] = useState(false);
  const [placed, setPlaced] = useState<Placed | null>(null);

  const byId = useMemo(() => new Map((data?.products ?? []).map((p) => [p.id, p])), [data]);
  const groups = useMemo(() => {
    const list = (data?.products ?? []).filter((p) => !q || `${p.name} ${p.sku} ${p.category ?? ""}`.toLowerCase().includes(q.toLowerCase()));
    const out = new Map<string, Item[]>();
    for (const p of list) out.set(p.category ?? "Products", [...(out.get(p.category ?? "Products") ?? []), p]);
    return [...out.entries()];
  }, [data, q]);
  const lines = Object.entries(cart).filter(([, n]) => n > 0);
  const total = lines.reduce((s, [id, n]) => s + Number(byId.get(id)?.price ?? 0) * n, 0);
  const count = lines.reduce((s, [, n]) => s + n, 0);
  const setQty = (id: string, n: number) => setCart((c) => ({ ...c, [id]: Math.max(0, Math.min(1000, n)) }));

  async function send() {
    setSending(true); setError(null);
    try {
      const r = await fetch(`/api/v1/public/catalogue/${encodeURIComponent(token)}/orders`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, address: form.address || undefined, note: form.note || undefined, website: form.website || undefined,
          items: lines.map(([product_id, quantity]) => ({ product_id, quantity })) }) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body?.error?.message ?? "Could not place the order");
      setPlaced(body); setStep("done"); setCart({});
      if (body.whatsapp_url) window.location.assign(body.whatsapp_url);
    } catch (e) { setError((e as Error).message); } finally { setSending(false); }
  }

  if (catalogue.error) return <Shell><div className="py-24 text-center"><ShoppingBagIcon className="mx-auto mb-3 size-10 text-muted-foreground" /><p className="font-medium">{catalogue.error.message}</p></div></Shell>;
  if (!data) return <Shell><div className="space-y-3 py-6">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-20" />)}</div></Shell>;

  if (step === "done" && placed) return (
    <Shell title={data.business}>
      <div className="py-16 text-center">
        <CheckCircle2Icon className="mx-auto mb-3 size-14 text-primary" />
        <h1 className="text-xl font-semibold">Order {placed.reference} sent</h1>
        <p className="mt-1 text-muted-foreground">{placed.lines} item{placed.lines === 1 ? "" : "s"} · {money(placed.grand_total)}. {data.business} will confirm it with you.</p>
        {placed.whatsapp_url && <a href={placed.whatsapp_url} className="mt-6 inline-flex items-center gap-2 rounded-full bg-[#25D366] px-5 py-3 font-medium text-white"><MessageCircleIcon className="size-5" /> Continue on WhatsApp</a>}
      </div>
    </Shell>
  );

  return (
    <Shell title={data.business}>
      {step === "browse" ? (
        <>
          <div className="py-4"><h1 className="text-xl font-semibold">{data.title}</h1>{data.welcome && <p className="mt-1 text-sm whitespace-pre-line text-muted-foreground">{data.welcome}</p>}</div>
          <div className="sticky top-14 z-10 -mx-4 bg-background/95 px-4 pb-3 backdrop-blur">
            <div className="relative"><SearchIcon className="absolute top-2.5 left-3 size-4 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search products" className="h-10 rounded-full pl-9" aria-label="Search products" /></div>
          </div>
          {groups.map(([cat, items]) => (
            <section key={cat} className="mb-5">
              <h2 className="mb-2 text-xs font-semibold tracking-wider text-muted-foreground uppercase">{cat}</h2>
              <ul className="divide-y rounded-xl border bg-card">{items.map((p) => {
                const n = cart[p.id] ?? 0;
                const out = p.in_stock === false;
                return (
                  <li key={p.id} className={cn("flex items-center gap-3 p-3", out && "opacity-60")}>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium leading-snug">{p.name}</p>
                      <p className="text-sm"><span className="font-semibold">{money(p.price)}</span>
                        {p.mrp && Number(p.mrp) > Number(p.price) && <span className="ml-1.5 text-xs text-muted-foreground line-through">{money(p.mrp)}</span>}
                        <span className="ml-1.5 text-xs text-muted-foreground">{p.prices_include_tax ? "incl. GST" : `+ ${Number(p.gst_rate)}% GST`}</span></p>
                      {out && <p className="text-xs text-destructive">Out of stock</p>}
                    </div>
                    {n === 0 ? <Button size="sm" variant="outline" disabled={out} onClick={() => setQty(p.id, 1)} className="rounded-full">Add</Button> : (
                      <div className="flex items-center gap-1 rounded-full border">
                        <Button size="icon" variant="ghost" className="size-8 rounded-full" onClick={() => setQty(p.id, n - 1)} aria-label={`Fewer ${p.name}`}><MinusIcon /></Button>
                        <span className="num w-6 text-center text-sm font-medium">{n}</span>
                        <Button size="icon" variant="ghost" className="size-8 rounded-full" onClick={() => setQty(p.id, n + 1)} aria-label={`More ${p.name}`}><PlusIcon /></Button>
                      </div>
                    )}
                  </li>
                );
              })}</ul>
            </section>
          ))}
          {!groups.length && <p className="py-10 text-center text-muted-foreground">No products match.</p>}
          {count > 0 && <div className="sticky bottom-0 -mx-4 border-t bg-background/95 p-3 backdrop-blur">
            <Button className="h-12 w-full rounded-full text-base" onClick={() => setStep("details")}>
              <ShoppingBagIcon /> {count} item{count === 1 ? "" : "s"} · {money(total)} — Continue</Button>
          </div>}
        </>
      ) : (
        <div className="space-y-4 py-4">
          <button type="button" className="text-sm text-blue" onClick={() => setStep("browse")}>← Back to products</button>
          <ul className="divide-y rounded-xl border bg-card text-sm">{lines.map(([id, n]) => (
            <li key={id} className="flex justify-between gap-2 p-3"><span>{byId.get(id)?.name} × {n}</span><span className="num">{money(Number(byId.get(id)?.price ?? 0) * n)}</span></li>))}
            <li className="flex justify-between p-3 font-semibold"><span>Approx. total</span><span className="num">{money(total)}</span></li></ul>
          {Number(data.min_order_value) > total && <p className="text-sm text-destructive">Minimum order is {money(data.min_order_value)}.</p>}
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); send(); }}>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Your name" aria-label="Your name" required className="h-11" />
            <Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="Mobile number" aria-label="Mobile number" inputMode="tel" required className="h-11" />
            <Textarea value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} placeholder="Delivery address (optional)" aria-label="Delivery address" rows={2} />
            <Input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="Note (optional)" aria-label="Note" className="h-11" />
            <input type="text" name="website" tabIndex={-1} autoComplete="off" value={form.website} onChange={(e) => setForm({ ...form, website: e.target.value })} className="hidden" aria-hidden="true" />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="h-12 w-full rounded-full text-base" disabled={sending || !lines.length || Number(data.min_order_value) > total}>
              {sending ? <Loader2Icon className="animate-spin" /> : <MessageCircleIcon />} {data.whatsapp ? "Send order on WhatsApp" : "Send order"}</Button>
            <p className="text-center text-xs text-muted-foreground">Prices are confirmed by {data.business} before billing.</p>
          </form>
        </div>
      )}
    </Shell>
  );
}

function Shell({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <div className="min-h-dvh bg-background">
      <header className="sticky top-0 z-20 flex h-14 items-center border-b bg-primary px-4 text-primary-foreground"><ShoppingBagIcon className="mr-2 size-5" /><span className="truncate font-semibold">{title ?? "Catalogue"}</span></header>
      <main className="mx-auto max-w-xl px-4">{children}</main>
    </div>
  );
}
