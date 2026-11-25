"use client";

import { useQueryClient } from "@tanstack/react-query";
import { BanknoteIcon, Loader2Icon, PencilIcon, PlusIcon, SendIcon, Trash2Icon, UserPlusIcon, UsersIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/data/confirm";
import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError, openFile } from "@/lib/api";
import { date, money, todayISO } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Employee {
  id: string; code: string; name: string; designation: string | null; phone: string | null; email: string | null; branch_id: string | null;
  joining_date: string; leaving_date: string | null; pay_type: "monthly" | "daily"; monthly_salary: string; daily_wage: string;
  commission_percent: string; user_id: string | null; pan: string | null; bank_account: string | null; ifsc: string | null; is_active: boolean; notes: string | null;
  pf_enabled?: boolean; uan?: string | null; basic_percent?: string; pf_wage_cap?: boolean; esi_enabled?: boolean; esi_number?: string | null; pt_state?: string | null; tds_enabled?: boolean;
}
type EmployeeDraft = Omit<Employee, "id">;
type Mark = "present" | "absent" | "half_day" | "leave" | "holiday";
interface Line { employee_id: string; employee_name: string; pay_type: string; days_in_month: number; payable_days: string; paid_days: string;
  base: string; sales: string; commission: string; bonus: string; deductions: string; net: string; note: string | null;
  pf_employee?: string; pf_employer?: string; esi_employee?: string; esi_employer?: string; pt?: string; tds?: string }
interface Run { id: string; month: string; status: "draft" | "posted" | "paid"; gross: string; deductions: string; net: string; lines: Line[] }

const MARKS: { v: Mark | null; label: string; short: string; cls: string }[] = [
  { v: null, label: "Present (not marked)", short: "", cls: "" },
  { v: "absent", label: "Absent (unpaid)", short: "A", cls: "bg-destructive/15 text-destructive" },
  { v: "half_day", label: "Half day", short: "½", cls: "bg-warning/20 text-amber-700" },
  { v: "leave", label: "Paid leave", short: "L", cls: "bg-blue-soft text-blue" },
  { v: "holiday", label: "Holiday", short: "H", cls: "bg-secondary text-secondary-foreground" },
];
const monthISO = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
const monthLabel = (m: string) => new Date(`${m}T00:00:00`).toLocaleDateString("en-IN", { month: "long", year: "numeric" });

export default function StaffPage() {
  const { can, isOwner } = useBusiness();
  const pay = isOwner || can("payroll.manage");
  const markAllowed = pay || can("attendance.mark");
  const [tab, setTab] = useState("attendance");
  if (!markAllowed) return <Card><EmptyState title="Not available" description="Needs payroll.manage or attendance.mark." /></Card>;
  return (
    <>
      <PageHeader title="Staff & salary" description="Employees, attendance (mark only the exceptions), monthly salaries with commission, PF, ESI, professional tax and TDS — posted to the books, with payslips." />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="attendance">Attendance</TabsTrigger>
          {pay && <TabsTrigger value="salary">Salaries</TabsTrigger>}
          {pay && <TabsTrigger value="employees">Employees</TabsTrigger>}
        </TabsList>
        <TabsContent value="attendance"><AttendanceGrid /></TabsContent>
        {pay && <TabsContent value="salary"><Salaries /></TabsContent>}
        {pay && <TabsContent value="employees"><Employees /></TabsContent>}
      </Tabs>
    </>
  );
}

// ---------------------------------------------------------------------------- attendance

