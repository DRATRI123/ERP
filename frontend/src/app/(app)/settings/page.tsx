"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangleIcon, CheckCircle2Icon, DatabaseBackupIcon, DownloadIcon, GlobeIcon, Loader2Icon, PaletteIcon, PlusIcon, ShieldCheckIcon, WrenchIcon,
} from "lucide-react";
import Link from "next/link";
import { useState, Suspense } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/data/page-header";
import { ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { useSearchParams } from "next/navigation";
import { BillingRules } from "@/features/settings/billing-rules";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ModulesSettings } from "@/features/settings/modules";
import { useApiQuery, useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError, openFile, type Page } from "@/lib/api";
import { dateTime, titleCase } from "@/lib/format";
import { GSTIN_RE, STATES } from "@/lib/india";
import { DOC_LABEL, type DocType } from "@/lib/types";

interface Business { id: string; name: string; legal_name: string | null; gstin: string | null; pan: string | null; phone: string | null; email: string | null;
  address_line1: string | null; address_line2: string | null; city: string | null; state_code: string | null; pincode: string | null; fy_start_month: number; inventory_valuation: string }
interface Branch { id: string; code: string; name: string; kind: string; gstin: string | null; phone: string | null; is_active: boolean; city: string | null; state_code: string | null; address_line1: string | null; pincode: string | null }
interface Series { id: string; doc_type: DocType; name: string; prefix: string; suffix: string; padding: number; is_default: boolean; is_active: boolean }

export default function SettingsPage() {
  return <Suspense><SettingsTabs /></Suspense>;
}

function SettingsTabs() {
  const { isOwner, can } = useBusiness();
  const raw = useSearchParams().get("tab") ?? "business";
  const tab = raw === "pharmacy" ? "modules" : raw;
  return (
    <>
      <PageHeader title="Settings" description="Business profile, branches, numbering, print templates, modules and system." />
      <Tabs defaultValue={tab}>
        <TabsList>
          <TabsTrigger value="business">Business</TabsTrigger><TabsTrigger value="branches">Branches</TabsTrigger>
          <TabsTrigger value="numbering">Numbering</TabsTrigger><TabsTrigger value="templates">Print templates</TabsTrigger>
          <TabsTrigger value="modules">Modules</TabsTrigger>
          {isOwner && <TabsTrigger value="system">System</TabsTrigger>}
        </TabsList>
        <TabsContent value="business"><BusinessProfile editable={isOwner || can("business.settings")} /><BillingRules editable={isOwner || can("business.settings")} /></TabsContent>
        <TabsContent value="branches"><Branches editable={isOwner || can("branches.manage")} /></TabsContent>
        <TabsContent value="numbering"><Numbering editable={isOwner || can("business.settings")} /></TabsContent>
        <TabsContent value="templates">
          <Card><CardContent className="flex flex-col items-start gap-3 sm:flex-row sm:items-center">
            <span className="flex size-10 items-center justify-center rounded-lg bg-primary-soft text-primary"><PaletteIcon className="size-5" /></span>
            <div className="flex-1"><p className="font-medium">Invoice designer</p><p className="text-sm text-muted-foreground">Logo, colours, columns (drag to reorder), A4 or thermal, UPI QR, terms and signature — with live preview.</p></div>
            <Button asChild><Link href="/settings/templates">Open designer</Link></Button>
          </CardContent></Card>
        </TabsContent>
        <TabsContent value="modules"><ModulesSettings editable={isOwner || can("business.settings")} /></TabsContent>
        {isOwner && <TabsContent value="system"><System /></TabsContent>}
      </Tabs>
      <Card className="mt-6"><CardContent className="flex items-center gap-3 text-sm">
        <ShieldCheckIcon className="size-5 text-primary" /><span className="flex-1">Two-factor sign-in, devices and password</span>
        <Button variant="outline" size="sm" asChild><Link href="/settings/security">Security settings</Link></Button>
      </CardContent></Card>
    </>
  );
}

function BusinessProfile({ editable }: { editable: boolean }) {
  const b = useBizQuery<Business>(["business"], "");
  if (b.isLoading) return <Skeleton className="h-72" />;
  if (b.error || !b.data) return <Card><ErrorState error={b.error} /></Card>;
  return <BusinessForm key={b.data.id} business={b.data} editable={editable} />;
}

