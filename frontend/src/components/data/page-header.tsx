"use client";

import type * as React from "react";

import { HelpButton } from "@/features/help/help-button";

/** Page title, description and actions. The ⓘ guide button appears next to the title on every page
 *  that has a guide (``help`` picks a specific one; ``help={false}`` hides it). */
export function PageHeader({ title, description, actions, children, help }: {
  title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode; children?: React.ReactNode; help?: string | false;
}) {
  return (
    <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0 space-y-1">
        <div className="flex items-center gap-1.5"><h1 className="truncate text-2xl font-semibold tracking-tight">{title}</h1>
          {help !== false && <HelpButton topic={help || undefined} />}</div>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
        {children}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