function AttendanceGrid() {
  const { base } = useBusiness();
  const [month, setMonth] = useState(monthISO());
  const emps = useBizQuery<Employee[]>(["payroll", "employees"], "/payroll/employees");
  const att = useBizQuery<{ days: number; marks: Record<string, Record<string, Mark>> }>(["payroll", "attendance", month], "/payroll/attendance", { month });
  const [busy, setBusy] = useState<string | null>(null);
  const active = (emps.data ?? []).filter((e) => e.is_active || e.leaving_date);
  const days = att.data?.days ?? 0;
  const today = todayISO();
  async function cycle(e: Employee, day: string) {
    const cur = att.data?.marks[e.id]?.[day] ?? null;
    const next = MARKS[(MARKS.findIndex((m) => m.v === cur) + 1) % MARKS.length]!.v;
    setBusy(`${e.id}${day}`);
    try { await api(`${base}/payroll/attendance`, { method: "PUT", body: { day, entries: [{ employee_id: e.id, status: next }] } }); await att.refetch(); }
    catch (err) { toast.error(err instanceof ApiError ? err.message : "Could not mark"); }
    finally { setBusy(null); }
  }
  if (emps.error || att.error) return <Card><ErrorState error={emps.error ?? att.error} /></Card>;
  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 pb-3">
        <div><CardTitle>Attendance</CardTitle><p className="text-sm text-muted-foreground">Click a day to cycle: absent → half day → leave → holiday → present.</p></div>
        <Input type="month" className="w-44" value={month.slice(0, 7)} onChange={(e) => e.target.value && setMonth(`${e.target.value}-01`)} aria-label="Month" />
      </CardHeader>
      {emps.isLoading || att.isLoading ? <CardContent><Skeleton className="h-40" /></CardContent> : !active.length ? <EmptyState icon={UsersIcon} title="No employees yet" description="Add employees under the Employees tab." /> : (
        <div className="overflow-x-auto">
          <table className="text-xs">
            <thead><tr className="bg-muted/40"><th className="sticky left-0 z-10 min-w-40 bg-muted px-3 py-2 text-left">Employee</th>
              {Array.from({ length: days }, (_, i) => {
                const d = new Date(`${month.slice(0, 8)}${String(i + 1).padStart(2, "0")}T00:00:00`);
                return <th key={i} className={cn("w-8 px-0.5 py-2 text-center font-medium", d.getDay() === 0 && "text-destructive")}>{i + 1}<br /><span className="text-[10px] text-muted-foreground">{d.toLocaleDateString("en-IN", { weekday: "narrow" })}</span></th>;
              })}
              <th className="px-3 py-2 text-right">Paid days</th></tr></thead>
            <tbody className="divide-y">
              {active.map((e) => {
                let paid = 0;
                return (
                  <tr key={e.id}>
                    <td className="sticky left-0 z-10 bg-card px-3 py-1.5"><p className="font-medium">{e.name}</p><p className="text-muted-foreground">{e.designation ?? e.code}</p></td>
                    {Array.from({ length: days }, (_, i) => {
                      const day = `${month.slice(0, 8)}${String(i + 1).padStart(2, "0")}`;
                      const employed = day >= e.joining_date && (!e.leaving_date || day <= e.leaving_date);
                      const m = att.data?.marks[e.id]?.[day] ?? null;
                      const spec = MARKS.find((x) => x.v === m)!;
                      if (employed && day <= today) paid += m === "absent" ? 0 : m === "half_day" ? 0.5 : 1;
                      return <td key={i} className="p-0.5 text-center">
                        {employed ? <button type="button" disabled={busy === `${e.id}${day}` || day > today} title={`${date(day)}: ${spec.label}`} onClick={() => cycle(e, day)}
                          className={cn("size-7 rounded text-[11px] font-semibold transition-colors", m ? spec.cls : "text-muted-foreground/40 hover:bg-accent", day > today && "opacity-30")}>
                          {busy === `${e.id}${day}` ? "…" : m ? spec.short : "·"}</button> : <span className="text-muted-foreground/30">—</span>}
                      </td>;
                    })}
                    <td className="num px-3 text-right font-medium">{paid}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex flex-wrap gap-3 border-t px-4 py-2 text-xs text-muted-foreground">{MARKS.slice(1).map((m) => <span key={m.short} className="flex items-center gap-1"><span className={cn("rounded px-1.5 font-semibold", m.cls)}>{m.short}</span>{m.label}</span>)}<span>· = present</span></div>
    </Card>
  );
}

// ---------------------------------------------------------------------------- salaries

function Salaries() {
  const runs = useBizQuery<Run[]>(["payroll", "runs"], "/payroll/runs");
  const [month, setMonth] = useState(() => { const d = new Date(); d.setMonth(d.getMonth() - (d.getDate() < 25 ? 1 : 0)); return monthISO(d); });
  const [openId, setOpenId] = useState<string | null>(null);
  const create = useBizMutation<{ month: string }, Run>("POST", "/payroll/runs", { invalidate: [["payroll", "runs"]], success: "Salaries calculated", onSuccess: (r) => setOpenId(r.id) });
  if (runs.error) return <Card><ErrorState error={runs.error} /></Card>;
  const open = runs.data?.find((r) => r.id === openId);
  return (
    <div className="space-y-4">
      <Card><CardContent className="flex flex-wrap items-end gap-3">
        <Field label="Month" htmlFor="sal-month"><Input id="sal-month" type="month" className="w-44" value={month.slice(0, 7)} onChange={(e) => e.target.value && setMonth(`${e.target.value}-01`)} /></Field>
        <Button disabled={create.isPending} onClick={() => create.mutate({ month })}>{create.isPending ? <Loader2Icon className="animate-spin" /> : <PlusIcon />} Calculate salaries</Button>
        <p className="text-sm text-muted-foreground">Uses attendance, salary or daily wage, and each person’s commission on their own sales.</p>
      </CardContent></Card>
      {runs.isLoading ? <Skeleton className="h-32" /> : !runs.data?.length ? null : (
        <div className="grid gap-3 md:grid-cols-3">{runs.data.map((r) => (
          <button key={r.id} type="button" onClick={() => setOpenId(r.id)} className={cn("rounded-xl border bg-card p-4 text-left transition-colors hover:border-primary/40", openId === r.id && "border-primary")}>
            <div className="flex items-center justify-between"><p className="font-semibold">{monthLabel(r.month)}</p>
              <Badge variant={r.status === "paid" ? "default" : r.status === "posted" ? "blue" : "secondary"}>{r.status === "draft" ? "Draft" : r.status === "posted" ? "In books" : "Paid"}</Badge></div>
            <p className="num mt-1 text-xl font-semibold">{money(r.net)}</p><p className="text-xs text-muted-foreground">{r.lines.length} employees · gross {money(r.gross)}</p>
          </button>))}</div>
      )}
      {open && <RunTable key={open.id + open.status} run={open} onDeleted={() => setOpenId(null)} />}
    </div>
  );
}

function RunTable({ run, onDeleted }: { run: Run; onDeleted: () => void }) {
  const { base, id: bizId } = useBusiness();
  const qc = useQueryClient();
  const accounts = useBizQuery<{ id: string; code: string; name: string }[]>(["payroll", "pay-from"], "/payroll/pay-from");
  const [payFrom, setPayFrom] = useState("");
  const [confirmPost, setConfirmPost] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const post = useBizMutation("POST", `/payroll/runs/${run.id}/post`, { invalidate: [["payroll", "runs"]], success: "Salaries posted to the books" });
  const payNow = useBizMutation<{ paid_from_account_id: string }>("POST", `/payroll/runs/${run.id}/pay`, { invalidate: [["payroll", "runs"]], success: "Marked paid" });
  const del = useBizMutation("DELETE", `/payroll/runs/${run.id}`, { invalidate: [["payroll", "runs"]], success: "Draft deleted", onSuccess: onDeleted });
  const draft = run.status === "draft";
  async function edit(l: Line, patch: Partial<Record<"bonus" | "deductions", string>>) {
    try { await api(`${base}/payroll/runs/${run.id}/lines/${l.employee_id}`, { method: "PATCH", body: patch }); }
    catch (e) { toast.error(e instanceof ApiError ? e.message : "Could not save"); }
    finally { qc.invalidateQueries({ queryKey: ["biz", bizId, "payroll", "runs"] }); }
  }
  const src = payFrom || accounts.data?.[0]?.id || "";
  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 pb-3">
        <CardTitle>{monthLabel(run.month)}</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          {draft && <><Button variant="ghost" size="sm" onClick={() => setDeleting(true)}><Trash2Icon /> Delete draft</Button>
            <Button size="sm" onClick={() => setConfirmPost(true)}><SendIcon /> Post to books</Button></>}
          {run.status === "posted" && <><NativeSelect className="h-8 w-48" value={src} onChange={(e) => setPayFrom(e.target.value)} aria-label="Pay from">
            {accounts.data?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</NativeSelect>
            <Button size="sm" disabled={!src || payNow.isPending} onClick={() => payNow.mutate({ paid_from_account_id: src })}><BanknoteIcon /> Mark paid</Button></>}
        </div>
      </CardHeader>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-xs text-muted-foreground uppercase"><tr>
            <th className="px-4 py-2 text-left">Employee</th><th className="px-3 py-2 text-right">Paid days</th><th className="px-3 py-2 text-right">Pay</th>
            <th className="hidden px-3 py-2 text-right md:table-cell">Commission</th><th className="w-28 px-3 py-2 text-right">Bonus</th><th className="w-28 px-3 py-2 text-right">Other deductions</th><th className="px-3 py-2 text-right">PF / ESI</th><th className="hidden px-3 py-2 text-right md:table-cell">PT / TDS</th><th className="px-4 py-2 text-right">Net</th><th /></tr></thead>
          <tbody className="divide-y">{run.lines.map((l) => (
            <tr key={l.employee_id}>
              <td className="px-4 py-2"><p className="font-medium">{l.employee_name}</p><p className="text-xs text-muted-foreground">{l.pay_type === "daily" ? "Daily wage" : "Monthly"}{l.note ? ` · ${l.note}` : ""}</p></td>
              <td className="num px-3 text-right">{Number(l.paid_days)} / {Number(l.payable_days)}</td>
              <td className="num px-3 text-right">{money(l.base)}</td>
              <td className="num hidden px-3 text-right md:table-cell">{Number(l.commission) ? <span title={`on sales of ${money(l.sales)}`}>{money(l.commission)}</span> : "—"}</td>
              <td className="px-3">{draft ? <Input className="num h-8 text-right" inputMode="decimal" defaultValue={Number(l.bonus) || ""} placeholder="0" aria-label={`Bonus for ${l.employee_name}`}
                onBlur={(e) => e.target.value !== String(Number(l.bonus) || "") && edit(l, { bonus: e.target.value || "0" })} /> : <span className="num block text-right">{money(l.bonus)}</span>}</td>
              <td className="px-3">{draft ? <Input className="num h-8 text-right" inputMode="decimal" defaultValue={Number(l.deductions) || ""} placeholder="0" aria-label={`Deductions for ${l.employee_name}`}
                onBlur={(e) => e.target.value !== String(Number(l.deductions) || "") && edit(l, { deductions: e.target.value || "0" })} /> : <span className="num block text-right">{money(l.deductions)}</span>}</td>
              <td className="num px-3 text-right text-xs">{Number(l.pf_employee ?? 0) || Number(l.esi_employee ?? 0) ? <>{money(l.pf_employee ?? "0")}<span className="block text-muted-foreground">{money(l.esi_employee ?? "0")}</span></> : "—"}</td>
              <td className="num hidden px-3 text-right text-xs md:table-cell">{Number(l.pt ?? 0) || Number(l.tds ?? 0) ? <>{money(l.pt ?? "0")}<span className="block text-muted-foreground">{money(l.tds ?? "0")}</span></> : "—"}</td>
              <td className="num px-4 text-right font-semibold">{money(l.net)}</td>
              <td className="px-2"><Button variant="ghost" size="sm" onClick={() => openFile(`${base}/payroll/runs/${run.id}/payslips/${l.employee_id}`)}>Payslip</Button></td>
            </tr>))}</tbody>
          <tfoot className="border-t-2 font-semibold"><tr><td className="px-4 py-2" colSpan={4}>Total</td><td /><td className="num px-3 text-right" colSpan={3}>{money(run.deductions)}</td><td className="num px-4 text-right">{money(run.net)}</td><td /></tr></tfoot>
        </table>
      </div>
      <StatutoryBox runId={run.id} month={run.month} />
      <ConfirmDialog open={confirmPost} onOpenChange={setConfirmPost} confirm="Post" title={`Post ${monthLabel(run.month)} salaries?`}
        description="Salaries & Wages is charged and Salaries Payable owed; the run can then no longer be edited." onConfirm={() => post.mutateAsync(undefined)} />
      <ConfirmDialog open={deleting} onOpenChange={setDeleting} destructive confirm="Delete" title="Delete this draft?" onConfirm={() => del.mutateAsync(undefined)} />
    </Card>
  );
}

// ---------------------------------------------------------------------------- employees

const EMPTY: EmployeeDraft = { code: "", name: "", designation: "", phone: "", email: "", branch_id: null, joining_date: todayISO(), leaving_date: null,
  pay_type: "monthly", monthly_salary: "", daily_wage: "", commission_percent: "0", user_id: null, pan: "", bank_account: "", ifsc: "", is_active: true, notes: "" };

function Employees() {
  const emps = useBizQuery<Employee[]>(["payroll", "employees"], "/payroll/employees");
  const [editing, setEditing] = useState<{ id?: string; draft: EmployeeDraft; key: string } | null>(null);
  if (emps.error) return <Card><ErrorState error={emps.error} /></Card>;
  return (
    <div className="space-y-4">
      <div className="flex justify-end"><Button onClick={() => setEditing({ draft: { ...EMPTY, code: `E${String((emps.data?.length ?? 0) + 1).padStart(3, "0")}` }, key: `n${Date.now()}` })}><UserPlusIcon /> Add employee</Button></div>
      {emps.isLoading ? <Skeleton className="h-40" /> : !emps.data?.length ? <Card><EmptyState icon={UsersIcon} title="No employees" description="Add your team with their salary or daily wage. Link a salesperson to their login to pay commission on their sales." /></Card> : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{emps.data.map((e) => (
          <Card key={e.id} className={e.is_active ? "" : "opacity-70"}><CardContent className="space-y-1">
            <div className="flex items-start justify-between"><div><p className="font-semibold">{e.name}</p><p className="text-xs text-muted-foreground">{e.code}{e.designation ? ` · ${e.designation}` : ""}</p></div>
              <Button size="icon" variant="ghost" aria-label={`Edit ${e.name}`} onClick={() => setEditing({ id: e.id, key: `${e.id}${Date.now()}`, draft: { ...e,
                monthly_salary: String(Number(e.monthly_salary) || ""), daily_wage: String(Number(e.daily_wage) || ""), commission_percent: String(Number(e.commission_percent)) } })}><PencilIcon /></Button></div>
            <p className="text-sm">{e.pay_type === "monthly" ? `${money(e.monthly_salary)} a month` : `${money(e.daily_wage)} a day`}{Number(e.commission_percent) ? ` + ${Number(e.commission_percent)} % commission` : ""}</p>
            <p className="text-xs text-muted-foreground">Since {date(e.joining_date)}{e.leaving_date ? ` · left ${date(e.leaving_date)}` : ""}</p>
          </CardContent></Card>))}</div>
      )}
      {editing && <EmployeeDialog key={editing.key} id={editing.id} initial={editing.draft} onClose={() => setEditing(null)} />}
    </div>
  );
}

function EmployeeDialog({ id, initial, onClose }: { id?: string; initial: EmployeeDraft; onClose: () => void }) {
  const [d, setD] = useState(initial);
  const members = useBizQuery<{ items: { user: { id: string; full_name: string } }[] }>(["members"], "/members", { limit: 200 });
  const set = (p: Partial<EmployeeDraft>) => setD((x) => ({ ...x, ...p }));
  const save = useBizMutation<EmployeeDraft>(id ? "PUT" : "POST", id ? `/payroll/employees/${id}` : "/payroll/employees",
    { invalidate: [["payroll"]], success: "Employee saved", onSuccess: onClose });
  const nn = (v: string | null) => (v ? v : null);
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide className="max-h-[92vh] overflow-y-auto">
        <DialogHeader><DialogTitle>{id ? "Edit employee" : "Add employee"}</DialogTitle><DialogDescription>Bank and PAN details are visible only to people who manage payroll.</DialogDescription></DialogHeader>
        <form className="grid gap-4 sm:grid-cols-3" onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ ...d, designation: nn(d.designation), phone: nn(d.phone), email: nn(d.email), leaving_date: nn(d.leaving_date), pan: nn(d.pan),
            bank_account: nn(d.bank_account), ifsc: nn(d.ifsc), notes: nn(d.notes), monthly_salary: d.monthly_salary || "0", daily_wage: d.daily_wage || "0",
            uan: nn(d.uan ?? null), esi_number: nn(d.esi_number ?? null), basic_percent: d.basic_percent || "100" });
        }}>
          <Field label="Name" htmlFor="em-name" className="sm:col-span-2"><Input id="em-name" value={d.name} onChange={(e) => set({ name: e.target.value })} /></Field>
          <Field label="Code" htmlFor="em-code"><Input id="em-code" value={d.code} onChange={(e) => set({ code: e.target.value })} /></Field>
          <Field label="Designation" htmlFor="em-des"><Input id="em-des" value={d.designation ?? ""} onChange={(e) => set({ designation: e.target.value })} placeholder="Pharmacist" /></Field>
          <Field label="Phone" htmlFor="em-ph"><Input id="em-ph" inputMode="tel" value={d.phone ?? ""} onChange={(e) => set({ phone: e.target.value })} /></Field>
          <Field label="Joined" htmlFor="em-join"><Input id="em-join" type="date" value={d.joining_date} onChange={(e) => set({ joining_date: e.target.value })} /></Field>
          <Field label="Pay" htmlFor="em-type"><NativeSelect id="em-type" value={d.pay_type} onChange={(e) => set({ pay_type: e.target.value as Employee["pay_type"] })}>
            <option value="monthly">Monthly salary</option><option value="daily">Daily wage</option></NativeSelect></Field>
          {d.pay_type === "monthly"
            ? <Field label="Monthly salary (₹)" htmlFor="em-sal"><Input id="em-sal" inputMode="decimal" value={d.monthly_salary} onChange={(e) => set({ monthly_salary: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
            : <Field label="Daily wage (₹)" htmlFor="em-wage"><Input id="em-wage" inputMode="decimal" value={d.daily_wage} onChange={(e) => set({ daily_wage: e.target.value.replace(/[^\d.]/g, "") })} /></Field>}
          <Field label="Commission on own sales %" htmlFor="em-comm"><Input id="em-comm" inputMode="decimal" value={d.commission_percent} onChange={(e) => set({ commission_percent: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
          <Field label="Their ERP login (salesperson)" htmlFor="em-user" className="sm:col-span-2"><NativeSelect id="em-user" value={d.user_id ?? ""} onChange={(e) => set({ user_id: e.target.value || null })}>
            <option value="">— none —</option>{members.data?.items.map((m) => <option key={m.user.id} value={m.user.id}>{m.user.full_name}</option>)}</NativeSelect></Field>
          <Field label="Left on" htmlFor="em-left"><Input id="em-left" type="date" value={d.leaving_date ?? ""} onChange={(e) => set({ leaving_date: e.target.value || null })} /></Field>
          <Field label="Bank account" htmlFor="em-bank"><Input id="em-bank" inputMode="numeric" value={d.bank_account ?? ""} onChange={(e) => set({ bank_account: e.target.value.replace(/\D/g, "") })} /></Field>
          <Field label="IFSC" htmlFor="em-ifsc"><Input id="em-ifsc" className="uppercase" value={d.ifsc ?? ""} onChange={(e) => set({ ifsc: e.target.value })} /></Field>
          <Field label="PAN" htmlFor="em-pan"><Input id="em-pan" className="uppercase" value={d.pan ?? ""} onChange={(e) => set({ pan: e.target.value })} /></Field>
          <fieldset className="grid gap-3 rounded-lg border p-3 sm:col-span-3 sm:grid-cols-3">
            <legend className="px-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Statutory deductions</legend>
            <label className="flex items-center gap-2 text-sm"><Switch checked={!!d.pf_enabled} onCheckedChange={(v) => set({ pf_enabled: v })} /> Provident fund (12%)</label>
            {d.pf_enabled && <>
              <Field label="UAN" htmlFor="em-uan"><Input id="em-uan" inputMode="numeric" maxLength={12} value={d.uan ?? ""} onChange={(e) => set({ uan: e.target.value.replace(/\D/g, "") })} /></Field>
              <Field label="Basic + DA % of salary" htmlFor="em-basic" hint="PF is on this part"><Input id="em-basic" inputMode="decimal" value={d.basic_percent ?? "100"} onChange={(e) => set({ basic_percent: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
              <label className="flex items-center gap-2 text-sm sm:col-span-3"><Switch checked={d.pf_wage_cap !== false} onCheckedChange={(v) => set({ pf_wage_cap: v })} /> PF only on the first ₹15,000 of wages</label>
            </>}
            <label className="flex items-center gap-2 text-sm"><Switch checked={!!d.esi_enabled} onCheckedChange={(v) => set({ esi_enabled: v })} /> ESI (wages up to ₹21,000)</label>
            {d.esi_enabled && <Field label="ESI IP number" htmlFor="em-esi"><Input id="em-esi" inputMode="numeric" value={d.esi_number ?? ""} onChange={(e) => set({ esi_number: e.target.value.replace(/\D/g, "") })} /></Field>}
            <Field label="Professional tax state" htmlFor="em-pt"><NativeSelect id="em-pt" value={d.pt_state ?? ""} onChange={(e) => set({ pt_state: e.target.value || null })}>
              <option value="">None</option><option value="24">Gujarat</option><option value="27">Maharashtra</option><option value="29">Karnataka</option>
              <option value="19">West Bengal</option><option value="36">Telangana</option><option value="37">Andhra Pradesh</option></NativeSelect></Field>
            <label className="flex items-center gap-2 text-sm"><Switch checked={!!d.tds_enabled} onCheckedChange={(v) => set({ tds_enabled: v })} /> TDS on salary (new regime)</label>
          </fieldset>
          <DialogFooter className="items-center sm:col-span-3 sm:justify-between">
            <label className="flex items-center gap-2 text-sm"><Switch checked={d.is_active} onCheckedChange={(v) => set({ is_active: v })} /> Active</label>
            <div className="flex gap-2"><Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={!d.name.trim() || !d.code.trim() || save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />}Save</Button></div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}


interface Statutory { pf: { rows: unknown[]; employee: string; eps: string; epf_employer: string; due_date: string }; esi: { rows: unknown[]; employee: string; employer: string; due_date: string };
  pt: { total: string }; tds: { total: string; due_date: string } }

/** What to deposit for the month: PF (ECR file), ESI, professional tax and TDS. */
function StatutoryBox({ runId, month }: { runId: string; month: string }) {
  const { base } = useBusiness();
  const q = useBizQuery<Statutory>(["payroll", "statutory", runId], `/payroll/runs/${runId}/statutory`);
  const s = q.data;
  if (!s || (!s.pf.rows.length && !s.esi.rows.length && !Number(s.pt.total) && !Number(s.tds.total))) return null;
  const pf = Number(s.pf.employee) + Number(s.pf.eps) + Number(s.pf.epf_employer);
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t bg-muted/30 px-4 py-3 text-sm">
      {s.pf.rows.length > 0 && <span>PF <b className="num">{money(String(pf))}</b> <span className="text-xs text-muted-foreground">by {date(s.pf.due_date)}</span>
        <Button variant="link" size="sm" className="h-auto px-1" onClick={() => openFile(`${base}/payroll/runs/${runId}/ecr.txt`, undefined, `ECR-${month.slice(0, 7)}.txt`)}>ECR file</Button></span>}
      {s.esi.rows.length > 0 && <span>ESI <b className="num">{money(String(Number(s.esi.employee) + Number(s.esi.employer)))}</b> <span className="text-xs text-muted-foreground">by {date(s.esi.due_date)}</span></span>}
      {Number(s.pt.total) > 0 && <span>Professional tax <b className="num">{money(s.pt.total)}</b></span>}
      {Number(s.tds.total) > 0 && <span>TDS on salary <b className="num">{money(s.tds.total)}</b> <span className="text-xs text-muted-foreground">by {date(s.tds.due_date)}</span></span>}
    </div>
  );
}
