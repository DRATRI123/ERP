"use client";

import type * as React from "react";

import { HelpButton } from "@/features/help/help-button";
import { useT } from "@/lib/i18n";

/** Page title, description and actions. The ⓘ guide button appears next to the title on every page
 *  that has a guide (``help`` picks a specific one; ``help={false}`` hides it). */
export function PageHeader({ title, description, actions, children, help }: {
  title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode; children?: React.ReactNode; help?: string | false;
}) {
  const t = useT();
  return (
    <div className="mb-4 flex flex-col gap-3 sm:mb-6 sm:flex-row sm:items-end sm:justify-between sm:gap-4">
      <div className="min-w-0 space-y-1">
        <div className="flex items-center gap-1.5"><h1 className="truncate text-xl font-semibold tracking-tight sm:text-2xl">{typeof title === "string" ? t(title) : title}</h1>
          {help !== false && <HelpButton topic={help || undefined} />}</div>
        {description && <p className="line-clamp-2 text-sm text-muted-foreground sm:line-clamp-none">{description}</p>}
        {children}
      </div>
      {actions && <div className="no-scrollbar -mx-3 flex items-center gap-2 overflow-x-auto px-3 pb-0.5 sm:mx-0 sm:flex-wrap sm:overflow-visible sm:px-0 sm:pb-0 [&>*]:shrink-0">{actions}</div>}
    </div>
  );
}
