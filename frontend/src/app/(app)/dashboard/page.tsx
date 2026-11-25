"use client";

import {
  AlertTriangleIcon, ArrowDownRightIcon, ArrowUpRightIcon, Building2Icon, CalendarClockIcon, FilePlus2Icon,
  HandCoinsIcon, IndianRupeeIcon, PackageXIcon, PiggyBankIcon, ReceiptTextIcon, TrendingUpIcon, WalletIcon,
} from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState } from "@/components/data/states";
import { DocStatusBadge } from "@/components/data/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SalesChart } from "@/features/dashboard/sales-chart";
import { useApiQuery, useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money, qty } from "@/lib/format";
import { DOC_LABEL, type Dashboard } from "@/lib/types";
import { useAuth } from "@/stores/auth";

interface OverviewRow { business_id: string; name: string; sales_month?: string; sales_prev_period?: string; growth_percent?: string | null; receivable?: string; receivable_overdue?: string }

export default function DashboardPage() {
  const me = useAuth((s) => s.me);
  const { business, isOwner, can } = useBusiness();
  const { data, isLoading, error, refetch } = useBizQuery<Dashboard>(["dashboard"], "/dashboard");
  const overview = useApiQuery<OverviewRow[]>(["overview"], "/dashboard/overview", undefined, { enabled: !!business });

  const hour = new Date().getHours();
  const greet = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";

  if (!business) {
    return (
      <Card className="mx-auto mt-10 max-w-lg">
        <EmptyState icon={Building2Icon} title="Set up your first business"
          description={isOwner ? "Add a business with its GSTIN, state and financial year. Branches, roles, a chart of accounts and a main warehouse are created for you."
            : "You haven't been added to a business yet. Ask the owner to invite you."}
          action={isOwner && <Button asChild><Link href="/businesses?new=1">Create business</Link></Button>} />
      </Card>
    );
  }

  const s = data?.sales, pr = data?.profit, pu = data?.purchases;
  return (
    <>
      <PageHeader
        title={`${greet}, ${me?.user.full_name.split(" ")[0] ?? ""}`}
        description={<>Here&apos;s how <span className="font-medium text-foreground">{business.business_name}</span> is doing today.</>}
        actions={<>
          {can("invoices.create") || isOwner ? <Button asChild><Link href="/sales/new"><FilePlus2Icon /> New invoice</Link></Button> : null}
          {can("purchases.create") || isOwner ? <Button variant="outline" asChild><Link href="/purchases/new">New purchase</Link></Button> : null}
        </>}
      />
      {error ? <Card><ErrorState error={error} retry={() => refetch()} /></Card> : (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 sm:gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard label="Today's sales" icon={IndianRupeeIcon} loading={isLoading} value={s ? money(s.today) : "—"}
              sub={s && <>{s.invoices_today} invoice{s.invoices_today === 1 ? "" : "s"} · {money(data?.payments_today?.received)} received</>} />
            <StatCard label="This month's revenue" icon={TrendingUpIcon} tone="blue" loading={isLoading} value={s ? money(s.month) : "—"}
              sub={pr?.gross_margin_percent ? `${pr.gross_margin_percent}% gross margin` : "since " + date(data?.month_start)} />
            <StatCard label="Expenses (month)" icon={WalletIcon} tone="amber" loading={isLoading} value={pr ? money(pr.expenses) : pu ? money(pu.month) : "—"}
              sub={pr ? `Cost of goods ${money(pr.cost_of_goods_sold)}` : pu ? "Purchases this month" : undefined} />
            <StatCard label="Net profit (month)" icon={PiggyBankIcon} tone={pr && Number(pr.net_profit) < 0 ? "red" : "green"} loading={isLoading}
              value={pr ? money(pr.net_profit) : "—"} sub={pr ? `Gross profit ${money(pr.gross_profit)}` : "Needs accounting access"} />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader className="flex-row items-center justify-between">
                <div><CardTitle>Sales vs purchases</CardTitle><p className="mt-1 text-xs text-muted-foreground">Last 30 days, net of returns</p></div>
                <div className="flex gap-3 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-chart-1" />Sales</span>
                  <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-chart-2" />Purchases</span>
                </div>
              </CardHeader>
              <CardContent>{isLoading ? <Skeleton className="h-[260px]" /> : data?.daily ? <SalesChart data={data.daily} /> : <EmptyState title="No sales data" />}</CardContent>
            </Card>
            <div className="grid gap-4">
              <StatCard label="To receive" icon={HandCoinsIcon} tone="green" loading={isLoading} value={s ? money(s.receivable) : "—"}
                sub={s && (Number(s.receivable_overdue) > 0 ? <span className="text-destructive">{money(s.receivable_overdue)} overdue</span> : `${s.open_invoices} open invoices`)} />
              <StatCard label="To pay" icon={ReceiptTextIcon} tone="violet" loading={isLoading} value={pu ? money(pu.payable) : "—"}
                sub={pu && (Number(pu.payable_overdue) > 0 ? <span className="text-destructive">{money(pu.payable_overdue)} overdue</span> : `${pu.open_bills} open bills`)} />
              <StatCard label="Stock alerts" icon={AlertTriangleIcon} tone={data?.stock?.low_stock_count ? "red" : "blue"} loading={isLoading}
                value={data?.stock ? data.stock.low_stock_count : "—"}
                sub={data?.stock && <>{data.stock.expiring_30d} batch{data.stock.expiring_30d === 1 ? "" : "es"} expiring in 30 days</>} />
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle>Recent transactions</CardTitle>
                <Button variant="link" size="sm" asChild className="h-auto p-0"><Link href="/sales">View all</Link></Button>
              </CardHeader>
              <CardContent className="px-0 pb-1">
                {isLoading ? <div className="space-y-3 px-5">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-9" />)}</div>
                  : !data?.recent?.length ? <EmptyState icon={ReceiptTextIcon} title="No transactions yet" description="Invoices and bills you create appear here." /> : (
                  <ul className="divide-y">
                    {data.recent.map((d) => (
                      <li key={d.id}>
                        <Link href={`/documents/${d.id}`} className="flex items-center gap-3 px-5 py-2.5 hover:bg-muted/40">
                          <span className={`flex size-8 items-center justify-center rounded-md ${d.doc_type.startsWith("purchase") ? "bg-blue-soft text-blue" : "bg-primary-soft text-primary"}`}>
                            {d.doc_type.startsWith("purchase") ? <ArrowDownRightIcon className="size-4" /> : <ArrowUpRightIcon className="size-4" />}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium">{d.party_name ?? "Walk-in customer"}</span>
                            <span className="block truncate text-xs text-muted-foreground">{DOC_LABEL[d.doc_type]} {d.number} · {date(d.doc_date)}</span>
                          </span>
                          <DocStatusBadge status={d.status} payment={d.payment_status} />
                          <span className="num w-28 text-right text-sm font-medium">{money(d.grand_total)}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle>Low stock</CardTitle>
                <Button variant="link" size="sm" asChild className="h-auto p-0"><Link href="/inventory?tab=alerts">Inventory</Link></Button>
              </CardHeader>
              <CardContent className="pt-2">
                {isLoading ? <Skeleton className="h-40" /> : !data?.stock ? <p className="text-sm text-muted-foreground">Needs stock access.</p>
                  : data.stock.low_stock.length === 0 ? <EmptyState icon={PackageXIcon} title="All stocked up" description="Nothing is below its reorder level." className="py-8" /> : (
                  <ul className="space-y-3">
                    {data.stock.low_stock.map((p) => {
                      const pct = Math.max(4, Math.min(100, (Number(p.on_hand) / Math.max(Number(p.reorder_level), 1)) * 100));
                      return (
                        <li key={p.product_id} className="space-y-1.5">
                          <div className="flex items-center justify-between text-sm"><span className="truncate font-medium">{p.name}</span>
                            <span className="num text-xs text-muted-foreground">{qty(p.on_hand)} / {qty(p.reorder_level)}</span></div>
                          <div className="h-1.5 rounded-full bg-muted"><div className="h-full rounded-full bg-destructive/80" style={{ width: `${pct}%` }} /></div>
                        </li>
                      );
                    })}
                  </ul>
                )}
                {data?.top_customers && data.top_customers.length > 0 && (
                  <div className="mt-6 border-t pt-4">
                    <p className="mb-3 text-sm font-semibold">Top customers this month</p>
                    <ul className="space-y-2 text-sm">
                      {data.top_customers.map((c) => <li key={c.name} className="flex justify-between gap-2"><span className="truncate">{c.name}</span><span className="num font-medium">{money(c.amount)}</span></li>)}
                    </ul>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {(overview.data?.length ?? 0) > 1 && (
            <Card>
              <CardHeader><CardTitle>Business-wise performance</CardTitle><p className="text-xs text-muted-foreground">This month so far, compared with the same days last month</p></CardHeader>
              <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {overview.data!.map((b) => {
                  const g = b.growth_percent ? Number(b.growth_percent) : null;
                  return (
                    <div key={b.business_id} className="rounded-lg border p-4">
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate font-medium">{b.name}</p>
                        {g !== null && <Badge variant={g >= 0 ? "default" : "destructive"}>{g >= 0 ? "▲" : "▼"} {Math.abs(g)}%</Badge>}
                      </div>
                      <p className="num mt-2 text-xl font-semibold">{b.sales_month ? money(b.sales_month) : "—"}</p>
                      <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground"><CalendarClockIcon className="size-3" />
                        To receive {money(b.receivable)}{Number(b.receivable_overdue ?? 0) > 0 && <span className="text-destructive"> · {money(b.receivable_overdue)} overdue</span>}</p>
                    </div>
                  );
                })}
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </>
  );
}
