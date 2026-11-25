"use client";

import { ArrowLeftRightIcon, Loader2Icon, PlusIcon } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { money, qty } from "@/lib/format";
import type { Product } from "@/lib/types";

export interface Doctor {
  id: string; name: string; registration_no: string | null; qualification: string | null; clinic: string | null;
  address: string | null; phone: string | null; is_active: boolean;
}
export type DoctorDraft = Omit<Doctor, "id">;
export const EMPTY_DOCTOR: DoctorDraft = { name: "", registration_no: "", qualification: "", clinic: "", address: "", phone: "", is_active: true };

/** Schedules that need the doctor and patient on the bill; the last three also need the Rx number. */
export const RX_SCHEDULES = new Set(["h", "h1", "x", "narcotic"]);
export const REGISTER_SCHEDULES = new Set(["h1", "x", "narcotic"]);
export const SCHEDULE_SHORT: Record<string, string> = { g: "Sch. G", h: "Sch. H", h1: "Sch. H1", x: "Sch. X", narcotic: "NDPS" };

/** Add or edit a prescribing doctor. */
export function DoctorDialog({ open, onOpenChange, doctor, initialName, onSaved }: {
  open: boolean; onOpenChange: (v: boolean) => void; doctor?: Doctor; initialName?: string; onSaved?: (d: Doctor) => void;
}) {
  const [d, setD] = useState<DoctorDraft>(() => (doctor ? { ...doctor } : { ...EMPTY_DOCTOR, name: initialName ?? "" }));
  const save = useBizMutation<DoctorDraft, Doctor>(doctor ? "PATCH" : "POST", doctor ? `/doctors/${doctor.id}` : "/doctors", {
    invalidate: [["doctors"]], success: (x) => `${x.name} saved`, onSuccess: (x) => { onSaved?.(x); onOpenChange(false); },
  });
  const set = (k: keyof DoctorDraft) => (e: React.ChangeEvent<HTMLInputElement>) => setD((x) => ({ ...x, [k]: e.target.value }));
  const body = Object.fromEntries(Object.entries(d).map(([k, v]) => [k, v === "" ? null : v])) as DoctorDraft;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>{doctor ? "Edit doctor" : "New doctor"}</DialogTitle>
          <DialogDescription>Printed on bills of prescription drugs and kept in the schedule register.</DialogDescription></DialogHeader>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate({ ...body, name: d.name.trim() }); }}>
          <Field label="Name" required htmlFor="dr-name" className="sm:col-span-2"><Input id="dr-name" autoFocus value={d.name} onChange={set("name")} placeholder="Dr. A. Mehta" /></Field>
          <Field label="Registration no." htmlFor="dr-reg" hint="Medical council number"><Input id="dr-reg" value={d.registration_no ?? ""} onChange={set("registration_no")} /></Field>
          <Field label="Qualification" htmlFor="dr-qual"><Input id="dr-qual" value={d.qualification ?? ""} onChange={set("qualification")} placeholder="MBBS, MD" /></Field>
          <Field label="Clinic / hospital" htmlFor="dr-clinic"><Input id="dr-clinic" value={d.clinic ?? ""} onChange={set("clinic")} /></Field>
          <Field label="Phone" htmlFor="dr-phone"><Input id="dr-phone" inputMode="tel" value={d.phone ?? ""} onChange={set("phone")} /></Field>
          <Field label="Address" htmlFor="dr-addr" className="sm:col-span-2"><Input id="dr-addr" value={d.address ?? ""} onChange={set("address")} /></Field>
          <DialogFooter className="sm:col-span-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={save.isPending || d.name.trim().length < 2}>{save.isPending && <Loader2Icon className="animate-spin" />} Save doctor</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Choose the prescribing doctor, or add one on the spot. */
export function DoctorSelect({ value, onChange, id }: { value: string; onChange: (id: string) => void; id?: string }) {
  const { can, isOwner } = useBusiness();
  const list = useBizQuery<Doctor[]>(["doctors"], "/doctors");
  const [adding, setAdding] = useState(false);
  return (
    <div className="flex gap-2">
      <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)} className="min-w-0 flex-1">
        <option value="">— Choose doctor —</option>
        {list.data?.map((d) => <option key={d.id} value={d.id}>{d.name}{d.clinic ? ` · ${d.clinic}` : ""}</option>)}
      </NativeSelect>
      {(isOwner || can("customers.manage")) && (
        <Button type="button" variant="outline" size="icon" onClick={() => setAdding(true)} aria-label="New doctor"><PlusIcon /></Button>
      )}
      {adding && <DoctorDialog open onOpenChange={setAdding} onSaved={(d) => onChange(d.id)} />}
    </div>
  );
}

