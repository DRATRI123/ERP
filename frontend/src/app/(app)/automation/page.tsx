"use client";

import {
  AlarmClockIcon, BellRingIcon, BotIcon, DatabaseBackupIcon, HandHeartIcon, Loader2Icon, MailIcon, PackageMinusIcon, PencilIcon,
  PlusIcon, SendIcon, SparklesIcon, Trash2Icon, TrendingUpIcon, WebhookIcon, XIcon, ZapIcon,
} from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { ConfirmDialog } from "@/components/data/confirm";
import { type Column, DataTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox, Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { dateTime } from "@/lib/format";

// ---------------------------------------------------------------------------- types

interface FieldSpec { key: string; label: string; type: "number" | "text" | "choice"; choices?: string[] }
interface TriggerSpec { type: string; label: string; fields: FieldSpec[]; variables: string[]; actions: string[] }
interface Catalog {
  triggers: TriggerSpec[]; actions: { type: string; label: string }[]; operators: { op: string; label: string }[];
  max_delay_minutes: number;
}
interface Condition { field: string; op: string; value: string }
interface Rule {
  id: string; name: string; is_active: boolean; trigger: string; conditions: Condition[]; delay_minutes: number;
  action: string; action_config: Record<string, unknown>; last_run_at: string | null; run_count: number; created_at: string;
}
interface Run {
  id: string; rule_id: string; event_type: string; status: "scheduled" | "done" | "skipped" | "failed";
  run_at: string; executed_at: string | null; detail: string | null; message_id: string | null; created_at: string;
}
type Draft = Omit<Rule, "id" | "last_run_at" | "run_count" | "created_at">;

const CHANNELS: Record<string, string> = { email: "E-mail", whatsapp: "WhatsApp", sms: "SMS" };

const TEMPLATES: { icon: React.ComponentType<{ className?: string }>; title: string; text: string; draft: Draft }[] = [
  { icon: AlarmClockIcon, title: "Overdue reminder", text: "WhatsApp the invoice 3 days after it becomes overdue — skipped if it gets paid.",
    draft: { name: "Overdue reminder (WhatsApp)", is_active: true, trigger: "invoice.overdue", conditions: [], delay_minutes: 3 * 1440,
      action: "send_document", action_config: { channel: "whatsapp" } } },
  { icon: TrendingUpIcon, title: "Big-sale alert", text: "E-mail me when a single invoice is ₹50,000 or more.",
    draft: { name: "Big-sale alert", is_active: true, trigger: "invoice.created", conditions: [{ field: "grand_total", op: "gte", value: "50000" }],
      delay_minutes: 0, action: "notify_email", action_config: { to: [], subject: "Big sale: {number} — ₹{grand_total}",
        body: "{party_name} was invoiced ₹{grand_total} ({number}) at {business}." } } },
  { icon: PackageMinusIcon, title: "Low-stock alert", text: "E-mail purchasing when a product reaches its reorder level.",
    draft: { name: "Low-stock alert", is_active: true, trigger: "stock.low", conditions: [], delay_minutes: 0, action: "notify_email",
      action_config: { to: [], subject: "Reorder {name}", body: "{name} ({sku}) is down to {on_hand}. Reorder level: {reorder_level}." } } },
  { icon: HandHeartIcon, title: "Welcome e-mail", text: "Greet every new customer who has an e-mail address.",
    draft: { name: "Welcome new customers", is_active: true, trigger: "customer.created", conditions: [], delay_minutes: 0, action: "notify_email",
      action_config: { to: ["customer"], subject: "Welcome to {business}", body: "Namaste {name},\n\nThank you for choosing {business}. We look forward to serving you.\n\nWarm regards,\n{business}" } } },
];

const EMPTY: Draft = { name: "", is_active: true, trigger: "invoice.created", conditions: [], delay_minutes: 0, action: "notify_email",
  action_config: { to: [], subject: "", body: "" } };

function delayText(m: number): string {
  if (!m) return "immediately";
  if (m % 1440 === 0) return `after ${m / 1440} day${m === 1440 ? "" : "s"}`;
  if (m % 60 === 0) return `after ${m / 60} hour${m === 60 ? "" : "s"}`;
  return `after ${m} min`;
}

// ---------------------------------------------------------------------------- page

export default function AutomationPage() {
  const { can } = useBusiness();
  const allowed = can("automation.manage");
  return (
    <>
      <PageHeader title="Automation" description="Your own “when this happens, do that” rules, plus the background jobs that always run." />
      {allowed ? <Rules /> : <Card className="mb-6 border-dashed"><CardContent className="flex items-start gap-3 text-sm">
        <BotIcon className="mt-0.5 size-5 text-muted-foreground" /><p className="text-muted-foreground">Managing automation rules needs the <b>automation.manage</b> permission. Ask the owner.</p>
      </CardContent></Card>}
      <BuiltIn />
    </>
  );
}

function Rules() {
  const catalog = useBizQuery<Catalog>(["automation", "catalog"], "/automation/catalog", undefined, { staleTime: Infinity });
  const rules = useBizQuery<Rule[]>(["automation", "rules"], "/automation/rules");
  const [editing, setEditing] = useState<{ key: string; id?: string; draft: Draft } | null>(null);
  const [deleting, setDeleting] = useState<Rule | null>(null);
  const toggle = useBizMutation<{ id: string; is_active: boolean }>("PATCH", (b) => `/automation/rules/${b.id}`,
    { invalidate: [["automation"]], success: (r) => ((r as unknown as Rule).is_active ? "Rule switched on" : "Rule paused") });
  const remove = useBizMutation<undefined>("DELETE", () => `/automation/rules/${deleting?.id}`, { invalidate: [["automation"]], success: "Rule deleted" });
  const open = (draft: Draft, id?: string) => setEditing({ key: `${id ?? "new"}-${Date.now()}`, id, draft });

  if (catalog.error || rules.error) return <Card className="mb-6"><ErrorState error={catalog.error ?? rules.error} retry={() => { catalog.refetch(); rules.refetch(); }} /></Card>;
  const triggers = new Map((catalog.data?.triggers ?? []).map((t) => [t.type, t]));
  const ruleName = new Map((rules.data ?? []).map((r) => [r.id, r.name]));

  return (
    <Tabs defaultValue="rules" className="mb-8">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <TabsList><TabsTrigger value="rules">Rules{rules.data?.length ? ` · ${rules.data.length}` : ""}</TabsTrigger><TabsTrigger value="runs">Run history</TabsTrigger></TabsList>
        <Button onClick={() => open(EMPTY)} disabled={!catalog.data}><PlusIcon /> New rule</Button>
      </div>
      <TabsContent value="rules" className="space-y-6">
        {rules.isLoading || catalog.isLoading ? <div className="space-y-3">{[0, 1].map((i) => <Skeleton key={i} className="h-24" />)}</div>
          : rules.data!.length === 0 ? null : (
            <div className="grid gap-3">
              {rules.data!.map((r) => (
                <Card key={r.id} className={r.is_active ? "" : "opacity-70"}>
                  <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center">
                    <span className={`flex size-10 shrink-0 items-center justify-center rounded-lg ${r.is_active ? "bg-primary-soft text-primary" : "bg-secondary text-muted-foreground"}`}>
                      {r.action === "send_document" ? <SendIcon className="size-5" /> : <MailIcon className="size-5" />}</span>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">{r.name}</p>
                      <Sentence rule={r} trigger={triggers.get(r.trigger)} />
                      <p className="mt-1 text-xs text-muted-foreground">{r.run_count ? `Ran ${r.run_count} time${r.run_count === 1 ? "" : "s"} · last ${dateTime(r.last_run_at)}` : "Has not run yet"}</p>
                    </div>
                    <div className="flex items-center gap-1">
                      <Switch checked={r.is_active} aria-label={r.is_active ? "Pause rule" : "Switch rule on"} onCheckedChange={(v) => toggle.mutate({ id: r.id, is_active: v })} />
                      <Button size="icon" variant="ghost" aria-label="Edit rule" onClick={() => open({ name: r.name, is_active: r.is_active, trigger: r.trigger, conditions: r.conditions, delay_minutes: r.delay_minutes, action: r.action, action_config: r.action_config }, r.id)}><PencilIcon /></Button>
                      <Button size="icon" variant="ghost" aria-label="Delete rule" onClick={() => setDeleting(r)}><Trash2Icon /></Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        <div>
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-muted-foreground"><SparklesIcon className="size-4" /> {rules.data?.length ? "Start from a template" : "Start with one of these"}</h2>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {TEMPLATES.map((t) => (
              <button key={t.title} disabled={!catalog.data} onClick={() => open(t.draft)} className="rounded-xl border bg-card p-4 text-left transition-colors hover:border-primary/40 disabled:opacity-60">
                <t.icon className="mb-2 size-5 text-primary" /><p className="font-medium">{t.title}</p><p className="text-sm text-muted-foreground">{t.text}</p>
              </button>
            ))}
          </div>
        </div>
      </TabsContent>
      <TabsContent value="runs"><RunHistory names={ruleName} /></TabsContent>

      {editing && catalog.data && <RuleDialog key={editing.key} catalog={catalog.data} id={editing.id} initial={editing.draft} onClose={() => setEditing(null)} />}
      <ConfirmDialog open={!!deleting} onOpenChange={(v) => !v && setDeleting(null)} destructive confirm="Delete rule"
        title={`Delete “${deleting?.name}”?`} description="Scheduled runs that have not happened yet are dropped. Messages already sent are not affected."
        onConfirm={() => remove.mutateAsync(undefined)} />
    </Tabs>
  );
}

function Sentence({ rule, trigger }: { rule: Pick<Rule, "trigger" | "conditions" | "delay_minutes" | "action" | "action_config">; trigger?: TriggerSpec }) {
  const label = (k: string) => trigger?.fields.find((f) => f.key === k)?.label.toLowerCase() ?? k;
  const OPS: Record<string, string> = { eq: "is", ne: "is not", gt: ">", gte: "≥", lt: "<", lte: "≤", contains: "contains", in: "is one of" };
  const cfg = rule.action_config as { channel?: string; to?: string[] };
  const to = (cfg.to ?? []).map((t) => (t === "customer" ? "the customer" : t)).join(", ");
  return (
    <p className="text-sm text-muted-foreground">
      <b className="font-medium text-foreground">When</b> {trigger?.label.toLowerCase() ?? rule.trigger}
      {rule.conditions.map((c, i) => <span key={i}> · <b className="font-medium text-foreground">{i ? "and" : "if"}</b> {label(c.field)} {OPS[c.op]} {c.value}</span>)}
      {" · "}<b className="font-medium text-foreground">{delayText(rule.delay_minutes)}</b>{" · "}
      {rule.action === "send_document" ? `send it by ${CHANNELS[cfg.channel ?? "email"]}` : `e-mail ${to || "…"}`}
    </p>
  );
}

// ---------------------------------------------------------------------------- builder

function RuleDialog({ catalog, id, initial, onClose }: { catalog: Catalog; id?: string; initial: Draft; onClose: () => void }) {
  const [d, setD] = useState<Draft>(initial);
  const [unit, setUnit] = useState(initial.delay_minutes && initial.delay_minutes % 1440 === 0 ? 1440 : initial.delay_minutes % 60 === 0 && initial.delay_minutes ? 60 : 1);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const save = useBizMutation<Draft>(id ? "PATCH" : "POST", id ? `/automation/rules/${id}` : "/automation/rules",
    { invalidate: [["automation"]], success: id ? "Rule saved" : "Rule created — it applies to new events from now on", onSuccess: onClose });
  const trigger = catalog.triggers.find((t) => t.type === d.trigger) ?? catalog.triggers[0]!;
  const cfg = d.action_config as { channel?: string; message?: string; to?: string[]; subject?: string; body?: string };
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }));
  const setCfg = (patch: Record<string, unknown>) => setD((x) => ({ ...x, action_config: { ...x.action_config, ...patch } }));
  const emails = (cfg.to ?? []).filter((t) => t !== "customer");
  const toCustomer = (cfg.to ?? []).includes("customer");
  const customerPossible = d.trigger.startsWith("invoice.") || d.trigger === "customer.created";

  function changeTrigger(type: string) {
    const t = catalog.triggers.find((x) => x.type === type)!;
    const action = t.actions.includes(d.action) ? d.action : t.actions[0]!;
    set({ trigger: type, action, conditions: d.conditions.filter((c) => t.fields.some((f) => f.key === c.field)),
      action_config: action === d.action ? d.action_config : action === "send_document" ? { channel: "email" } : { to: [], subject: "", body: "" } });
  }
  function insertVar(v: string) {
    const el = bodyRef.current;
    const body = cfg.body ?? "";
    const at = el?.selectionStart ?? body.length;
    setCfg({ body: `${body.slice(0, at)}{${v}}${body.slice(el?.selectionEnd ?? at)}` });
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(at + v.length + 2, at + v.length + 2); });
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide className="max-h-[92vh] overflow-y-auto">
        <DialogHeader><DialogTitle>{id ? "Edit rule" : "New automation rule"}</DialogTitle>
          <DialogDescription>Rules run in the background a few seconds after the event, and only for events after the rule was created.</DialogDescription></DialogHeader>
        <form className="grid gap-5" onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ ...d, name: d.name.trim() || trigger.label });
        }}>
          <Field label="Name"><Input value={d.name} onChange={(e) => set({ name: e.target.value })} placeholder={trigger.label} maxLength={120} /></Field>

          <Step n="1" title="When">
            <NativeSelect value={d.trigger} onChange={(e) => changeTrigger(e.target.value)} aria-label="Trigger">
              {catalog.triggers.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
            </NativeSelect>
          </Step>

          <Step n="2" title="Only if" optional>
            <div className="grid gap-2">
              {d.conditions.map((c, i) => {
                const f = trigger.fields.find((x) => x.key === c.field) ?? trigger.fields[0]!;
                const ops = catalog.operators.filter((o) => f.type === "number" ? !["contains"].includes(o.op) : f.type === "choice" ? ["eq", "ne", "in"].includes(o.op) : ["eq", "ne", "contains", "in"].includes(o.op));
                const upd = (patch: Partial<Condition>) => set({ conditions: d.conditions.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
                return (
                  <div key={i} className="grid grid-cols-[1fr_auto] gap-2 sm:grid-cols-[1.2fr_0.8fr_1fr_auto]">
                    <NativeSelect value={c.field} aria-label="Field" onChange={(e) => {
                      const nf = trigger.fields.find((x) => x.key === e.target.value)!;
                      upd({ field: nf.key, op: nf.type === "number" ? "gte" : "eq", value: nf.choices?.[0] ?? "" });
                    }}>{trigger.fields.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}</NativeSelect>
                    <Button type="button" size="icon" variant="ghost" className="sm:order-last" aria-label="Remove condition" onClick={() => set({ conditions: d.conditions.filter((_, j) => j !== i) })}><XIcon /></Button>
                    <NativeSelect value={c.op} aria-label="Comparison" onChange={(e) => upd({ op: e.target.value })}>
                      {ops.map((o) => <option key={o.op} value={o.op}>{o.label}</option>)}</NativeSelect>
                    {f.type === "choice" && c.op !== "in" ? (
                      <NativeSelect value={c.value} aria-label="Value" onChange={(e) => upd({ value: e.target.value })}>
                        {f.choices!.map((x) => <option key={x} value={x}>{x.replace(/_/g, " ")}</option>)}</NativeSelect>
                    ) : <Input value={c.value} aria-label="Value" inputMode={f.type === "number" && c.op !== "in" ? "decimal" : undefined}
                      placeholder={c.op === "in" ? "a, b, c" : f.type === "number" ? "0" : "text"} onChange={(e) => upd({ value: e.target.value })} />}
                  </div>
                );
              })}
              {d.conditions.length < 10 && <div><Button type="button" variant="outline" size="sm" onClick={() => {
                const f = trigger.fields[0]!;
                set({ conditions: [...d.conditions, { field: f.key, op: f.type === "number" ? "gte" : "eq", value: f.choices?.[0] ?? "" }] });
              }}><PlusIcon /> Add condition</Button></div>}
              {d.trigger === "invoice.overdue" && <p className="text-xs text-muted-foreground">Overdue rules are skipped automatically once the invoice is paid or cancelled.</p>}
            </div>
          </Step>

          <Step n="3" title="Wait">
            <div className="flex items-center gap-2">
              <Input type="number" min={0} className="w-28" aria-label="Delay" value={d.delay_minutes / unit || 0}
                onChange={(e) => set({ delay_minutes: Math.max(0, Math.round(Number(e.target.value) * unit)) })} />
              <NativeSelect className="w-32" value={unit} aria-label="Delay unit" onChange={(e) => {
                const u = Number(e.target.value); set({ delay_minutes: Math.round((d.delay_minutes / unit) * u) }); setUnit(u);
              }}><option value={1}>minutes</option><option value={60}>hours</option><option value={1440}>days</option></NativeSelect>
              <span className="text-sm text-muted-foreground">{d.delay_minutes ? "then check the conditions again" : "0 = straight away"}</span>
            </div>
          </Step>

          <Step n="4" title="Then">
            <div className="grid gap-3">
              <NativeSelect value={d.action} aria-label="Action" onChange={(e) => set({ action: e.target.value,
                action_config: e.target.value === "send_document" ? { channel: "email" } : { to: [], subject: "", body: "" } })}>
                {catalog.actions.filter((a) => trigger.actions.includes(a.type)).map((a) => <option key={a.type} value={a.type}>{a.label}</option>)}
              </NativeSelect>
              {d.action === "send_document" ? (
                <div className="grid gap-3 sm:grid-cols-[180px_1fr]">
                  <Field label="Channel"><NativeSelect value={cfg.channel ?? "email"} onChange={(e) => setCfg({ channel: e.target.value })}>
                    {Object.entries(CHANNELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</NativeSelect></Field>
                  <Field label="Note in the e-mail" hint={cfg.channel === "email" || !cfg.channel ? "Optional, shown above the invoice details." : "WhatsApp and SMS use your approved template."}>
                    <Input value={cfg.message ?? ""} disabled={!!cfg.channel && cfg.channel !== "email"} onChange={(e) => setCfg({ message: e.target.value })} placeholder="A gentle reminder that this invoice is due." /></Field>
                  <p className="text-xs text-muted-foreground sm:col-span-2">Sent to the customer’s e-mail or phone on the invoice, through the integration you connected. <Link href="/integrations" className="text-blue hover:underline">Integrations</Link></p>
                </div>
              ) : (
                <div className="grid gap-3">
                  <Field label="To" hint="E-mail addresses, comma-separated (up to 10).">
                    <Input value={emails.join(", ")} placeholder="you@yourshop.in, accounts@yourshop.in" onChange={(e) => {
                      const list = e.target.value.split(",").map((x) => x.trim()).filter(Boolean);
                      setCfg({ to: [...(toCustomer ? ["customer"] : []), ...list] });
                    }} /></Field>
                  {customerPossible && <label className="flex items-center gap-2 text-sm"><Checkbox checked={toCustomer} onCheckedChange={(v) =>
                    setCfg({ to: [...(v ? ["customer"] : []), ...emails] })} /> Also send to the customer (if they have an e-mail address)</label>}
                  <Field label="Subject"><Input value={cfg.subject ?? ""} maxLength={200} onChange={(e) => setCfg({ subject: e.target.value })} /></Field>
                  <Field label="Message" hint={<span className="flex flex-wrap items-center gap-1">Insert: {[...trigger.variables, "business"].map((v) => (
                    <button key={v} type="button" className="rounded bg-secondary px-1.5 py-0.5 font-mono text-[11px] hover:bg-primary-soft hover:text-primary" onClick={() => insertVar(v)}>{`{${v}}`}</button>))}</span>}>
                    <Textarea ref={bodyRef} rows={5} value={cfg.body ?? ""} maxLength={5000} onChange={(e) => setCfg({ body: e.target.value })} /></Field>
                </div>
              )}
            </div>
          </Step>

          <div className="rounded-lg bg-muted/60 px-3 py-2"><Sentence rule={d} trigger={trigger} /></div>
          <DialogFooter className="items-center sm:justify-between">
            <label className="flex items-center gap-2 text-sm"><Switch checked={d.is_active} onCheckedChange={(v) => set({ is_active: v })} /> Active</label>
            <div className="flex gap-2"><Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={save.isPending || d.conditions.some((c) => !c.value.trim())}>{save.isPending && <Loader2Icon className="animate-spin" />}{id ? "Save rule" : "Create rule"}</Button></div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Step({ n, title, optional, children }: { n: string; title: string; optional?: boolean; children: React.ReactNode }) {
  return (
    <section className="grid gap-2 sm:grid-cols-[110px_1fr] sm:gap-4">
      <p className="flex items-center gap-2 self-start pt-1.5 text-sm font-semibold"><span className="flex size-5 items-center justify-center rounded-full bg-primary text-[11px] text-primary-foreground">{n}</span>
        {title}{optional && <span className="font-normal text-muted-foreground sm:hidden">(optional)</span>}</p>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------------------- history & built-in jobs

const RUN_BADGE: Record<Run["status"], ["default" | "blue" | "secondary" | "destructive", string]> = {
  done: ["default", "Done"], scheduled: ["blue", "Scheduled"], skipped: ["secondary", "Skipped"], failed: ["destructive", "Failed"],
};

function RunHistory({ names }: { names: Map<string, string> }) {
  const [status, setStatus] = useState("");
  const cols: Column<Run>[] = [
    { key: "rule", header: "Rule", cell: (r) => <span className="font-medium">{names.get(r.rule_id) ?? "Deleted rule"}</span> },
    { key: "event", header: "Event", cell: (r) => <code className="text-xs">{r.event_type}</code>, hideOnMobile: true },
    { key: "status", header: "Status", cell: (r) => <Badge variant={RUN_BADGE[r.status][0]}>{RUN_BADGE[r.status][1]}</Badge> },
    { key: "when", header: "When", cell: (r) => <span className="text-sm">{r.executed_at ? dateTime(r.executed_at) : `due ${dateTime(r.run_at)}`}</span> },
    { key: "detail", header: "Detail", cell: (r) => <span className="block max-w-80 truncate text-sm text-muted-foreground" title={r.detail ?? undefined}>{r.detail ?? (r.message_id ? "Message queued" : "")}</span>, hideOnMobile: true },
  ];
  return (
    <DataTable<Run> queryKey={["automation", "runs"]} path="/automation/runs" columns={cols} searchParam={null}
      filters={{ status: status || undefined }}
      toolbar={<NativeSelect className="w-40" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
        <option value="">All runs</option>{Object.entries(RUN_BADGE).map(([k, [, l]]) => <option key={k} value={k}>{l}</option>)}</NativeSelect>}
      empty={<EmptyState icon={ZapIcon} title="No runs yet" description="Runs appear here when an event matches one of your rules." />} />
  );
}

function BuiltIn() {
  const jobs = [
    { icon: DatabaseBackupIcon, title: "Daily encrypted backups", text: "With retention, off-site copy and a monthly restore drill.", href: "/settings" },
    { icon: BellRingIcon, title: "Overdue invoice events", text: "Raised daily for unpaid invoices past their due date.", href: "/developer" },
    { icon: SendIcon, title: "Message delivery", text: "E-mail, WhatsApp and SMS with automatic retries.", href: "/integrations" },
    { icon: WebhookIcon, title: "Webhooks", text: "Signed notifications to your other tools.", href: "/developer" },
  ];
  return (
    <Card>
      <CardHeader><CardTitle className="text-base">Always running</CardTitle></CardHeader>
      <CardContent className="grid gap-3 pt-0 sm:grid-cols-2 xl:grid-cols-4">
        {jobs.map((r) => (
          <Link key={r.title} href={r.href} className="flex gap-3 rounded-lg p-2 transition-colors hover:bg-accent">
            <r.icon className="mt-0.5 size-5 shrink-0 text-primary" />
            <div><p className="text-sm font-medium">{r.title}</p><p className="text-xs text-muted-foreground">{r.text}</p></div>
          </Link>
        ))}
      </CardContent>
    </Card>
  );
}
