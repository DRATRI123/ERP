"use client";

import { ArrowDownRightIcon, ArrowUpRightIcon, BanknoteIcon, Building2Icon, HandCoinsIcon, PackageIcon, ReceiptIndianRupeeIcon, TrendingUpIcon, TruckIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState } from "@/components/data/states";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery, useBusiness } from "@/hooks/use-business";
import { money } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useWorkspace } from "@/stores/business";

type Money = string;
interface Row {
  business_id: string; name: string; sales: Money; sales_prev: Money; purchases: Money; net_profit: Money; cash: Money; bank: Money;
  receivable: Money; receivable_overdue: Money; payable: Money; payable_overdue: Money; stock_value: Money; growth_percent: string | null;
  trend: { month: string; sales: Money }[];
}
interface Combined { period: string; from: string; to: string; totals: Omit<Row, "business_id" | "name" | "trend">; businesses: Row[] }

const PERIODS = [["today", "Today"], ["month", "This month"], ["year", "This financial year"]] as const;
const COLORS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];

function Growth({ value }: { value: string | null }) {
  if (value === null) return <span className="text-muted-foreground">—</span>;
  const up = Number(value) >= 0;
  return <span className={cn("inline-flex items-center gap-0.5 text-xs font-medium", up ? "text-success" : "text-destructive")}>
    {up ? <ArrowUpRightIcon className="size-3.5" /> : <ArrowDownRightIcon className="size-3.5" />}{Math.abs(Number(value))}%</span>;
}

