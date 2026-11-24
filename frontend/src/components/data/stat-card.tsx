import type * as React from "react";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function StatCard({ label, value, sub, icon: Icon, tone = "green", loading }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode; icon: React.ElementType;
  tone?: "green" | "blue" | "amber" | "red" | "violet"; loading?: boolean;
}) {
  const tones = {
    green: "bg-primary-soft text-primary", blue: "bg-blue-soft text-blue",
    amber: "bg-warning/15 text-amber-600 dark:text-warning", red: "bg-destructive/10 text-destructive",
    violet: "bg-[oklch(0.95_0.03_300)] text-[oklch(0.5_0.17_300)] dark:bg-[oklch(0.3_0.06_300)] dark:text-[oklch(0.8_0.1_300)]",
  };
  return (
    <Card className="relative overflow-hidden p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
          {loading ? <Skeleton className="h-7 w-28" /> : <p className="num truncate text-2xl font-semibold tracking-tight">{value}</p>}
          {sub && !loading && <div className="text-xs text-muted-foreground">{sub}</div>}
        </div>
        <span className={cn("flex size-9 shrink-0 items-center justify-center rounded-lg", tones[tone])}><Icon className="size-[18px]" /></span>
      </div>
    </Card>
  );
}