/** Same salt, other brands: swap the line to one that is in stock or cheaper. */
export function SubstitutesDialog({ product, open, onOpenChange, onPick }: {
  product: { id: string; name: string; composition?: string | null }; open: boolean; onOpenChange: (v: boolean) => void; onPick: (p: Product) => void;
}) {
  const q = useBizQuery<Product[]>(["substitutes", product.id], `/products/${product.id}/substitutes`, undefined, { enabled: open });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>Substitutes for {product.name}</DialogTitle>
          <DialogDescription>{product.composition ? `Same salt: ${product.composition}` : "Add the salt / composition to the product to find substitutes."}</DialogDescription></DialogHeader>
        {q.isLoading ? <Skeleton className="h-24" /> : !q.data?.length ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No other product with this salt.</p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {q.data.map((p) => {
              const out = p.on_hand !== undefined && p.on_hand !== null && Number(p.on_hand) <= 0;
              return (
                <li key={p.id} className="flex items-center gap-3 px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{p.name}</p>
                    <p className="truncate text-xs text-muted-foreground">{p.sku}{p.rack ? ` · Rack ${p.rack as string}` : ""}
                      {p.on_hand !== undefined && p.on_hand !== null && <span className={out ? "text-destructive" : ""}> · {qty(p.on_hand as string)} in stock</span>}</p>
                  </div>
                  <span className="num text-sm">{p.selling_price ? money(p.selling_price) : "—"}</span>
                  <Button size="sm" variant="outline" disabled={out} onClick={() => { onPick(p); onOpenChange(false); }}><ArrowLeftRightIcon /> Use</Button>
                </li>
              );
            })}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function ScheduleBadge({ schedule }: { schedule?: string | null }) {
  if (!schedule || schedule === "none") return null;
  return <Badge variant={RX_SCHEDULES.has(schedule) ? "destructive" : "secondary"} className="h-5 px-1.5 text-[10px]">{SCHEDULE_SHORT[schedule] ?? schedule}</Badge>;
}

/** Settings: automatic discount on batches close to expiry, so they sell before they must be returned. */
export function NearExpirySettings({ editable }: { editable: boolean }) {
  const q = useBizQuery<{ near_expiry_discount: { days: number; percent: string } }>(["pharmacy-settings"], "/pharmacy/settings");
  if (!q.data) return <Skeleton className="h-40" />;
  return <NearExpiryForm key={JSON.stringify(q.data)} initial={q.data.near_expiry_discount} editable={editable} />;
}

function NearExpiryForm({ initial, editable }: { initial: { days: number; percent: string }; editable: boolean }) {
  const [days, setDays] = useState(String(initial.days || ""));
  const [percent, setPercent] = useState(Number(initial.percent) ? String(Number(initial.percent)) : "");
  const save = useBizMutation<{ days: number; percent: string }>("PUT", "/pharmacy/settings/near-expiry", {
    invalidate: [["pharmacy-settings"]], success: "Near-expiry discount saved",
  });
  const on = Number(days) > 0 && Number(percent) > 0;
  return (
    <div className="max-w-2xl space-y-4 rounded-xl border bg-card p-4 sm:p-5">
      <div>
        <h3 className="font-semibold">Near-expiry discount</h3>
        <p className="text-sm text-muted-foreground">On a sales invoice, a batch expiring within these days gets this discount automatically (shown as
          “Near expiry” on the line). Lines where you typed your own discount are left alone. Leave empty to turn it off.</p>
      </div>
      <form className="grid gap-4 sm:grid-cols-3" onSubmit={(e) => { e.preventDefault(); save.mutate({ days: Number(days || 0), percent: percent || "0" }); }}>
        <Field label="Expiring within (days)" htmlFor="ne-days"><Input id="ne-days" inputMode="numeric" value={days} disabled={!editable} onChange={(e) => setDays(e.target.value.replace(/\D/g, ""))} placeholder="60" /></Field>
        <Field label="Discount %" htmlFor="ne-pct"><Input id="ne-pct" inputMode="decimal" value={percent} disabled={!editable} onChange={(e) => setPercent(e.target.value.replace(/[^\d.]/g, ""))} placeholder="10" /></Field>
        <div className="flex items-end">{editable && <Button type="submit" disabled={save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />} Save</Button>}</div>
      </form>
      <p className="text-sm">{on ? <>Now: <b>{percent}% off</b> batches expiring within <b>{days} days</b>.</> : "Now: off."}</p>
    </div>
  );
}
