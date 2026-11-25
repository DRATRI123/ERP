"use client";

import { DownloadIcon, MailIcon, PrinterIcon } from "lucide-react";
import { useState } from "react";

import { MoneyTable, type MCol } from "@/components/data/money-table";
import Link from "next/link";

import { PageHeader } from "@/components/data/page-header";
import { ErrorState, TableSkeleton } from "@/components/data/states";
import { PeriodPicker, PRESETS } from "@/components/pickers/period";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useBizQuery } from "@/hooks/use-business";
import { useModules } from "@/hooks/use-modules";
import { downloadCsv } from "@/lib/csv";
import { date, money, todayISO } from "@/lib/format";
import { cn } from "@/lib/utils";

type R = Record<string, unknown>;
type Row = { code?: string; name: string; amount: string };
interface PL { revenue: Row[]; total_revenue: string; cost_of_goods_sold: Row[]; total_cost_of_goods_sold: string; gross_profit: string;
  gross_margin_percent: string | null; other_income: Row[]; total_other_income: string; operating_expenses: Row[]; depreciation: Row[];
  finance_costs: Row[]; total_expenses: string; net_profit: string; [k: string]: unknown }
interface BS { assets: Record<string, Row[]>; total_assets: string; liabilities: Record<string, Row[]>; total_liabilities: string;
  equity: Row[]; profit_not_yet_closed: string; total_equity: string; balanced: boolean }
interface CF { opening_cash: string; closing_cash: string; operating: { item: string; amount: string }[]; investing: { item: string; amount: string }[];
  financing: { item: string; amount: string }[]; net_operating: string; net_investing: string; net_financing: string }
const REPORTS = [
  { id: "pl", label: "Profit & loss", group: "Financial statements" }, { id: "bs", label: "Balance sheet", group: "Financial statements" },
  { id: "cf", label: "Cash flow", group: "Financial statements" }, { id: "tb", label: "Trial balance", group: "Books" },
  { id: "db", label: "Day book", group: "Books" }, { id: "ar", label: "Receivables aging", group: "Parties" },
  { id: "ap", label: "Payables aging", group: "Parties" }, { id: "sv", label: "Stock valuation", group: "Inventory" },
  { id: "rx", label: "Schedule H1 / X register", group: "Pharmacy" }, { id: "ds", label: "Doctor-wise sales", group: "Pharmacy" },
] as const;
type Id = (typeof REPORTS)[number]["id"];

