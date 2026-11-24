"use client";

import { ArrowLeftIcon, EyeIcon, Loader2Icon, MailIcon, PencilIcon, PlusIcon, SendIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/data/confirm";
import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox, Switch } from "@/components/ui/switch";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";
import { dateTime } from "@/lib/format";

interface Report {
  id: string; name: string; frequency: "daily" | "weekly" | "monthly"; weekday: number; day_of_month: number; hour: number;
  recipients: string[]; sections: string[]; integration_id: string | null; is_active: boolean; next_run_at: string | null;
  last_sent_at: string | null; last_error: string | null;
}
type Draft = Omit<Report, "id" | "next_run_at" | "last_sent_at" | "last_error">;
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const EMPTY: Draft = { name: "Morning summary", frequency: "daily", weekday: 0, day_of_month: 1, hour: 8, recipients: [],
  sections: ["sales", "collections", "dues", "low_stock", "expiry"], integration_id: null, is_active: true };

function when(r: Pick<Report, "frequency" | "weekday" | "day_of_month" | "hour">) {
  const h = `${String(r.hour).padStart(2, "0")}:00`;
  if (r.frequency === "daily") return `Every day at ${h} — covers yesterday`;
  if (r.frequency === "weekly") return `Every ${DAYS[r.weekday]} at ${h} — covers the last 7 days`;
  return `On day ${r.day_of_month} of each month at ${h} — covers last month`;
}

