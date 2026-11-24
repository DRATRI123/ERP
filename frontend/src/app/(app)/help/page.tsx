"use client";

import { BookOpenIcon, ChevronRightIcon, SearchIcon } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { PageHeader } from "@/components/data/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { HELP, HELP_BY_ID } from "@/features/help/content";
import { HelpView } from "@/features/help/help-view";
import { cn } from "@/lib/utils";

/** "How do I…" shortcuts straight to the right guide. */
const TASKS: [string, string][] = [
  ["Make a sales invoice", "sales"], ["Make a sales order", "sales"], ["Turn a quotation or order into an invoice", "sales"],
  ["Customer returned goods (credit note)", "sales"], ["Make a purchase order", "purchases"], ["Record a purchase bill", "purchases"],
  ["Return goods to a supplier", "purchases"], ["Record money received / paid", "payments"], ["Add a product with batches & expiry", "inventory"],
  ["Opening stock, adjustments, transfers", "inventory"], ["See what to reorder", "reorder"], ["Return expired stock", "expiry"],
  ["Hospital / doctor prices, 10 + 1 schemes", "pricing"], ["Record production", "manufacturing"], ["File GST returns", "gst"],
  ["Make an e-way bill", "gst"], ["Pay salaries", "staff"], ["Approve expense claims", "claims"], ["Send data to Tally", "accounting"],
  ["Take orders on WhatsApp", "catalogue"], ["Get daily report e-mails", "reports"], ["Add a staff login", "users"],
];

function HelpCentre() {
  const params = useSearchParams();
  const router = useRouter();
  const [q, setQ] = useState("");
  const selected = HELP_BY_ID.get(params.get("topic") ?? "") ?? HELP_BY_ID.get("start")!;
  const open = (id: string) => { router.replace(`/help?topic=${id}`, { scroll: false }); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const matches = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (!term) return null;
    return HELP.filter((t) => [t.title, t.summary, ...t.sections.flatMap((s) => [s.heading, ...s.steps, s.note ?? ""]), ...(t.tips ?? [])]
      .join(" ").toLowerCase().includes(term));
  }, [q]);
  const groups = [...new Set(HELP.map((t) => t.group))];

  return (
    <>
      <PageHeader title="Help & guide" description="Step-by-step guides for every part of the ERP. Every page also has an ⓘ button next to its title." help={false} />
      <div className="grid gap-5 lg:grid-cols-[280px_1fr]">
        <aside className="space-y-4">
          <div className="relative"><SearchIcon className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search: purchase bill, credit note…" className="pl-8" aria-label="Search guides" /></div>
          {matches ? (
            <Card><CardContent className="space-y-1 p-2">
              {matches.length ? matches.map((t) => <TopicLink key={t.id} title={t.title} active={t.id === selected.id} onClick={() => open(t.id)} />)
                : <p className="p-2 text-sm text-muted-foreground">No guide mentions “{q}”.</p>}
            </CardContent></Card>
          ) : groups.map((g) => (
            <div key={g}><p className="px-2 pb-1 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">{g}</p>
              {HELP.filter((t) => t.group === g).map((t) => <TopicLink key={t.id} title={t.title} active={t.id === selected.id} onClick={() => open(t.id)} />)}
            </div>
          ))}
        </aside>
        <div className="min-w-0 space-y-5">
          {selected.id === "start" && !q && (
            <Card><CardContent>
              <h2 className="mb-3 flex items-center gap-2 font-semibold"><BookOpenIcon className="size-4 text-primary" /> How do I…</h2>
              <div className="grid gap-1 sm:grid-cols-2">
                {TASKS.map(([label, id]) => (
                  <button key={label} type="button" onClick={() => open(id)} className="group flex items-center justify-between rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent">
                    {label}<ChevronRightIcon className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" /></button>
                ))}
              </div>
            </CardContent></Card>
          )}
          <Card><CardContent>
            <p className="text-xs font-medium text-muted-foreground uppercase">{selected.group}</p>
            <h2 className="mb-4 text-xl font-semibold">{selected.title}</h2>
            <HelpView topic={selected} onOpen={open} />
          </CardContent></Card>
        </div>
      </div>
    </>
  );
}

function TopicLink({ title, active, onClick }: { title: string; active: boolean; onClick: () => void }) {
  return <button type="button" onClick={onClick} className={cn("block w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent", active && "bg-sidebar-active font-medium text-primary")}>{title}</button>;
}

export default function HelpPage() {
  return <Suspense><HelpCentre /></Suspense>;
}