function Statement({ sections }: { sections: { title: string; rows: Row[]; total?: string; strong?: boolean }[] }) {
  return (
    <div className="divide-y">
      {sections.map((s) => (
        <div key={s.title} className="px-5 py-3">
          <div className={cn("flex justify-between py-1", s.strong ? "text-base font-semibold" : "text-sm font-semibold")}>
            <span>{s.title}</span>{s.total !== undefined && <span className="num">{money(s.total)}</span>}
          </div>
          {s.rows.map((r, i) => (
            <div key={i} className="flex justify-between py-1 pl-4 text-sm text-muted-foreground">
              <span>{r.code && <span className="mr-2 font-mono text-xs">{r.code}</span>}{r.name}</span><span className="num">{money(r.amount)}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

export default function ReportsPage() {
  const [report, setReport] = useState<Id>("pl");
  const pharmacy = useModules().on("pharmacy");
  const [period, setPeriod] = useState(PRESETS.this_fy!());
  const [asOf, setAsOf] = useState(todayISO());
  const ranged = { date_from: period.from, date_to: period.to };
  const pl = useBizQuery<PL>(["rep-pl"], "/accounting/reports/profit-and-loss", ranged, { enabled: report === "pl" });
  const bs = useBizQuery<BS>(["rep-bs"], "/accounting/reports/balance-sheet", { as_of: asOf }, { enabled: report === "bs" });
  const cf = useBizQuery<CF>(["rep-cf"], "/accounting/reports/cash-flow", ranged, { enabled: report === "cf" });
  const tb = useBizQuery<{ rows: R[]; balanced: boolean; totals: R }>(["rep-tb"], "/accounting/reports/trial-balance", ranged, { enabled: report === "tb" });
  const dbk = useBizQuery<{ number: string; date: string; source_ref: string | null; narration: string; lines: { account: string; debit: string; credit: string }[] }[]>(
    ["rep-db"], "/accounting/reports/day-book", { date_from: period.to.slice(0, 8) + "01" > period.from ? period.to.slice(0, 8) + "01" : period.from, date_to: period.to }, { enabled: report === "db" });
  const ar = useBizQuery<{ parties: R[] }>(["rep-ar"], "/accounting/reports/aging", { side: "receivable", as_of: asOf }, { enabled: report === "ar" });
  const ap = useBizQuery<{ parties: R[] }>(["rep-ap"], "/accounting/reports/aging", { side: "payable", as_of: asOf }, { enabled: report === "ap" });
  const sv = useBizQuery<{ method: string; total_value: string; warehouses: R[] }>(["rep-sv"], "/inventory/valuation", undefined, { enabled: report === "sv" });
  const rx = useBizQuery<{ rows: R[] }>(["rep-rx"], "/reports/schedule-register", ranged, { enabled: report === "rx" });
  const ds = useBizQuery<{ rows: R[] }>(["rep-ds"], "/reports/doctor-sales", ranged, { enabled: report === "ds" });
  const active = { pl, bs, cf, tb, db: dbk, ar, ap, sv, rx, ds }[report];
  const rxCols: MCol[] = [{ key: "date", header: "Date" }, { key: "number", header: "Bill no." }, { key: "patient_name", header: "Patient" },
    { key: "patient_address", header: "Patient address" }, { key: "doctor_name", header: "Doctor" }, { key: "doctor_registration", header: "Reg. no." },
    { key: "rx_number", header: "Rx no." }, { key: "product_name", header: "Drug" }, { key: "schedule_label", header: "Schedule" },
    { key: "batch_no", header: "Batch" }, { key: "quantity", header: "Qty", align: "right" }];
  const dsCols: MCol[] = [{ key: "doctor_name", header: "Doctor" }, { key: "clinic", header: "Clinic" }, { key: "bills", header: "Bills", align: "right" },
    { key: "taxable", header: "Taxable value", money: true }, { key: "total", header: "Bill value", money: true }];
  const usesAsOf = report === "bs" || report === "ar" || report === "ap";

  const agingCols: MCol[] = [{ key: "name", header: "Party" }, { key: "not_due", header: "Not due", money: true }, { key: "1_30", header: "1–30", money: true },
    { key: "31_60", header: "31–60", money: true }, { key: "61_90", header: "61–90", money: true }, { key: "over_90", header: "90+", money: true }, { key: "net", header: "Net due", money: true }];
  const tbCols: MCol[] = [{ key: "code", header: "Code" }, { key: "name", header: "Account" }, { key: "opening_debit", header: "Opening Dr", money: true }, { key: "opening_credit", header: "Opening Cr", money: true },
    { key: "debit", header: "Debit", money: true }, { key: "credit", header: "Credit", money: true }, { key: "closing_debit", header: "Closing Dr", money: true }, { key: "closing_credit", header: "Closing Cr", money: true }];

  function exportCsv() {
    const name = `${report}-${usesAsOf ? asOf : `${period.from}_${period.to}`}.csv`;
    if (report === "tb" && tb.data) downloadCsv(name, tbCols.map((c) => c.header), tb.data.rows.map((r) => tbCols.map((c) => r[c.key] as string)));
    else if ((report === "ar" || report === "ap") && (report === "ar" ? ar : ap).data) downloadCsv(name, agingCols.map((c) => c.header), (report === "ar" ? ar : ap).data!.parties.map((r) => agingCols.map((c) => r[c.key] as string)));
    else if (report === "db" && dbk.data) downloadCsv(name, ["Number", "Date", "Reference", "Account", "Debit", "Credit"], dbk.data.flatMap((e) => e.lines.map((l) => [e.number, e.date, e.source_ref, l.account, l.debit, l.credit])));
    else if (report === "rx" && rx.data) downloadCsv(name, rxCols.map((c) => c.header), rx.data.rows.map((r) => rxCols.map((c) => r[c.key] as string)));
    else if (report === "ds" && ds.data) downloadCsv(name, dsCols.map((c) => c.header), ds.data.rows.map((r) => dsCols.map((c) => r[c.key] as string)));
    else if (report === "pl" && pl.data) {
      const keys = ["revenue", "cost_of_goods_sold", "other_income", "operating_expenses", "depreciation", "finance_costs"];
      downloadCsv(name, ["Section", "Code", "Account", "Amount"], keys.flatMap((k) => ((pl.data![k] as Row[] | undefined) ?? []).map((r) => [k, r.code, r.name, r.amount])));
    }
  }

  let body: React.ReactNode = null;
  if (active.isLoading) body = <TableSkeleton />;
  else if (active.error) body = <ErrorState error={active.error} retry={() => active.refetch()} />;
  else if (report === "pl" && pl.data) {
    const d = pl.data;
    body = <Statement sections={[
      { title: "Revenue", rows: d.revenue, total: d.total_revenue }, { title: "Cost of goods sold", rows: d.cost_of_goods_sold, total: d.total_cost_of_goods_sold },
      { title: `Gross profit${d.gross_margin_percent ? ` (${d.gross_margin_percent}%)` : ""}`, rows: [], total: d.gross_profit, strong: true },
      { title: "Other income", rows: d.other_income, total: d.total_other_income },
      { title: "Expenses", rows: [...d.operating_expenses, ...d.depreciation, ...d.finance_costs], total: d.total_expenses },
      { title: "Net profit", rows: [], total: d.net_profit, strong: true }]} />;
  } else if (report === "bs" && bs.data) {
    const d = bs.data;
    const equity = Number(d.profit_not_yet_closed) ? [...d.equity, { name: "Profit not yet closed to equity", amount: d.profit_not_yet_closed }] : d.equity;
    body = <>
      <div className="flex justify-end px-5 pt-3"><Badge variant={d.balanced ? "default" : "destructive"}>{d.balanced ? "Balanced" : "Not balanced"}</Badge></div>
      <Statement sections={[{ title: "Assets", rows: Object.values(d.assets).flat(), total: d.total_assets, strong: true },
        { title: "Liabilities", rows: Object.values(d.liabilities).flat(), total: d.total_liabilities },
        { title: "Equity", rows: equity, total: d.total_equity }]} /></>;
  } else if (report === "cf" && cf.data) {
    const d = cf.data;
    const map = (rows: { item: string; amount: string }[]) => rows.map((r) => ({ name: r.item, amount: r.amount }));
    body = <Statement sections={[{ title: "Opening cash & bank", rows: [], total: d.opening_cash },
      { title: "Operating activities", rows: map(d.operating), total: d.net_operating }, { title: "Investing activities", rows: map(d.investing), total: d.net_investing },
      { title: "Financing activities", rows: map(d.financing), total: d.net_financing }, { title: "Closing cash & bank", rows: [], total: d.closing_cash, strong: true }]} />;
  } else if (report === "tb" && tb.data) {
    body = <><div className="flex justify-end px-4 pt-3"><Badge variant={tb.data.balanced ? "default" : "destructive"}>{tb.data.balanced ? "Debits = credits" : "Out of balance"}</Badge></div><MoneyTable rows={tb.data.rows} cols={tbCols} /></>;
  } else if (report === "db" && dbk.data) {
    body = dbk.data.length === 0 ? <p className="p-8 text-center text-sm text-muted-foreground">No entries.</p> : (
      <div className="divide-y">{dbk.data.map((e) => (
        <div key={e.number} className="px-5 py-3 text-sm">
          <div className="flex justify-between font-medium"><span>{e.number} · {e.source_ref ?? e.narration}</span><span className="text-muted-foreground">{date(e.date)}</span></div>
          {e.lines.map((l, i) => <div key={i} className="grid grid-cols-[1fr_110px_110px] pl-4 text-muted-foreground"><span>{l.account}</span><span className="num text-right">{Number(l.debit) ? money(l.debit) : ""}</span><span className="num text-right">{Number(l.credit) ? money(l.credit) : ""}</span></div>)}
        </div>))}</div>);
  } else if ((report === "ar" || report === "ap") && (report === "ar" ? ar : ap).data) {
    body = <MoneyTable rows={(report === "ar" ? ar : ap).data!.parties} cols={agingCols} />;
  } else if (report === "sv" && sv.data) {
    body = <MoneyTable rows={sv.data.warehouses} cols={[{ key: "code", header: "Warehouse" }, { key: "name", header: "Name" }, { key: "stock_lines", header: "Stock lines", align: "right" }, { key: "value", header: "Value", money: true }]} />;
  } else if (report === "rx" && rx.data) {
    body = rx.data.rows.length === 0 ? <p className="p-8 text-center text-sm text-muted-foreground">No Schedule H1, X or narcotic sales in this period.</p> : <MoneyTable rows={rx.data.rows} cols={rxCols} />;
  } else if (report === "ds" && ds.data) {
    body = ds.data.rows.length === 0 ? <p className="p-8 text-center text-sm text-muted-foreground">No bills with a doctor in this period. Choose the doctor on a bill to see it here.</p> : <MoneyTable rows={ds.data.rows} cols={dsCols} />;
  }

  const shown = REPORTS.filter((r) => pharmacy || r.group !== "Pharmacy");
  const groups = [...new Set(shown.map((r) => r.group))];
  return (
    <>
      <PageHeader title="Reports" description="Statements and registers, straight from the books."
        actions={<Link href="/reports/scheduled" className={buttonVariants({ variant: "outline" })}><MailIcon /> Scheduled e-mails</Link>} />
      <div className="grid gap-5 lg:grid-cols-[220px_1fr]">
        <nav className="space-y-4" aria-label="Reports">
          {groups.map((g) => (
            <div key={g}><p className="px-2 pb-1 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">{g}</p>
              {shown.filter((r) => r.group === g).map((r) => (
                <button key={r.id} type="button" onClick={() => setReport(r.id)}
                  className={cn("block w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent", report === r.id && "bg-sidebar-active font-medium text-primary")}>{r.label}</button>
              ))}</div>
          ))}
        </nav>
        <Card className="min-w-0 overflow-hidden">
          <div className="no-print flex flex-col gap-3 border-b p-4 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="font-semibold">{REPORTS.find((r) => r.id === report)!.label}</h2>
            <div className="flex flex-wrap items-center gap-2">
              {report === "sv" ? null : usesAsOf ? <label className="flex items-center gap-2 text-sm text-muted-foreground">As of <Input type="date" className="w-40" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>
                : <PeriodPicker value={period} onChange={setPeriod} />}
              <Button variant="outline" size="icon" onClick={() => window.print()} aria-label="Print"><PrinterIcon /></Button>
              {["pl", "tb", "db", "ar", "ap", "rx", "ds"].includes(report) && <Button variant="outline" size="icon" onClick={exportCsv} aria-label="Export CSV"><DownloadIcon /></Button>}
            </div>
          </div>
          {body}
        </Card>
      </div>
    </>
  );
}
