"use client";

import { CalendarClockIcon, Loader2Icon, MessageCircleIcon, PhoneIcon, PlusIcon, TargetIcon, TrophyIcon, UserCheckIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { StaticTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money } from "@/lib/format";

type Stage = "new" | "contacted" | "qualified" | "proposal" | "won" | "lost";
interface Lead {
  id: string; name: string; company: string | null; phone: string | null; email: string | null; city: string | null; source: string | null;
  requirement: string | null; stage: Stage; expected_value: string; assigned_to: string | null; next_follow_up: string | null;
  party_id: string | null; lost_reason: string | null; closed_on: string | null; overdue: boolean;
}
interface Activity { id: string; kind: string; note: string; happened_at: string; by: string | null }
const STAGES: [Stage, string][] = [["new", "New"], ["contacted", "Contacted"], ["qualified", "Interested"], ["proposal", "Quotation sent"], ["won", "Won"], ["lost", "Lost"]];
const STAGE_LABEL = Object.fromEntries(STAGES) as Record<Stage, string>;
const SOURCES = ["Walk-in", "Phone call", "WhatsApp", "Referral", "IndiaMART", "JustDial", "Website", "Exhibition", "Field visit"];
const KINDS = [["call", "Call"], ["visit", "Visit"], ["whatsapp", "WhatsApp"], ["meeting", "Meeting"], ["email", "E-mail"], ["note", "Note"]] as const;
const plusDays = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
const wa = (phone: string) => `https://wa.me/${phone.replace(/\D/g, "").replace(/^(\d{10})$/, "91$1")}`;

export default function LeadsPage() {
  const { can, isOwner } = useBusiness();
  const manage = isOwner || can("customers.manage");
  const [tab, setTab] = useState("today");
  const [q, setQ] = useState("");
  const query = tab === "today" ? { due: "today" } : tab === "open" ? {} : { stage: tab };
  const list = useBizQuery<Lead[]>(["leads", tab, q], "/leads", { ...query, q: q || undefined });
  const pipe = useBizQuery<{ stages: Record<Stage, { count: number; value: string }>; follow_ups_due: number; without_follow_up: number; win_rate: number | null }>(["leads", "pipeline"], "/leads/pipeline");
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const p = pipe.data;
  const openValue = p ? (["new", "contacted", "qualified", "proposal"] as Stage[]).reduce((a, s) => a + Number(p.stages[s].value), 0) : 0;
  const openCount = p ? (["new", "contacted", "qualified", "proposal"] as Stage[]).reduce((a, s) => a + p.stages[s].count, 0) : 0;
  const rows = tab === "open" ? (list.data ?? []).filter((l) => l.stage !== "won" && l.stage !== "lost") : list.data ?? [];
  return (
    <>
      <PageHeader title="Leads & follow-ups" description="Every enquiry until it becomes an order: who to call today, what was said, and what is in the pipeline."
        actions={manage && <Button onClick={() => setAdding(true)}><PlusIcon /> New lead</Button>} />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:mb-6 sm:gap-4 lg:grid-cols-4">
        <StatCard label="Follow up today" icon={CalendarClockIcon} tone={p?.follow_ups_due ? "amber" : "green"} loading={pipe.isLoading} value={String(p?.follow_ups_due ?? "—")}
          sub={p?.without_follow_up ? `${p.without_follow_up} with no date set` : undefined} />
        <StatCard label="Open pipeline" icon={TargetIcon} loading={pipe.isLoading} value={money(openValue.toFixed(2))} sub={`${openCount} leads`} />
        <StatCard label="Won this month" icon={TrophyIcon} loading={pipe.isLoading} value={money(p?.stages.won.value ?? "0")} sub={`${p?.stages.won.count ?? 0} leads`} />
        <StatCard label="Win rate (month)" icon={UserCheckIcon} loading={pipe.isLoading} value={p?.win_rate === null || p?.win_rate === undefined ? "—" : `${p.win_rate} %`} />
      </div>
      {p && openCount > 0 && <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {(["new", "contacted", "qualified", "proposal"] as Stage[]).map((s) => (
          <button key={s} type="button" onClick={() => setTab(s)} className="rounded-lg border bg-card p-2 text-left hover:bg-accent">
            <p className="text-xs text-muted-foreground">{STAGE_LABEL[s]}</p><p className="font-semibold">{p.stages[s].count} <span className="text-xs font-normal text-muted-foreground">· {money(p.stages[s].value)}</span></p>
          </button>))}
      </div>}
      <Card className="overflow-hidden">
        <div className="flex flex-col gap-2 border-b p-3 sm:flex-row sm:items-center sm:justify-between">
          <Tabs value={tab} onValueChange={setTab}><TabsList className="max-w-full overflow-x-auto">
            <TabsTrigger value="today">Due today</TabsTrigger><TabsTrigger value="open">All open</TabsTrigger>
            {STAGES.map(([v, l]) => <TabsTrigger key={v} value={v}>{l}</TabsTrigger>)}</TabsList></Tabs>
          <Input className="sm:w-56" placeholder="Search name, firm, phone" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {list.error ? <ErrorState error={list.error} retry={() => list.refetch()} /> : list.isLoading ? <TableSkeleton /> : !rows.length ? (
          <EmptyState icon={TargetIcon} title={tab === "today" ? "No follow-ups due" : "No leads here"} description="Add every enquiry — phone, WhatsApp, walk-in or IndiaMART — and set the next follow-up date so none is forgotten." />
        ) : (
          <StaticTable rows={rows} rowKey={(l) => l.id} columns={[
            { key: "n", header: "Lead", mobile: "title", cell: (l) => <button type="button" className="text-left" onClick={() => setOpen(l.id)}><p className="font-medium text-primary hover:underline">{l.name}{l.company ? <span className="text-muted-foreground"> · {l.company}</span> : null}</p>
              <p className="text-xs text-muted-foreground">{[l.phone, l.city, l.source].filter(Boolean).join(" · ") || "—"}</p></button> },
            { key: "s", header: "Stage", cell: (l) => <Badge variant={l.stage === "won" ? "default" : l.stage === "lost" ? "destructive" : "secondary"}>{STAGE_LABEL[l.stage]}</Badge> },
            { key: "f", header: "Next follow-up", mobile: "aside", cell: (l) => l.next_follow_up ? <span className={l.overdue ? "font-medium text-destructive" : ""}>{date(l.next_follow_up)}</span> : <span className="text-muted-foreground">—</span> },
            { key: "v", header: "Value", align: "right", cell: (l) => <span className="num">{Number(l.expected_value) ? money(l.expected_value) : "—"}</span> },
            { key: "a", header: "Owner", cell: (l) => l.assigned_to ?? "—" },
            { key: "x", header: "", mobile: "row", cell: (l) => <div className="flex justify-end gap-1"><Button size="sm" variant="outline" onClick={() => setOpen(l.id)}>Open</Button>{l.phone && <div className="flex justify-end gap-1">
              <Button asChild size="icon" variant="ghost" aria-label="Call"><a href={`tel:${l.phone}`}><PhoneIcon /></a></Button>
              <Button asChild size="icon" variant="ghost" aria-label="WhatsApp"><a href={wa(l.phone)} target="_blank" rel="noreferrer"><MessageCircleIcon /></a></Button></div>}</div> },
          ]} />
        )}
      </Card>
      {adding && <NewLead onClose={() => setAdding(false)} />}
      {open && <LeadDialog id={open} manage={manage} onClose={() => setOpen(null)} />}
    </>
  );
}

function NewLead({ onClose }: { onClose: () => void }) {
  const [f, setF] = useState({ name: "", company: "", phone: "", email: "", city: "", source: "", requirement: "", expected_value: "", next_follow_up: plusDays(1) });
  const save = useBizMutation<Record<string, unknown>>("POST", "/leads", { invalidate: [["leads"]], success: "Lead added", onSuccess: onClose });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF((x) => ({ ...x, [k]: e.target.value }));
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>New lead</DialogTitle><DialogDescription>Someone who asked about buying. Set when to follow up.</DialogDescription></DialogHeader>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate({ ...Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.trim() || null])), expected_value: f.expected_value || "0" }); }}>
          <Field label="Name" htmlFor="ld-name"><Input id="ld-name" autoFocus value={f.name} onChange={set("name")} /></Field>
          <Field label="Firm / shop" htmlFor="ld-co"><Input id="ld-co" value={f.company} onChange={set("company")} /></Field>
          <Field label="Mobile" htmlFor="ld-ph"><Input id="ld-ph" inputMode="tel" value={f.phone} onChange={set("phone")} /></Field>
          <Field label="City" htmlFor="ld-city"><Input id="ld-city" value={f.city} onChange={set("city")} /></Field>
          <Field label="Came from" htmlFor="ld-src"><NativeSelect id="ld-src" value={f.source} onChange={set("source")}><option value="">—</option>{SOURCES.map((s) => <option key={s}>{s}</option>)}</NativeSelect></Field>
          <Field label="Expected order value" htmlFor="ld-val"><Input id="ld-val" inputMode="decimal" className="num" value={f.expected_value} onChange={(e) => setF((x) => ({ ...x, expected_value: e.target.value.replace(/[^\d.]/g, "") }))} /></Field>
          <Field label="What they want" htmlFor="ld-req" className="sm:col-span-2"><Textarea id="ld-req" rows={2} value={f.requirement} onChange={set("requirement")} /></Field>
          <Field label="Follow up on" htmlFor="ld-fu"><Input id="ld-fu" type="date" value={f.next_follow_up} onChange={set("next_follow_up")} /></Field>
          <Field label="E-mail" htmlFor="ld-em"><Input id="ld-em" type="email" value={f.email} onChange={set("email")} /></Field>
          <DialogFooter className="sm:col-span-2">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!f.name.trim() || save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />} Add lead</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function LeadDialog({ id, manage, onClose }: { id: string; manage: boolean; onClose: () => void }) {
  const lead = useBizQuery<Lead & { activities: Activity[] }>(["leads", "one", id], `/leads/${id}`);
  const [kind, setKind] = useState<(typeof KINDS)[number][0]>("call");
  const [note, setNote] = useState("");
  const [next, setNext] = useState(plusDays(3));
  const [lostReason, setLostReason] = useState("");
  const [losing, setLosing] = useState(false);
  const logIt = useBizMutation<Record<string, unknown>>("POST", `/leads/${id}/activities`, { invalidate: [["leads"]], success: "Saved", onSuccess: () => setNote("") });
  const patch = useBizMutation<Record<string, unknown>>("PATCH", `/leads/${id}`, { invalidate: [["leads"]], success: "Lead updated", onSuccess: () => setLosing(false) });
  const convert = useBizMutation<undefined, { party_id: string; party_name: string }>("POST", `/leads/${id}/convert`, { invalidate: [["leads"], ["customers"]], success: (o) => `${o.party_name} is now a customer` });
  const l = lead.data;
  const closed = l?.stage === "won" || l?.stage === "lost";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        {!l ? <div className="py-10 text-center">{lead.error ? <ErrorState error={lead.error} /> : <Loader2Icon className="mx-auto animate-spin" />}</div> : <>
          <DialogHeader>
            <DialogTitle>{l.name}{l.company ? ` · ${l.company}` : ""}</DialogTitle>
            <DialogDescription>{[l.phone, l.email, l.city, l.source].filter(Boolean).join(" · ")}{l.requirement ? ` — ${l.requirement}` : ""}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 md:grid-cols-[1fr_15rem]">
            <div className="min-w-0 space-y-3">
              {!closed && <form className="space-y-2 rounded-lg border p-3" onSubmit={(e) => { e.preventDefault(); logIt.mutate({ kind, note, next_follow_up: next || null }); }}>
                <div className="flex flex-wrap gap-1">{KINDS.map(([v, lab]) => <Button key={v} type="button" size="sm" variant={kind === v ? "default" : "outline"} onClick={() => setKind(v)}>{lab}</Button>)}</div>
                <Textarea aria-label="What happened" rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Spoke to him — wants rates for 50 boxes, call after Diwali" />
                <div className="flex flex-wrap items-end gap-2">
                  <Field label="Next follow-up" htmlFor="ld-next"><Input id="ld-next" type="date" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
                  <div className="flex gap-1">{[1, 3, 7].map((n) => <Button key={n} type="button" size="sm" variant="ghost" onClick={() => setNext(plusDays(n))}>+{n}d</Button>)}</div>
                  <Button type="submit" className="ml-auto" disabled={!note.trim() || logIt.isPending}>{logIt.isPending && <Loader2Icon className="animate-spin" />} Save</Button>
                </div>
              </form>}
              <ol className="max-h-72 space-y-2 overflow-auto">
                {l.activities.map((a) => (
                  <li key={a.id} className="rounded-md bg-muted/50 p-2 text-sm"><p>{a.note}</p>
                    <p className="text-xs text-muted-foreground">{KINDS.find(([v]) => v === a.kind)?.[1]} · {new Date(a.happened_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}{a.by ? ` · ${a.by}` : ""}</p></li>
                ))}
                {!l.activities.length && <li className="text-sm text-muted-foreground">Nothing logged yet.</li>}
              </ol>
            </div>
            <div className="space-y-3">
              <Field label="Stage" htmlFor="ld-stage"><NativeSelect id="ld-stage" value={l.stage} disabled={!manage} onChange={(e) => {
                const v = e.target.value as Stage; if (v === "lost") setLosing(true); else patch.mutate({ stage: v }); }}>
                {STAGES.map(([v, lab]) => <option key={v} value={v}>{lab}</option>)}</NativeSelect></Field>
              {losing && <div className="space-y-2 rounded-md border p-2"><Input aria-label="Why lost" value={lostReason} onChange={(e) => setLostReason(e.target.value)} placeholder="Price, bought elsewhere…" />
                <Button size="sm" variant="destructive" disabled={!lostReason.trim()} onClick={() => patch.mutate({ stage: "lost", lost_reason: lostReason })}>Mark lost</Button></div>}
              {l.lost_reason && <p className="text-sm text-destructive">Lost: {l.lost_reason}</p>}
              <p className="text-sm">Expected value: <span className="num font-medium">{money(l.expected_value)}</span></p>
              <p className="text-sm text-muted-foreground">Owner: {l.assigned_to ?? "—"}</p>
              {l.phone && <div className="flex gap-2"><Button asChild size="sm" variant="outline"><a href={`tel:${l.phone}`}><PhoneIcon /> Call</a></Button>
                <Button asChild size="sm" variant="outline"><a href={wa(l.phone)} target="_blank" rel="noreferrer"><MessageCircleIcon /> WhatsApp</a></Button></div>}
              {manage && (l.party_id ? (
                <Button asChild size="sm" className="w-full"><Link href={`/sales/new?type=quotation&party=${l.party_id}&party_name=${encodeURIComponent(l.company || l.name)}`}>Make quotation</Link></Button>
              ) : (
                <Button size="sm" className="w-full" disabled={convert.isPending} onClick={() => convert.mutate(undefined)}>{convert.isPending && <Loader2Icon className="animate-spin" />}<UserCheckIcon /> Won — make customer</Button>
              ))}
            </div>
          </div>
        </>}
      </DialogContent>
    </Dialog>
  );
}

