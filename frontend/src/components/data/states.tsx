import { AlertTriangleIcon, InboxIcon, LockIcon, RefreshCwIcon } from "lucide-react";
import type * as React from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

export function EmptyState({ icon: Icon = InboxIcon, title, description, action, className }: {
  icon?: React.ElementType; title: string; description?: React.ReactNode; action?: React.ReactNode; className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 px-6 py-14 text-center", className)}>
      <div className="flex size-12 items-center justify-center rounded-full bg-primary-soft text-primary"><Icon className="size-5" /></div>
      <div className="space-y-1">
        <p className="font-medium">{title}</p>
        {description && <p className="mx-auto max-w-sm text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function ErrorState({ error, retry, className }: { error: unknown; retry?: () => void; className?: string }) {
  const e = error instanceof ApiError ? error : null;
  if (e?.status === 403) {
    return <EmptyState icon={LockIcon} title="You don't have access to this"
      description="Ask the owner or a business administrator for the permission." className={className} />;
  }
  return (
    <div className={cn("flex flex-col items-center gap-3 px-6 py-12 text-center", className)}>
      <div className="flex size-12 items-center justify-center rounded-full bg-destructive/10 text-destructive"><AlertTriangleIcon className="size-5" /></div>
      <div>
        <p className="font-medium">Couldn&apos;t load this</p>
        <p className="text-sm text-muted-foreground">{e?.message ?? "Check your connection and try again."}</p>
        {e?.requestId && <p className="mt-1 text-xs text-muted-foreground">Reference {e.requestId.slice(0, 8)}</p>}
      </div>
      {retry && <Button variant="outline" size="sm" onClick={retry}><RefreshCwIcon /> Try again</Button>}
    </div>
  );
}

export function TableSkeleton({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="divide-y">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4 px-3 py-3">
          {Array.from({ length: cols }).map((__, c) => (
            <Skeleton key={c} className={cn("h-4", c === 0 ? "w-1/4" : "flex-1")} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function ComingSoon({ title, children }: { title: string; children: React.ReactNode }) {
  return <EmptyState title={title} description={children} />;
}
