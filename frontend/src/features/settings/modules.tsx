"use client";

import { BlocksIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { NearExpirySettings } from "@/features/pharmacy/pharmacy";
import { useBizMutation } from "@/hooks/use-business";
import { useModules, type ModuleKey } from "@/hooks/use-modules";

type Preset = { label: string; hint: string; on: ModuleKey[] };
const ALL: ModuleKey[] = ["pharmacy", "cheques", "tds", "interest", "damage", "loyalty", "manufacturing", "pricing", "catalogue", "payroll", "claims", "licences", "automation", "developer"];
/** Starting points; every switch can still be changed afterwards. */
const PRESETS: Preset[] = [
  { label: "Medical store / pharmacy", hint: "Retail or wholesale chemist", on: ["pharmacy", "cheques", "tds", "damage", "loyalty", "pricing", "catalogue", "payroll", "claims", "licences", "automation"] },
  { label: "Manufacturer (Ayurveda, FMCG…)", hint: "Makes and sells its own products", on: ["manufacturing", "damage", "cheques", "tds", "interest", "pricing", "catalogue", "payroll", "claims", "licences", "automation"] },
  { label: "Distributor / wholesaler", hint: "Buys and sells to shops", on: ["damage", "cheques", "tds", "interest", "pricing", "catalogue", "payroll", "claims", "licences", "automation"] },
  { label: "Retail shop", hint: "Counter billing", on: ["damage", "loyalty", "cheques", "pricing", "catalogue", "payroll", "claims", "automation"] },
  { label: "Services / clinic / office", hint: "No stock trading", on: ["tds", "payroll", "claims", "licences", "automation"] },
];

/** Settings → Modules: each business switches on only what it uses. */
export function ModulesSettings({ editable }: { editable: boolean }) {
  const { list, on } = useModules();
  const save = useBizMutation<{ modules: Record<string, boolean> }>("PUT", "/modules", {
    invalidate: [["modules"]], success: "Modules updated",
  });
  if (!list) return <Skeleton className="h-96" />;
  const apply = (p: Preset) => save.mutate({ modules: Object.fromEntries(ALL.map((k) => [k, p.on.includes(k)])) });
  return (
    <div className="space-y-4">
      <div className="rounded-xl border bg-card p-4 sm:p-5">
        <div className="mb-1 flex items-center gap-2"><BlocksIcon className="size-4 text-primary" /><h3 className="font-semibold">Modules for this business</h3></div>
        <p className="mb-4 text-sm text-muted-foreground">Each business shows only what it uses. Sales, purchases, stock, customers, payments, accounting,
          GST and reports are always on. Switching a module off only hides it — its data is kept and returns when you switch it back on.</p>
        {editable && (
          <div className="mb-4">
            <p className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Quick set-up by business type</p>
            <div className="flex flex-wrap gap-2">
              {PRESETS.map((p) => (
                <Button key={p.label} type="button" variant="outline" size="sm" disabled={save.isPending} onClick={() => apply(p)} title={p.hint}>{p.label}</Button>
              ))}
            </div>
          </div>
        )}
        <ul className="divide-y rounded-lg border">
          {list.map((m) => (
            <li key={m.key} className="flex items-start justify-between gap-4 px-3 py-3">
              <label htmlFor={`mod-${m.key}`} className="min-w-0">
                <span className="block text-sm font-medium">{m.label}</span>
                <span className="block text-xs text-muted-foreground">{m.description}</span>
              </label>
              <Switch id={`mod-${m.key}`} checked={on(m.key)} disabled={!editable || save.isPending}
                onCheckedChange={(v) => save.mutate({ modules: { [m.key]: v } })} aria-label={m.label} />
            </li>
          ))}
        </ul>
      </div>
      {on("pharmacy") && <NearExpirySettings editable={editable} />}
    </div>
  );
}