function BusinessForm({ business, editable }: { business: Business; editable: boolean }) {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const [v, setV] = useState<Partial<Business>>(business);
  const save = useMutation({
    mutationFn: () => api(base, { method: "PATCH", body: {
      name: v.name, legal_name: v.legal_name || null, gstin: v.gstin || null, pan: v.pan || null, phone: v.phone || null, email: v.email || null,
      address_line1: v.address_line1 || null, address_line2: v.address_line2 || null, city: v.city || null, state_code: v.state_code || null, pincode: v.pincode || null } }),
    onSuccess: () => { toast.success("Business profile saved"); qc.invalidateQueries({ queryKey: ["biz", id] }); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const set = (k: keyof Business) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setV({ ...v, [k]: e.target.value });
  const gstinBad = !!v.gstin && !GSTIN_RE.test(v.gstin.toUpperCase());
  return (
    <Card>
      <CardHeader><CardTitle>Business profile</CardTitle><CardDescription>Printed on invoices and used for GST. Financial year starts in month {business.fy_start_month}; stock valued by {titleCase(business.inventory_valuation)}.</CardDescription></CardHeader>
      <CardContent>
        <fieldset disabled={!editable} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Trade name"><Input value={v.name ?? ""} onChange={set("name")} /></Field>
          <Field label="Legal name"><Input value={v.legal_name ?? ""} onChange={set("legal_name")} /></Field>
          <Field label="GSTIN" error={gstinBad ? "Not a valid GSTIN" : undefined}><Input className="uppercase" value={v.gstin ?? ""} onChange={set("gstin")} /></Field>
          <Field label="PAN"><Input className="uppercase" value={v.pan ?? ""} onChange={set("pan")} /></Field>
          <Field label="Phone"><Input value={v.phone ?? ""} onChange={set("phone")} /></Field>
          <Field label="E-mail"><Input type="email" value={v.email ?? ""} onChange={set("email")} /></Field>
          <Field label="Address" className="lg:col-span-2"><Input value={v.address_line1 ?? ""} onChange={set("address_line1")} /></Field>
          <Field label="City"><Input value={v.city ?? ""} onChange={set("city")} /></Field>
          <Field label="State"><NativeSelect value={v.state_code ?? ""} onChange={set("state_code")}><option value="">—</option>{STATES.map(([c, n]) => <option key={c} value={c}>{c} — {n}</option>)}</NativeSelect></Field>
          <Field label="PIN code"><Input value={v.pincode ?? ""} onChange={set("pincode")} /></Field>
        </fieldset>
        {editable && <Button className="mt-5" disabled={gstinBad || save.isPending} onClick={() => save.mutate()}>{save.isPending && <Loader2Icon className="animate-spin" />} Save profile</Button>}
      </CardContent>
    </Card>
  );
}

function Branches({ editable }: { editable: boolean }) {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const list = useBizQuery<Branch[]>(["branches"], "/branches");
  const [open, setOpen] = useState(false);
  const [v, setV] = useState({ code: "", name: "", kind: "branch", gstin: "", city: "", state_code: "", address_line1: "", pincode: "" });
  const create = useMutation({
    mutationFn: () => api(`${base}/branches`, { body: Object.fromEntries(Object.entries(v).map(([k, x]) => [k, x || null])) }),
    onSuccess: () => { toast.success("Branch added (with its own warehouse)"); qc.invalidateQueries({ queryKey: ["biz", id] }); setOpen(false); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  return (
    <>
      {editable && <div className="mb-3 flex justify-end"><Button onClick={() => setOpen(true)}><PlusIcon /> Add branch</Button></div>}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {list.data?.map((b) => (
          <Card key={b.id}><CardHeader className="flex-row items-start justify-between"><div><CardTitle>{b.name}</CardTitle><p className="mt-1 font-mono text-xs text-muted-foreground">{b.code}</p></div>
            <Badge variant={b.is_active ? "default" : "secondary"}>{titleCase(b.kind)}</Badge></CardHeader>
            <CardContent className="text-sm text-muted-foreground">{[b.address_line1, b.city, b.pincode].filter(Boolean).join(", ") || "No address"}{b.gstin && <p className="mt-1 font-mono text-xs">{b.gstin}</p>}</CardContent></Card>
        ))}
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>New branch</DialogTitle></DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Code"><Input value={v.code} onChange={(e) => setV({ ...v, code: e.target.value.toUpperCase() })} placeholder="PUNE2" /></Field>
            <Field label="Name"><Input value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} /></Field>
            <Field label="Type"><NativeSelect value={v.kind} onChange={(e) => setV({ ...v, kind: e.target.value })}><option value="branch">Branch / shop</option><option value="head_office">Head office</option></NativeSelect></Field>
            <Field label="GSTIN (if separate)"><Input value={v.gstin} onChange={(e) => setV({ ...v, gstin: e.target.value.toUpperCase() })} /></Field>
            <Field label="Address" className="sm:col-span-2"><Input value={v.address_line1} onChange={(e) => setV({ ...v, address_line1: e.target.value })} /></Field>
            <Field label="City"><Input value={v.city} onChange={(e) => setV({ ...v, city: e.target.value })} /></Field>
            <Field label="State"><NativeSelect value={v.state_code} onChange={(e) => setV({ ...v, state_code: e.target.value })}><option value="">—</option>{STATES.map(([c, n]) => <option key={c} value={c}>{n}</option>)}</NativeSelect></Field>
          </div>
          <DialogFooter><Button disabled={!v.code || v.name.length < 2 || create.isPending} onClick={() => create.mutate()}>Add branch</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function Numbering({ editable }: { editable: boolean }) {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const list = useBizQuery<Series[]>(["series"], "/document-series");
  const [open, setOpen] = useState(false);
  const [v, setV] = useState({ doc_type: "sales_invoice", name: "", prefix: "", padding: "4", is_default: false });
  const create = useMutation({
    mutationFn: () => api(`${base}/document-series`, { body: { ...v, padding: Number(v.padding) } }),
    onSuccess: () => { toast.success("Series added"); qc.invalidateQueries({ queryKey: ["biz", id, "series"] }); setOpen(false); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between"><div><CardTitle>Document numbering</CardTitle><CardDescription>Gapless per series. {"{FY}"} becomes the financial year, e.g. 2026-27.</CardDescription></div>
        {editable && <Button size="sm" onClick={() => setOpen(true)}><PlusIcon /> New series</Button>}</CardHeader>
      <CardContent className="divide-y p-0">
        {list.data?.map((s) => (
          <div key={s.id} className="flex items-center gap-3 px-5 py-3 text-sm">
            <span className="w-40 font-medium">{DOC_LABEL[s.doc_type]}</span><span className="flex-1">{s.name}</span>
            <span className="font-mono text-xs">{s.prefix}{"0".repeat(Math.max(s.padding - 1, 0))}1{s.suffix}</span>{s.is_default && <Badge>Default</Badge>}
          </div>
        ))}
        {list.data?.length === 0 && <p className="px-5 py-6 text-sm text-muted-foreground">Default series are created the first time each document type is used.</p>}
      </CardContent>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>New numbering series</DialogTitle></DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Document"><NativeSelect value={v.doc_type} onChange={(e) => setV({ ...v, doc_type: e.target.value })}>{Object.entries(DOC_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</NativeSelect></Field>
            <Field label="Name"><Input value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} placeholder="Counter 2" /></Field>
            <Field label="Prefix" hint="Max 16 characters in total for GST invoices"><Input value={v.prefix} onChange={(e) => setV({ ...v, prefix: e.target.value.toUpperCase() })} placeholder="C2/{FY}/" /></Field>
            <Field label="Digits"><Input value={v.padding} inputMode="numeric" onChange={(e) => setV({ ...v, padding: e.target.value })} /></Field>
            <label className="flex items-center gap-2 text-sm sm:col-span-2"><Switch checked={v.is_default} onCheckedChange={(x) => setV({ ...v, is_default: x })} /> Use as default</label>
          </div>
          <DialogFooter><Button disabled={!v.name || !v.prefix || create.isPending} onClick={() => create.mutate()}>Create</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

interface Health { status: string; warnings: string[]; worker: { alive: boolean; last_heartbeat: string | null }; backups: { configured: boolean; last_success_at: string | null; last_status: string | null };
  disk: { free_mb: number; total_mb: number } | null; queues: Record<string, { pending?: number; count?: number }> }
interface Backup { id: string; started_at: string; status: string; trigger: string; filename: string | null; size_bytes: number | null; verified: boolean; offsite: string | null; error: string | null; deleted_at: string | null }

function System() {
  const qc = useQueryClient();
  const health = useApiQuery<Health>(["system-health"], "/system/health");
  const backups = useApiQuery<Page<Backup>>(["backups"], "/system/backups", { limit: 10 });
  const maint = useApiQuery<{ enabled: boolean; message?: string }>(["maintenance"], "/system/maintenance");
  const domains = useApiQuery<{ id: string; hostname: string; status: string; dns_record: { type: string; name: string; value: string } }[]>(["domains"], "/system/domains");
  const run = useMutation({ mutationFn: () => api("/system/backups", { body: {} }), onSuccess: () => { toast.success("Backup queued — the worker runs it within a minute"); qc.invalidateQueries({ queryKey: ["backups"] }); }, onError: (e: ApiError) => toast.error(e.message) });
  const setMaint = useMutation({ mutationFn: (enabled: boolean) => api("/system/maintenance", { method: "PUT", body: { enabled } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["maintenance"] }), onError: (e: ApiError) => toast.error(e.message) });
  const [host, setHost] = useState("");
  const addDomain = useMutation({ mutationFn: () => api("/system/domains", { body: { hostname: host } }), onSuccess: () => { setHost(""); qc.invalidateQueries({ queryKey: ["domains"] }); }, onError: (e: ApiError) => toast.error(e.message) });
  const verify = useMutation({ mutationFn: (id: string) => api<{ status: string; last_error: string | null }>(`/system/domains/${id}/verify`, { body: {} }),
    onSuccess: (d) => { toast[d.status === "active" ? "success" : "error"](d.status === "active" ? "Domain verified" : d.last_error ?? "Not verified yet"); qc.invalidateQueries({ queryKey: ["domains"] }); } });

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card className="lg:col-span-2">
        <CardHeader className="flex-row items-center justify-between"><CardTitle>System health</CardTitle>
          {health.data && <Badge variant={health.data.status === "ok" ? "default" : "warning"}>{health.data.status === "ok" ? "All good" : "Needs attention"}</Badge>}</CardHeader>
        <CardContent>
          {health.isLoading ? <Skeleton className="h-20" /> : health.error ? <ErrorState error={health.error} /> : health.data && (
            <div className="grid gap-4 sm:grid-cols-3">
              <div className="sm:col-span-2">
                {health.data.warnings.length === 0 ? <p className="flex items-center gap-2 text-sm"><CheckCircle2Icon className="size-4 text-primary" />Nothing needs attention.</p> : (
                  <ul className="space-y-1.5 text-sm">{health.data.warnings.map((w) => <li key={w} className="flex gap-2"><AlertTriangleIcon className="mt-0.5 size-4 shrink-0 text-warning" />{w}</li>)}</ul>
                )}
              </div>
              <div className="space-y-1 text-sm text-muted-foreground">
                <p>Worker: <span className={health.data.worker.alive ? "text-primary" : "text-destructive"}>{health.data.worker.alive ? "running" : "stopped"}</span></p>
                <p>Last backup: {dateTime(health.data.backups.last_success_at)}</p>
                {health.data.disk && <p>Disk free: {(health.data.disk.free_mb / 1024).toFixed(1)} GB</p>}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex-row items-center justify-between"><div><CardTitle>Backups</CardTitle><CardDescription>Encrypted (AES-256-GCM) and verified after writing.</CardDescription></div>
          <Button size="sm" onClick={() => run.mutate()} disabled={run.isPending}><DatabaseBackupIcon /> Back up now</Button></CardHeader>
        <CardContent className="divide-y p-0">
          {backups.data?.items.map((b) => (
            <div key={b.id} className="flex items-center gap-3 px-5 py-2.5 text-sm">
              <Badge variant={b.status === "succeeded" ? "default" : b.status === "failed" ? "destructive" : "warning"}>{titleCase(b.status)}</Badge>
              <span className="flex-1 truncate">{dateTime(b.started_at)} · {titleCase(b.trigger)}{b.size_bytes ? ` · ${(b.size_bytes / 1048576).toFixed(1)} MB` : ""}{b.error && <span className="text-destructive"> · {b.error}</span>}</span>
              {b.status === "succeeded" && !b.deleted_at && <Button size="icon-sm" variant="ghost" aria-label="Download" onClick={() => openFile(`/system/backups/${b.id}/download`, undefined, b.filename ?? "backup.erpbk")}><DownloadIcon /></Button>}
            </div>
          ))}
          {backups.data?.items.length === 0 && <p className="px-5 py-6 text-sm text-muted-foreground">No backups yet.</p>}
        </CardContent>
      </Card>
      <div className="space-y-5">
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><WrenchIcon className="size-4" /> Maintenance mode</CardTitle><CardDescription>Pauses every change (viewing still works). Used while moving to PostgreSQL.</CardDescription></CardHeader>
          <CardContent><label className="flex items-center justify-between text-sm">{maint.data?.enabled ? "On — changes are paused" : "Off"}<Switch checked={!!maint.data?.enabled} onCheckedChange={(v) => setMaint.mutate(v)} /></label></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><GlobeIcon className="size-4" /> Custom domains</CardTitle><CardDescription>Prove ownership with a DNS TXT record; SSL is issued automatically.</CardDescription></CardHeader>
          <CardContent className="space-y-3">
            {domains.data?.map((d) => (
              <div key={d.id} className="rounded-md border p-2 text-sm">
                <div className="flex items-center justify-between"><span className="font-medium">{d.hostname}</span>
                  <div className="flex items-center gap-2"><Badge variant={d.status === "active" ? "default" : "warning"}>{titleCase(d.status)}</Badge>{d.status !== "active" && <Button size="sm" variant="ghost" onClick={() => verify.mutate(d.id)}>Verify</Button>}</div></div>
                {d.status !== "active" && <p className="mt-1 font-mono text-xs break-all text-muted-foreground">{d.dns_record.type} {d.dns_record.name} → {d.dns_record.value}</p>}
              </div>
            ))}
            <div className="flex gap-2"><Input value={host} onChange={(e) => setHost(e.target.value)} placeholder="billing.yourclinic.in" /><Button variant="outline" disabled={host.length < 4} onClick={() => addDomain.mutate()}>Add</Button></div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
