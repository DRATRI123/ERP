"use client";

import { DownloadIcon, Landmark, Loader2Icon, PlusIcon } from "lucide-react";
import { useState } from "react";

import { StaticTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { PeriodPicker, PRESETS } from "@/components/pickers/period";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { downloadCsv } from "@/lib/csv";
import { date, money, todayISO } from "@/lib/format";

interface Row { payment_id: string; number: string; date: string; party_name: string | null; pan: string | null; section: string | null; gross: string; tds: string; net: string; rate: string }
interface Month { month: string; deducted: string; deposited: string; balance: string; due_date: string; overdue: boolean }
interface Register { rows: Row[]; sections: { section: string; count: number; gross: string; tds: string }[]; total_tds: string; months?: Month[] }

export default function TdsPage() {
  const { can, isOwner } = useBusiness();
  const [dir, setDir] = useState<"out" | "in">("out");
  const [period, setPeriod] = useState(PRESETS.this_fy!());
  const reg = useBizQuery<Register>(["tds-register"], "/reports/tds", { direction: dir, date_from: period.from, date_to: period.to });
  const [deposit, setDeposit] = useState<Month | null>(null);
  const d = reg.data;
  const due = (d?.months ?? []).reduce((s, m) => s + Math.max(0, Number(m.balance)), 0);
  const overdue = (d?.months ?? []).filter((m) => m.overdue).length;
  const canDeposit = isOwner || (can("payments.create") && can("accounting.post"));

  function exportCsv() {
    if (!d) return;
    downloadCsv(`tds-${dir}-${period.from}_${period.to}.csv`, ["Date", "Voucher", "Party", "PAN", "Section", "Gross", "Rate %", "TDS", "Net paid"],
      d.rows.map((r) => [r.date, r.number, r.party_name, r.pan, r.section, r.gross, r.rate, r.tds, r.net]));
  }
  return (
    <>
      <PageHeader title="TDS" description="TDS you deduct on payments (deposit it by the 7th of next month; March by 30 April) and TDS customers deduct from you (claim it in your return)."
      />
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <Tabs value={dir} onValueChange={(v) => setDir(v as "out" | "in")}><TabsList>
          <TabsTrigger value="out">Deducted by us</TabsTrigger><TabsTrigger value="in">Deducted by customers</TabsTrigger>
        </TabsList></Tabs>
        <div className="lg:w-auto"><PeriodPicker value={period} onChange={setPeriod} /></div>
      </div>
      <div className="mb-4 grid grid-cols-2 gap-3 sm:mb-6 sm:gap-4 lg:grid-cols-3">
        <StatCard label={dir === "out" ? "TDS deducted" : "TDS receivable"} icon={Landmark} loading={reg.isLoading} value={d ? money(d.total_tds) : "—"} sub={d ? `${d.rows.length} ${d.rows.length === 1 ? "entry" : "entries"}` : undefined} />
        {dir === "out" && <StatCard label="To deposit" icon={Landmark} tone={due ? (overdue ? "red" : "amber") : "green"} loading={reg.isLoading} value={money(due.toFixed(2))}
          sub={overdue ? `${overdue} month${overdue === 1 ? "" : "s"} overdue — interest applies` : "nothing overdue"} />}
      </div>
      {reg.error ? <Card><ErrorState error={reg.error} retry={() => reg.refetch()} /></Card> : reg.isLoading ? <Card><TableSkeleton /></Card> : (
        <div className="space-y-4">
          {dir === "out" && (d?.months?.length ?? 0) > 0 && (
            <Card className="overflow-hidden"><CardHeader><CardTitle>Month by month</CardTitle></CardHeader>
              <StaticTable rows={d!.months!} rowKey={(m) => m.month} columns={[
                { key: "m", header: "Month", mobile: "title", cell: (m) => new Date(m.month).toLocaleDateString("en-IN", { month: "long", year: "numeric" }) },
                { key: "ded", header: "Deducted", align: "right", cell: (m) => <span className="num">{money(m.deducted)}</span> },
                { key: "dep", header: "Deposited", align: "right", cell: (m) => <span className="num">{money(m.deposited)}</span> },
                { key: "due", header: "Due by", cell: (m) => <span>{date(m.due_date)} {Number(m.balance) > 0 ? <Badge variant={m.overdue ? "destructive" : "warning"}>{m.overdue ? "Overdue" : "Due"}</Badge> : <Badge>Deposited</Badge>}</span> },
                { key: "bal", header: "Balance", align: "right", mobile: "aside", cell: (m) => <span className="num font-medium">{money(m.balance)}</span> },
                ...(canDeposit ? [{ key: "x", header: "", mobile: "row" as const, cell: (m: Month) => Number(m.balance) > 0 &&
                  <Button size="sm" variant="outline" onClick={() => setDeposit(m)}><PlusIcon /> Record challan</Button> }] : []),
              ]} />
            </Card>
          )}
          {(d?.sections.length ?? 0) > 0 && (
            <Card><CardHeader><CardTitle>By section</CardTitle></CardHeader><CardContent className="flex flex-wrap gap-2">
              {d!.sections.map((s) => <Badge key={s.section} variant="outline" className="h-auto py-1.5 text-sm">{s.section} · {s.count} · <b className="num ml-1">{money(s.tds)}</b></Badge>)}
            </CardContent></Card>
          )}
          <Card className="overflow-hidden">
            <div className="flex items-center justify-between border-b px-4 py-3"><h2 className="font-semibold">Entries</h2>
              {d?.rows.length ? <Button size="sm" variant="outline" onClick={exportCsv}><DownloadIcon /> CSV for the return</Button> : null}</div>
            {!d?.rows.length ? <EmptyState title="No TDS in this period" description={dir === "out" ? "When recording a payment to a supplier, tick “Deduct TDS” and choose the section." : "When a customer pays less because they deducted TDS, tick “Customer deducted TDS” while recording the receipt."} /> : (
              <StaticTable rows={d.rows} rowKey={(r) => r.payment_id} columns={[
                { key: "p", header: "Party", mobile: "title", cell: (r) => <div><p className="font-medium">{r.party_name}</p><p className="text-xs text-muted-foreground">{r.number} · PAN {r.pan ?? <span className="text-destructive">missing (20%)</span>}</p></div> },
                { key: "d", header: "Date", cell: (r) => date(r.date) },
                { key: "s", header: "Section", cell: (r) => <Badge variant="outline">{r.section}</Badge> },
                { key: "g", header: "Gross", align: "right", cell: (r) => <span className="num">{money(r.gross)}</span> },
                { key: "r", header: "Rate", align: "right", cell: (r) => <span className="num">{Number(r.rate)}%</span> },
                { key: "t", header: "TDS", align: "right", mobile: "aside", cell: (r) => <span className="num font-medium">{money(r.tds)}</span> },
              ]} />
            )}
          </Card>
        </div>
      )}
      {deposit && <DepositDialog month={deposit} onClose={() => setDeposit(null)} />}
    </>
  );
}

function DepositDialog({ month, onClose }: { month: Month; onClose: () => void }) {
  const banks = useBizQuery<{ id: string; code: string; name: string; subtype: string }[]>(["accounts"], "/accounting/accounts");
  const [f, setF] = useState({ amount: Number(month.balance).toFixed(2), deposit_date: todayISO(), challan_no: "", bsr_code: "", account_id: "" });
  const save = useBizMutation<Record<string, unknown>>("POST", "/tds/deposits", { invalidate: [["tds-register"]], success: "Challan recorded", onSuccess: onClose });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF((x) => ({ ...x, [k]: e.target.value }));
  const bankList = banks.data?.filter((a) => a.subtype === "bank") ?? [];
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>TDS challan for {new Date(month.month).toLocaleDateString("en-IN", { month: "long", year: "numeric" })}</DialogTitle>
          <DialogDescription>After paying on the income-tax portal (ITNS 281), enter the challan here. The books get Dr TDS Payable, Cr Bank.</DialogDescription></DialogHeader>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate({ month: month.month, ...f, challan_no: f.challan_no || null, bsr_code: f.bsr_code || null, account_id: f.account_id || bankList[0]?.id }); }}>
          <Field label="Amount" htmlFor="dp-amt"><Input id="dp-amt" inputMode="decimal" className="num" value={f.amount} onChange={set("amount")} /></Field>
          <Field label="Paid on" htmlFor="dp-date"><Input id="dp-date" type="date" value={f.deposit_date} onChange={set("deposit_date")} /></Field>
          <Field label="Challan serial no." htmlFor="dp-ch"><Input id="dp-ch" value={f.challan_no} onChange={set("challan_no")} /></Field>
          <Field label="BSR code" htmlFor="dp-bsr" hint="7 digits"><Input id="dp-bsr" inputMode="numeric" value={f.bsr_code} onChange={set("bsr_code")} /></Field>
          <Field label="Paid from" htmlFor="dp-acc" className="sm:col-span-2"><NativeSelect id="dp-acc" value={f.account_id} onChange={set("account_id")}>
            {bankList.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</NativeSelect></Field>
          <DialogFooter className="sm:col-span-2">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!(Number(f.amount) > 0) || save.isPending || !bankList.length}>{save.isPending && <Loader2Icon className="animate-spin" />} Record challan</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
