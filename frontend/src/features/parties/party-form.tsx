"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { useModules } from "@/hooks/use-modules";
import { api, ApiError } from "@/lib/api";
import { GSTIN_RE, STATES } from "@/lib/india";
import type { Party } from "@/lib/types";

const schema = z.object({
  name: z.string().trim().min(2, "Required"),
  gst_type: z.string(),
  gstin: z.string().trim().toUpperCase().refine((v) => !v || GSTIN_RE.test(v), "Not a valid GSTIN").optional(),
  state_code: z.string().optional(),
  contact_person: z.string().optional(),
  phone: z.string().trim().refine((v) => !v || /^\+?[0-9 -]{7,15}$/.test(v), "Not a valid phone").optional(),
  email: z.string().trim().refine((v) => !v || z.email().safeParse(v).success, "Not a valid e-mail").optional(),
  credit_limit: z.string().regex(/^\d*(\.\d{0,2})?$/).optional(),
  credit_days: z.string().regex(/^\d*$/).optional(),
  pan: z.string().trim().toUpperCase().regex(/^([A-Z]{5}[0-9]{4}[A-Z])?$/, "10 characters, e.g. ABCDE1234F").optional(),
  tds_section: z.string().optional(),
  interest_rate: z.string().regex(/^\d*(\.\d{0,2})?$/, "A % like 18").optional(),
  address: z.string().optional(), city: z.string().optional(), pincode: z.string().regex(/^(\d{6})?$/, "6 digits").optional(),
  notes: z.string().optional(),
  price_list_id: z.string().optional(),
  credit_hold: z.boolean().optional(),
});
type Values = z.infer<typeof schema>;

