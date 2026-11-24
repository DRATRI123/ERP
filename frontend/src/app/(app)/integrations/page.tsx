"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2Icon, CircleAlertIcon, CopyIcon, CreditCardIcon, ExternalLinkIcon, Loader2Icon, MailIcon, MessageCircleIcon,
  MessageSquareIcon, PlugZapIcon, ShoppingBagIcon, type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError, type Page } from "@/lib/api";
import { dateTime, titleCase } from "@/lib/format";

interface FieldSpec { name: string; label: string; secret: boolean; required: boolean; help: string }
interface Provider { code: string; label: string; category: string; capabilities: string[]; webhook: boolean; oauth: boolean; docs: string; fields: FieldSpec[] }
interface Integration { id: string; provider: string; label: string; name: string; status: string; config: Record<string, unknown>; has_credentials: boolean;
  last_tested_at: string | null; last_error: string | null; webhook_url: string | null; capabilities: string[] }
interface Log { id?: string; operation: string; direction?: string; success: boolean; http_status: number | null; detail: string | null; created_at: string; duration_ms?: number }

const ICON: Record<string, LucideIcon> = { communication: MessageCircleIcon, payments: CreditCardIcon, google: MailIcon, commerce: ShoppingBagIcon, sms: MessageSquareIcon };

