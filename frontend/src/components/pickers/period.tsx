"use client";

import { Input, NativeSelect } from "@/components/ui/input";
import { todayISO } from "@/lib/format";

export interface Period { from: string; to: string }

function fyStart(d: Date) {
  const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  return `${y}-04-01`;
}
function iso(d: Date) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

export const PRESETS: Record<string, () => Period> = {
  today: () => ({ from: todayISO(), to: todayISO() }),
  this_month: () => ({ from: `${todayISO().slice(0, 8)}01`, to: todayISO() }),
  last_month: () => {
    const d = new Date();
    const first = new Date(d.getFullYear(), d.getMonth() - 1, 1);
    const last = new Date(d.getFullYear(), d.getMonth(), 0);
    return { from: iso(first), to: iso(last) };
  },
  this_quarter: () => {
    const d = new Date();
    const q = Math.floor(d.getMonth() / 3) * 3;
    return { from: iso(new Date(d.getFullYear(), q, 1)), to: todayISO() };
  },
  this_fy: () => ({ from: fyStart(new Date()), to: todayISO() }),
};

/** Date range with Indian-business presets (financial year starts 1 April). */
export function PeriodPicker({ value, onChange }: { value: Period; onChange: (p: Period) => void }) {
  return (
    <div className="grid w-full grid-cols-[1fr_auto_1fr] items-center gap-2 sm:flex sm:w-auto sm:flex-wrap">
      <NativeSelect className="col-span-3 sm:w-40" defaultValue="" onChange={(e) => { const f = PRESETS[e.target.value]; if (f) onChange(f()); }} aria-label="Period">
        <option value="" disabled>Quick period…</option>
        <option value="today">Today</option><option value="this_month">This month</option>
        <option value="last_month">Last month</option><option value="this_quarter">This quarter</option>
        <option value="this_fy">This financial year</option>
      </NativeSelect>
      <Input type="date" className="min-w-0 sm:w-40" value={value.from} max={value.to} onChange={(e) => onChange({ ...value, from: e.target.value })} aria-label="From" />
      <span className="text-sm text-muted-foreground">to</span>
      <Input type="date" className="min-w-0 sm:w-40" value={value.to} min={value.from} onChange={(e) => onChange({ ...value, to: e.target.value })} aria-label="To" />
    </div>
  );
}