export function PartyDialog({ kind, open, onOpenChange, party, initialName, onSaved }: {
  kind: "customers" | "suppliers"; open: boolean; onOpenChange: (v: boolean) => void; party?: Party; initialName?: string; onSaved?: (p: Party) => void;
}) {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const modules = useModules();
  const priceLists = useBizQuery<{ id: string; name: string; is_active: boolean }[]>(["price-lists"], "/price-lists", undefined, { enabled: open && kind === "customers" });
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    values: party ? {
      name: party.name, gst_type: (party.gst_type as string) ?? "unregistered", gstin: party.gstin ?? "", state_code: party.state_code ?? "",
      contact_person: (party.contact_person as string) ?? "", phone: party.phone ?? "", email: party.email ?? "",
      credit_limit: party.credit_limit ? String(Number(party.credit_limit)) : "", credit_days: party.credit_days?.toString() ?? "", notes: (party.notes as string) ?? "",
      price_list_id: (party.price_list_id as string | null) ?? "", credit_hold: !!party.credit_hold,
      pan: (party.pan as string | null) ?? "", tds_section: (party.tds_section as string | null) ?? "",
      interest_rate: party.interest_rate !== null && party.interest_rate !== undefined ? String(Number(party.interest_rate)) : "",
    } : undefined,
    defaultValues: { name: initialName ?? "", gst_type: "unregistered", state_code: "" },
  });
  const save = useMutation({
    mutationFn: (v: Values) => {
      const nn = (x?: string) => (x ? x : null);
      const body: Record<string, unknown> = {
        name: v.name, gst_type: v.gstin ? "regular" : v.gst_type, gstin: nn(v.gstin), state_code: nn(v.state_code ?? (v.gstin ? v.gstin.slice(0, 2) : "")),
        contact_person: nn(v.contact_person), phone: nn(v.phone), email: nn(v.email), credit_limit: nn(v.credit_limit),
        credit_days: v.credit_days ? Number(v.credit_days) : null, notes: nn(v.notes), pan: nn(v.pan),
        ...(modules.on("tds") ? { tds_section: nn(v.tds_section) } : {}),
        ...(modules.on("interest") && kind === "customers" ? { interest_rate: nn(v.interest_rate) } : {}),
        ...(kind === "customers" ? { price_list_id: nn(v.price_list_id), credit_hold: !!v.credit_hold } : {}),
      };
      if (!party && v.address) body.addresses = [{ kind: "billing", address_line1: v.address, city: nn(v.city), pincode: nn(v.pincode), state_code: body.state_code, is_default: true }];
      return party ? api<Party>(`${base}/${kind}/${party.id}`, { method: "PATCH", body }) : api<Party>(`${base}/${kind}`, { body });
    },
    onSuccess: (p) => { toast.success(`${p.name} saved`); qc.invalidateQueries({ queryKey: ["biz", id] }); onSaved?.(p); onOpenChange(false); form.reset(); },
    onError: (e: ApiError) => { for (const [k, m] of Object.entries(e.fieldErrors)) form.setError(k as keyof Values, { message: m }); toast.error(e.message); },
  });
  const err = form.formState.errors;
  const label = kind === "customers" ? "customer" : "supplier";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>{party ? `Edit ${label}` : `New ${label}`}</DialogTitle></DialogHeader>
        <form onSubmit={form.handleSubmit((v) => save.mutate(v))} className="grid gap-4 sm:grid-cols-2" noValidate>
          <Field label="Name" required error={err.name?.message} className="sm:col-span-2"><Input autoFocus {...form.register("name")} /></Field>
          <Field label="GSTIN" error={err.gstin?.message} hint="State fills in from the GSTIN"><Input className="uppercase" {...form.register("gstin")} /></Field>
          <Field label="State (place of supply)">
            <NativeSelect {...form.register("state_code")}><option value="">—</option>{STATES.map(([c, n]) => <option key={c} value={c}>{c} — {n}</option>)}</NativeSelect>
          </Field>
          <Field label="Phone" error={err.phone?.message}><Input inputMode="tel" {...form.register("phone")} /></Field>
          <Field label="E-mail" error={err.email?.message}><Input type="email" {...form.register("email")} /></Field>
          <Field label="Contact person"><Input {...form.register("contact_person")} /></Field>
          <Field label="PAN" error={err.pan?.message} hint={modules.on("tds") ? "Without PAN, TDS is 20%" : undefined}><Input className="uppercase" maxLength={10} {...form.register("pan")} /></Field>
          {modules.on("tds") && (
            <Field label="Usual TDS section" hint="Pre-filled when paying them"><NativeSelect {...form.register("tds_section")}>
              <option value="">No TDS</option>{["194C", "194J", "194H", "194I", "194Q", "194A", "194T"].map((x) => <option key={x} value={x}>{x}</option>)}</NativeSelect></Field>
          )}
          {modules.on("interest") && kind === "customers" && (
            <Field label="Interest on overdue (% per year)" error={err.interest_rate?.message} hint="Empty: business default; 0: never"><Input inputMode="decimal" {...form.register("interest_rate")} /></Field>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Field label="Credit limit"><Input inputMode="decimal" {...form.register("credit_limit")} /></Field>
            <Field label="Credit days"><Input inputMode="numeric" {...form.register("credit_days")} /></Field>
          </div>
          {kind === "customers" && <>
            <Field label="Price list" hint="Hospital, doctor, wholesale… prices"><NativeSelect {...form.register("price_list_id")}>
              <option value="">Normal prices</option>{priceLists.data?.filter((p) => p.is_active).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</NativeSelect></Field>
            <label className="flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" className="size-4 accent-[var(--primary)]" {...form.register("credit_hold")} />
              Credit hold — only full-payment sales</label>
          </>}
          {!party && <>
            <Field label="Address" className="sm:col-span-2"><Input {...form.register("address")} /></Field>
            <Field label="City"><Input {...form.register("city")} /></Field>
            <Field label="PIN code" error={err.pincode?.message}><Input inputMode="numeric" {...form.register("pincode")} /></Field>
          </>}
          <Field label="Notes" className="sm:col-span-2"><Textarea {...form.register("notes")} className="min-h-14" /></Field>
          <DialogFooter className="sm:col-span-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />} Save</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