export default function IntegrationsPage() {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const providers = useBizQuery<Provider[]>(["providers"], "/integrations/providers");
  const list = useBizQuery<Integration[]>(["integrations"], "/integrations");
  const [connect, setConnect] = useState<Provider | null>(null);
  const [logsFor, setLogsFor] = useState<Integration | null>(null);
  const test = useMutation({
    mutationFn: (i: Integration) => api<{ ok: boolean; message?: string; detail?: string }>(`${base}/integrations/${i.id}/test`, { body: {} }),
    onSuccess: (r) => { toast.success(r.message ?? r.detail ?? "Connection works"); qc.invalidateQueries({ queryKey: ["biz", id, "integrations"] }); },
    onError: (e: ApiError) => { toast.error(e.message); qc.invalidateQueries({ queryKey: ["biz", id, "integrations"] }); },
  });
  const disconnect = useMutation({
    mutationFn: (i: Integration) => api(`${base}/integrations/${i.id}/disconnect`, { body: {} }),
    onSuccess: () => { toast.success("Disconnected — stored credentials were deleted"); qc.invalidateQueries({ queryKey: ["biz", id] }); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  async function consent(i: Integration) {
    try { const r = await api<{ consent_url: string }>(`${base}/integrations/${i.id}/consent-url`); window.open(r.consent_url, "_blank", "noopener"); }
    catch (e) { toast.error((e as ApiError).message); }
  }
  const categories = [...new Set((providers.data ?? []).map((p) => p.category))];

  return (
    <>
      <PageHeader title="Integrations" description="WhatsApp, e-mail, SMS, payment gateways, Google and Shopify. Credentials are encrypted on the server and never shown again." />
      {list.error ? <Card><ErrorState error={list.error} /></Card> : (
        <section className="mb-8">
          <h2 className="mb-3 text-sm font-semibold text-muted-foreground uppercase">Connected</h2>
          {list.isLoading ? <Skeleton className="h-28" /> : !list.data?.length ? (
            <Card><EmptyState icon={PlugZapIcon} title="Nothing connected yet" description="Choose a service below to send invoices on WhatsApp, collect payments by link, or back up to Google Drive." /></Card>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {list.data.map((i) => (
                <Card key={i.id}>
                  <CardHeader className="flex-row items-start justify-between gap-2">
                    <div><CardTitle>{i.name}</CardTitle><p className="mt-1 text-xs text-muted-foreground">{i.label}</p></div>
                    <Badge variant={i.status === "connected" ? "default" : i.status === "error" ? "destructive" : "warning"}>{titleCase(i.status)}</Badge>
                  </CardHeader>
                  <CardContent className="space-y-3 text-sm">
                    {i.last_error ? <p className="flex gap-1.5 text-destructive"><CircleAlertIcon className="mt-0.5 size-4 shrink-0" />{i.last_error}</p>
                      : i.last_tested_at && <p className="flex items-center gap-1.5 text-muted-foreground"><CheckCircle2Icon className="size-4 text-primary" />Tested {dateTime(i.last_tested_at)}</p>}
                    {i.webhook_url && (
                      <div className="rounded-md bg-muted p-2 text-xs"><p className="mb-1 font-medium">Webhook URL for the provider</p>
                        <button type="button" className="flex w-full items-center gap-1 text-left font-mono break-all" onClick={() => { void navigator.clipboard.writeText(i.webhook_url!); toast.success("Copied"); }}>
                          {i.webhook_url}<CopyIcon className="size-3 shrink-0" /></button></div>
                    )}
                    <div className="flex flex-wrap gap-2">
                      {i.provider === "google" && i.status !== "connected" && <Button size="sm" onClick={() => consent(i)}><ExternalLinkIcon /> Authorise with Google</Button>}
                      <Button size="sm" variant="outline" onClick={() => test.mutate(i)} disabled={test.isPending}>{test.isPending && test.variables?.id === i.id ? <Loader2Icon className="animate-spin" /> : null}Test</Button>
                      <Button size="sm" variant="outline" onClick={() => setLogsFor(i)}>Activity</Button>
                      <Button size="sm" variant="ghost" className="text-destructive" onClick={() => { if (confirm(`Disconnect ${i.name}?`)) disconnect.mutate(i); }}>Disconnect</Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </section>
      )}
      {categories.map((c) => {
        const Icon = ICON[c] ?? PlugZapIcon;
        return (
          <section key={c} className="mb-6">
            <h2 className="mb-3 text-sm font-semibold text-muted-foreground uppercase">{titleCase(c)}</h2>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {providers.data!.filter((p) => p.category === c).map((p) => (
                <Card key={p.code} className="flex flex-col">
                  <CardHeader className="flex-row items-center gap-3">
                    <span className="flex size-10 items-center justify-center rounded-lg bg-primary-soft text-primary"><Icon className="size-5" /></span>
                    <div><CardTitle>{p.label}</CardTitle><p className="mt-1 text-xs text-muted-foreground">{p.capabilities.map(titleCase).join(" · ")}</p></div>
                  </CardHeader>
                  <CardContent className="flex flex-1 flex-col justify-between gap-3">
                    <p className="line-clamp-3 text-sm text-muted-foreground">{p.docs}</p>
                    <Button variant="outline" onClick={() => setConnect(p)}><PlugZapIcon /> Connect</Button>
                  </CardContent>
                </Card>
              ))}
            </div>
          </section>
        );
      })}
      {connect && <ConnectDialog provider={connect} onClose={() => setConnect(null)} />}
      <Dialog open={!!logsFor} onOpenChange={(v) => !v && setLogsFor(null)}>
        <DialogContent wide>
          <DialogHeader><DialogTitle>{logsFor?.name} — activity</DialogTitle></DialogHeader>
          {logsFor && <Logs integration={logsFor} />}
        </DialogContent>
      </Dialog>
    </>
  );
}

function Logs({ integration }: { integration: Integration }) {
  const logs = useBizQuery<Page<Log>>(["integration-logs", integration.id], `/integrations/${integration.id}/logs`, { limit: 50 });
  if (logs.isLoading) return <Skeleton className="h-40" />;
  if (!logs.data?.items.length) return <p className="py-6 text-center text-sm text-muted-foreground">No calls yet.</p>;
  return (
    <div className="max-h-[60vh] divide-y overflow-y-auto text-sm">
      {logs.data.items.map((l, i) => (
        <div key={l.id ?? i} className="flex items-start gap-3 py-2">
          {l.success ? <CheckCircle2Icon className="mt-0.5 size-4 text-primary" /> : <CircleAlertIcon className="mt-0.5 size-4 text-destructive" />}
          <div className="min-w-0 flex-1"><p className="font-medium">{titleCase(l.operation)}{l.http_status ? ` · HTTP ${l.http_status}` : ""}</p>
            <p className="truncate text-xs text-muted-foreground">{l.detail}</p></div>
          <span className="text-xs whitespace-nowrap text-muted-foreground">{dateTime(l.created_at)}</span>
        </div>
      ))}
    </div>
  );
}

function ConnectDialog({ provider, onClose }: { provider: Provider; onClose: () => void }) {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const [name, setName] = useState(provider.label);
  const [values, setValues] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => {
      const config: Record<string, string> = {}, credentials: Record<string, string> = {};
      for (const f of provider.fields) if (values[f.name]) (f.secret ? credentials : config)[f.name] = values[f.name]!;
      return api<Integration & { consent_url?: string }>(`${base}/integrations`, { body: { provider: provider.code, name, config, credentials } });
    },
    onSuccess: (i) => {
      qc.invalidateQueries({ queryKey: ["biz", id] });
      toast.success(`${i.name} added`);
      if (i.consent_url) window.open(i.consent_url, "_blank", "noopener");
      onClose();
    },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const missing = provider.fields.some((f) => f.required && !values[f.name]);
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Connect {provider.label}</DialogTitle><DialogDescription>{provider.docs}</DialogDescription></DialogHeader>
        <div className="grid gap-4">
          <Field label="Name in the ERP"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          {provider.fields.map((f) => (
            <Field key={f.name} label={f.label} required={f.required} hint={f.help || (f.secret ? "Encrypted at rest; never displayed again" : undefined)}>
              <Input type={f.secret ? "password" : "text"} autoComplete="off" value={values[f.name] ?? ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} />
            </Field>
          ))}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={missing || !name || save.isPending} onClick={() => save.mutate()}>{save.isPending && <Loader2Icon className="animate-spin" />} Connect</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