export default function ScheduledReportsPage() {
  const { base, can, isOwner } = useBusiness();
  const allowed = isOwner || can("reports.view", "automation.manage");
  const list = useBizQuery<Report[]>(["scheduled-reports"], "/scheduled-reports", undefined, { enabled: allowed });
  const sections = useBizQuery<{ key: string; label: string }[]>(["scheduled-sections"], "/scheduled-reports/sections", undefined, { enabled: allowed, staleTime: Infinity });
  const [editing, setEditing] = useState<{ id?: string; draft: Draft; key: string } | null>(null);
  const [deleting, setDeleting] = useState<Report | null>(null);
  const [preview, setPreview] = useState<{ name: string; html: string } | null>(null);
  const remove = useBizMutation("DELETE", () => `/scheduled-reports/${deleting?.id}`, { invalidate: [["scheduled-reports"]], success: "Report deleted" });
  const sendNow = useBizMutation<string, { queued: number }>("POST", (id) => `/scheduled-reports/${id}/send-now`,
    { invalidate: [["scheduled-reports"]], success: (o) => `Queued for ${o.queued} recipient${o.queued === 1 ? "" : "s"}` });
  const label = new Map((sections.data ?? []).map((s) => [s.key, s.label]));
  const open = (draft: Draft, id?: string) => setEditing({ id, draft, key: `${id ?? "new"}-${Date.now()}` });

  return (
    <>
      <Link href="/reports" className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeftIcon className="size-4" /> Reports</Link>
      <PageHeader title="Scheduled reports" description="Your numbers in your inbox: sales, cash, dues, low stock and expiry — every morning, week or month."
        actions={allowed && <Button onClick={() => open(EMPTY)}><PlusIcon /> New schedule</Button>} />
      {!allowed ? <Card><EmptyState title="Not available" description="Needs reports.view and automation.manage." /></Card>
        : list.error ? <Card><ErrorState error={list.error} retry={() => list.refetch()} /></Card>
        : list.isLoading ? <Skeleton className="h-40" />
        : !list.data?.length ? <Card><EmptyState icon={MailIcon} title="No scheduled reports" description="Create a morning summary — it is e-mailed through your connected Gmail or SMTP."
            action={<Button onClick={() => open(EMPTY)}><PlusIcon /> New schedule</Button>} /></Card>
        : (
          <div className="grid gap-3 lg:grid-cols-2">
            {list.data.map((r) => (
              <Card key={r.id} className={r.is_active ? "" : "opacity-70"}>
                <CardContent className="space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div><p className="font-semibold">{r.name}</p><p className="text-sm text-muted-foreground">{when(r)}</p></div>
                    <Badge variant={r.is_active ? "default" : "secondary"}>{r.is_active ? "On" : "Off"}</Badge>
                  </div>
                  <p className="text-sm">To {r.recipients.join(", ")}</p>
                  <div className="flex flex-wrap gap-1">{r.sections.map((s) => <Badge key={s} variant="secondary">{label.get(s) ?? s}</Badge>)}</div>
                  <p className="text-xs text-muted-foreground">{r.next_run_at && r.is_active ? `Next: ${dateTime(r.next_run_at)}` : ""}{r.last_sent_at ? ` · last sent ${dateTime(r.last_sent_at)}` : ""}</p>
                  {r.last_error && <p className="rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive">Last attempt failed: {r.last_error}</p>}
                  <div className="flex flex-wrap justify-end gap-1 border-t pt-2">
                    <Button size="sm" variant="ghost" onClick={async () => {
                      try { const res = await api<Response>(`${base}/scheduled-reports/${r.id}/preview`, { raw: true }); setPreview({ name: r.name, html: await res.text() }); }
                      catch (e) { toast.error(e instanceof ApiError ? e.message : "Preview failed"); }
                    }}><EyeIcon /> Preview</Button>
                    <Button size="sm" variant="ghost" disabled={sendNow.isPending} onClick={() => sendNow.mutate(r.id)}><SendIcon /> Send now</Button>
                    <Button size="icon" variant="ghost" aria-label="Edit schedule" onClick={() => open({ name: r.name, frequency: r.frequency, weekday: r.weekday, day_of_month: r.day_of_month,
                      hour: r.hour, recipients: r.recipients, sections: r.sections, integration_id: r.integration_id, is_active: r.is_active }, r.id)}><PencilIcon /></Button>
                    <Button size="icon" variant="ghost" aria-label="Delete schedule" onClick={() => setDeleting(r)}><Trash2Icon /></Button>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}

      {editing && <ReportDialog key={editing.key} id={editing.id} initial={editing.draft} sections={sections.data ?? []} onClose={() => setEditing(null)} />}
      <Dialog open={!!preview} onOpenChange={(v) => !v && setPreview(null)}>
        <DialogContent wide className="gap-3">
          <DialogHeader><DialogTitle>{preview?.name}</DialogTitle><DialogDescription>As it would be sent now.</DialogDescription></DialogHeader>
          {/* Server-rendered, escaped e-mail HTML; sandboxed with no scripts. */}
          <iframe title="Report preview" sandbox="" srcDoc={preview?.html ?? ""} className="h-[65vh] w-full rounded-md border bg-white" />
        </DialogContent>
      </Dialog>
      <ConfirmDialog open={!!deleting} onOpenChange={(v) => !v && setDeleting(null)} destructive confirm="Delete"
        title={`Delete “${deleting?.name}”?`} onConfirm={() => remove.mutateAsync(undefined)} />
    </>
  );
}

function ReportDialog({ id, initial, sections, onClose }: { id?: string; initial: Draft; sections: { key: string; label: string }[]; onClose: () => void }) {
  const [d, setD] = useState<Draft>(initial);
  const [to, setTo] = useState(initial.recipients.join(", "));
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }));
  const save = useBizMutation<Draft>(id ? "PUT" : "POST", id ? `/scheduled-reports/${id}` : "/scheduled-reports",
    { invalidate: [["scheduled-reports"]], success: "Schedule saved", onSuccess: onClose });
  const recipients = to.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>{id ? "Edit schedule" : "New scheduled report"}</DialogTitle></DialogHeader>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate({ ...d, recipients }); }}>
          <Field label="Name" htmlFor="sr-name" className="sm:col-span-2"><Input id="sr-name" value={d.name} maxLength={120} onChange={(e) => set({ name: e.target.value })} /></Field>
          <Field label="How often" htmlFor="sr-freq"><NativeSelect id="sr-freq" value={d.frequency} onChange={(e) => set({ frequency: e.target.value as Draft["frequency"] })}>
            <option value="daily">Every day (yesterday’s numbers)</option><option value="weekly">Every week (last 7 days)</option><option value="monthly">Every month (last month)</option></NativeSelect></Field>
          <Field label="At" htmlFor="sr-hour"><NativeSelect id="sr-hour" value={d.hour} onChange={(e) => set({ hour: Number(e.target.value) })}>
            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}</NativeSelect></Field>
          {d.frequency === "weekly" && <Field label="On" htmlFor="sr-wd"><NativeSelect id="sr-wd" value={d.weekday} onChange={(e) => set({ weekday: Number(e.target.value) })}>
            {DAYS.map((n, i) => <option key={n} value={i}>{n}</option>)}</NativeSelect></Field>}
          {d.frequency === "monthly" && <Field label="Day of month" htmlFor="sr-dom"><NativeSelect id="sr-dom" value={d.day_of_month} onChange={(e) => set({ day_of_month: Number(e.target.value) })}>
            {Array.from({ length: 28 }, (_, i) => <option key={i} value={i + 1}>{i + 1}</option>)}</NativeSelect></Field>}
          <Field label="Send to" htmlFor="sr-to" hint="E-mail addresses, comma-separated (up to 10)" className="sm:col-span-2">
            <Input id="sr-to" value={to} onChange={(e) => setTo(e.target.value)} placeholder="you@yourshop.in, accounts@yourshop.in" /></Field>
          <fieldset className="sm:col-span-2"><legend className="mb-2 text-sm font-medium">Include</legend>
            <div className="grid gap-2 sm:grid-cols-2">{sections.map((s) => (
              <label key={s.key} className="flex items-center gap-2 text-sm"><Checkbox checked={d.sections.includes(s.key)}
                onCheckedChange={(v) => set({ sections: v ? [...d.sections, s.key] : d.sections.filter((x) => x !== s.key) })} /> {s.label}</label>
            ))}</div>
          </fieldset>
          <DialogFooter className="items-center sm:col-span-2 sm:justify-between">
            <label className="flex items-center gap-2 text-sm"><Switch checked={d.is_active} onCheckedChange={(v) => set({ is_active: v })} /> Active</label>
            <div className="flex gap-2"><Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={!d.name.trim() || !recipients.length || !d.sections.length || save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />}Save</Button></div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
