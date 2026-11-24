import type * as React from "react";

import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

/** Label + control + error/hint, used by every form. */
export function Field({ label, error, hint, children, className, htmlFor, required }: {
  label: React.ReactNode; error?: string; hint?: React.ReactNode; children: React.ReactNode;
  className?: string; htmlFor?: string; required?: boolean;
}) {
  return (
    <div className={cn("grid gap-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}{required && <span className="text-destructive">*</span>}</Label>
      {children}
      {error ? <p className="text-xs text-destructive">{error}</p> : hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
