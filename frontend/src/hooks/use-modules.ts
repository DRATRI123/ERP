"use client";

import { useBizQuery } from "@/hooks/use-business";

export type ModuleKey = "pharmacy" | "cheques" | "tds" | "interest" | "manufacturing" | "pricing" | "catalogue" | "payroll" | "claims" | "licences" | "automation" | "developer";
export interface ModuleInfo { key: ModuleKey; label: string; description: string; enabled: boolean; default: boolean }

/** Defaults while the business's choice loads: everything on except pharmacy (as on the server). */
const DEFAULT_OFF = new Set<string>(["pharmacy", "interest"]);

export function isModuleOn(modules: Record<string, boolean> | undefined, key: string) {
  return modules?.[key] ?? !DEFAULT_OFF.has(key);
}

/** Which optional modules this business uses (Settings → Modules). */
export function useModules() {
  const q = useBizQuery<{ modules: ModuleInfo[]; enabled: Record<string, boolean> }>(["modules"], "/modules", undefined, { staleTime: 5 * 60_000 });
  return { list: q.data?.modules, enabled: q.data?.enabled, on: (key: ModuleKey) => isModuleOn(q.data?.enabled, key), loading: q.isLoading };
}
