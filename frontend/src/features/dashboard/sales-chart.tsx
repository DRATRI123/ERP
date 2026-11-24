"use client";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { money } from "@/lib/format";
import type { Dashboard } from "@/lib/types";

export function SalesChart({ data }: { data: NonNullable<Dashboard["daily"]> }) {
  const rows = data.map((d) => ({ ...d, sales: Number(d.sales), purchases: Number(d.purchases),
    label: new Date(`${d.date}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) }));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="gSales" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.35} /><stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="gPurch" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-2)" stopOpacity={0.2} /><stop offset="100%" stopColor="var(--chart-2)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
        <XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={11} stroke="var(--muted-foreground)" interval="preserveStartEnd" minTickGap={24} />
        <YAxis tickLine={false} axisLine={false} fontSize={11} width={56} stroke="var(--muted-foreground)"
          tickFormatter={(v: number) => money(v, { compact: true })} />
        <Tooltip
          contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
          formatter={(v, name) => [money(Number(v)), name === "sales" ? "Sales" : "Purchases"]} labelStyle={{ color: "var(--muted-foreground)" }} />
        <Area type="monotone" dataKey="purchases" stroke="var(--chart-2)" strokeWidth={1.5} fill="url(#gPurch)" />
        <Area type="monotone" dataKey="sales" stroke="var(--chart-1)" strokeWidth={2.25} fill="url(#gSales)" />
      </AreaChart>
    </ResponsiveContainer>
  );
}