export default function OwnerPage() {
  const router = useRouter();
  const { isOwner } = useBusiness();
  const setBusiness = useWorkspace((s) => s.setBusiness);
  const [period, setPeriod] = useState<string>("month");
  const q = useApiQuery<Combined>(["combined", period], "/dashboard/combined", { period }, { enabled: isOwner });
  const t = q.data?.totals;
  if (!isOwner) return <Card><EmptyState title="Owner only" description="The combined view of all businesses is for the owner." /></Card>;

  const months = q.data?.businesses[0]?.trend.map((m) => m.month) ?? [];
  const chart = months.map((m, i) => ({
    label: new Date(`${m}T00:00:00`).toLocaleDateString("en-IN", { month: "short" }),
    ...Object.fromEntries((q.data?.businesses ?? []).map((b) => [b.name, Number(b.trend[i]?.sales ?? 0)])),
  }));

  return (
    <>
      <PageHeader title="All businesses" description={q.data ? `Combined figures, ${new Date(q.data.from).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })} to today.` : "Combined figures."}
        actions={<div className="flex rounded-lg border p-0.5">{PERIODS.map(([k, label]) => (
          <button key={k} type="button" onClick={() => setPeriod(k)} aria-pressed={period === k}
            className={cn("rounded-md px-3 py-1.5 text-sm", period === k ? "bg-primary text-primary-foreground" : "hover:bg-accent")}>{label}</button>))}</div>} />
      {q.error ? <Card><ErrorState error={q.error} retry={() => q.refetch()} /></Card> : (
        <>
          <div className="mb-4 sm:mb-6 grid grid-cols-2 gap-3 sm:gap-4 sm:grid-cols-2 xl:grid-cols-3">
            <StatCard label="Sales" icon={TrendingUpIcon} loading={q.isLoading} value={t ? money(t.sales) : "—"}
              sub={t && <span className="flex items-center gap-1"><Growth value={t.growth_percent} /> vs same days before</span>} />
            <StatCard label="Net profit (books)" icon={ReceiptIndianRupeeIcon} tone={t && Number(t.net_profit) < 0 ? "red" : "green"} loading={q.isLoading} value={t ? money(t.net_profit) : "—"} />
            <StatCard label="Cash + bank" icon={BanknoteIcon} tone="blue" loading={q.isLoading} value={t ? money(Number(t.cash) + Number(t.bank)) : "—"}
              sub={t && `Cash ${money(t.cash)} · Bank ${money(t.bank)}`} />
            <StatCard label="To receive" icon={HandCoinsIcon} tone={t && Number(t.receivable_overdue) > 0 ? "amber" : "green"} loading={q.isLoading} value={t ? money(t.receivable) : "—"}
              sub={t && `${money(t.receivable_overdue)} overdue`} />
            <StatCard label="To pay" icon={TruckIcon} tone="violet" loading={q.isLoading} value={t ? money(t.payable) : "—"} sub={t && `${money(t.payable_overdue)} overdue`} />
            <StatCard label="Stock value" icon={PackageIcon} tone="blue" loading={q.isLoading} value={t ? money(t.stock_value) : "—"} />
          </div>

          <Card className="mb-6 overflow-hidden">
            <CardHeader className="pb-2"><CardTitle>Business by business</CardTitle></CardHeader>
            {q.isLoading ? <CardContent><Skeleton className="h-40" /></CardContent> : !q.data?.businesses.length ? <EmptyState icon={Building2Icon} title="No active businesses" /> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-muted/40 text-xs text-muted-foreground uppercase">
                    <tr><th className="px-4 py-2 text-left">Business</th><th className="px-3 py-2 text-right">Sales</th><th className="px-3 py-2 text-right">Growth</th>
                      <th className="px-3 py-2 text-right">Net profit</th><th className="hidden px-3 py-2 text-right md:table-cell">Cash + bank</th>
                      <th className="px-3 py-2 text-right">To receive</th><th className="hidden px-3 py-2 text-right lg:table-cell">To pay</th>
                      <th className="hidden px-3 py-2 text-right lg:table-cell">Stock</th></tr>
                  </thead>
                  <tbody className="divide-y">
                    {q.data.businesses.map((b) => (
                      <tr key={b.business_id} className="cursor-pointer hover:bg-accent/50" onClick={() => { setBusiness(b.business_id); router.push("/dashboard"); }}>
                        <td className="px-4 py-2.5 font-medium">{b.name}</td>
                        <td className="num px-3 py-2.5 text-right">{money(b.sales)}</td>
                        <td className="px-3 py-2.5 text-right"><Growth value={b.growth_percent} /></td>
                        <td className={cn("num px-3 py-2.5 text-right", Number(b.net_profit) < 0 && "text-destructive")}>{money(b.net_profit)}</td>
                        <td className="num hidden px-3 py-2.5 text-right md:table-cell">{money(Number(b.cash) + Number(b.bank))}</td>
                        <td className="num px-3 py-2.5 text-right">{money(b.receivable)}{Number(b.receivable_overdue) > 0 && <p className="text-xs text-destructive">{money(b.receivable_overdue)} overdue</p>}</td>
                        <td className="num hidden px-3 py-2.5 text-right lg:table-cell">{money(b.payable)}</td>
                        <td className="num hidden px-3 py-2.5 text-right lg:table-cell">{money(b.stock_value)}</td>
                      </tr>
                    ))}
                  </tbody>
                  {t && q.data.businesses.length > 1 && <tfoot className="border-t-2 font-semibold"><tr>
                    <td className="px-4 py-2.5">Total</td><td className="num px-3 py-2.5 text-right">{money(t.sales)}</td>
                    <td className="px-3 py-2.5 text-right"><Growth value={t.growth_percent} /></td><td className="num px-3 py-2.5 text-right">{money(t.net_profit)}</td>
                    <td className="num hidden px-3 py-2.5 text-right md:table-cell">{money(Number(t.cash) + Number(t.bank))}</td><td className="num px-3 py-2.5 text-right">{money(t.receivable)}</td>
                    <td className="num hidden px-3 py-2.5 text-right lg:table-cell">{money(t.payable)}</td><td className="num hidden px-3 py-2.5 text-right lg:table-cell">{money(t.stock_value)}</td>
                  </tr></tfoot>}
                </table>
              </div>
            )}
          </Card>

          {chart.length > 0 && <Card>
            <CardHeader className="pb-0"><CardTitle>Sales — last 6 months</CardTitle></CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={chart} margin={{ top: 12, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={11} stroke="var(--muted-foreground)" />
                  <YAxis tickLine={false} axisLine={false} fontSize={11} width={56} stroke="var(--muted-foreground)" tickFormatter={(v: number) => money(v, { compact: true })} />
                  <Tooltip contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }} formatter={(v) => money(Number(v))} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  {q.data!.businesses.map((b, i) => <Bar key={b.business_id} dataKey={b.name} stackId="s" fill={COLORS[i % COLORS.length]} radius={i === q.data!.businesses.length - 1 ? [4, 4, 0, 0] : 0} />)}
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>}
        </>
      )}
    </>
  );
}
