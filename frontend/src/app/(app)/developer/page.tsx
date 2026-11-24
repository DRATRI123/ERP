"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpenIcon, ExternalLinkIcon, KeyRoundIcon, Loader2Icon, PlusIcon, RefreshCwIcon, SendIcon, WebhookIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { DataTable, type Column } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { SecretReveal } from "@/components/data/secret-reveal";
import { EmptyState, ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApiQuery, useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError, type Page } from "@/lib/api";
import { dateTime, titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Key { id: string; prefix: string; label: string | null; created_at: string; expires_at: string | null; last_used_at: string | null; revoked_at: string | null }
interface App { id: string; name: string; description: string | null; business_ids: string[]; scopes: string[]; rate_limit_per_minute: number; is_active: boolean; created_at: string; keys: Key[] }
interface Endpoint { id: string; url: string; description: string | null; events: string[]; status: string; disabled_reason: string | null; consecutive_failures: number; created_at: string }
interface Delivery { id: string; event_type: string; status: string; attempts: number; response_status: number | null; error: string | null; duration_ms: number | null; last_attempt_at: string | null; next_attempt_at: string | null }
interface ReqLog { method: string; path: string; status: number; error_code: string | null; duration_ms: number; key_prefix: string | null; created_at: string; request_id: string }

export default function DeveloperPage() {
  return (
    <>
      <PageHeader title="Developer API & webhooks" description="Connect your own tools: scoped API keys, signed webhooks, logs and documentation."
        actions={<Button variant="outline" asChild><a href="/api/v1/docs" target="_blank" rel="noopener"><BookOpenIcon /> API reference <ExternalLinkIcon className="size-3" /></a></Button>} />
      <Tabs defaultValue="apps">
        <TabsList><TabsTrigger value="apps">Applications & keys</TabsTrigger><TabsTrigger value="webhooks">Webhooks</TabsTrigger><TabsTrigger value="docs">Quick start</TabsTrigger></TabsList>
        <TabsContent value="apps"><Apps /></TabsContent>
        <TabsContent value="webhooks"><Webhooks /></TabsContent>
        <TabsContent value="docs"><QuickStart /></TabsContent>
      </Tabs>
    </>
  );
}

function Apps() {
  const qc = useQueryClient();
  const apps = useApiQuery<App[]>(["dev-apps"], "/developer/apps");
  const [create, setCreate] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const keyAction = useMutation({
    mutationFn: ({ path }: { path: string }) => api<{ key?: string }>(path, { body: {} }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["dev-apps"] }); if (r.key) setSecret(r.key); else toast.success("Key revoked"); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  if (apps.isLoading) return <Skeleton className="h-40" />;
  if (apps.error) return <Card><ErrorState error={apps.error} /></Card>;
  const app = apps.data?.find((a) => a.id === selected);
  return (
    <div className="space-y-4">
      <div className="flex justify-end"><Button onClick={() => setCreate(true)}><PlusIcon /> New application</Button></div>
      {!apps.data?.length ? <Card><EmptyState icon={KeyRoundIcon} title="No API applications" description="Create one per integration (for example “Website orders”), give it only the scopes it needs, then issue a key." /></Card> : (
        <div className="grid gap-4 lg:grid-cols-2">
          {apps.data.map((a) => (
            <Card key={a.id} className={cn(!a.is_active && "opacity-60")}>
              <CardHeader className="flex-row items-start justify-between">
                <div><CardTitle>{a.name}</CardTitle><p className="mt-1 text-xs text-muted-foreground">{a.rate_limit_per_minute}/min · {a.business_ids.length} business{a.business_ids.length === 1 ? "" : "es"}</p></div>
                <Badge variant={a.is_active ? "default" : "secondary"}>{a.is_active ? "Active" : "Disabled"}</Badge>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap gap-1">{a.scopes.map((s) => <Badge key={s} variant="outline" className="font-mono">{s}</Badge>)}</div>
                <div className="divide-y rounded-md border text-sm">
                  {a.keys.length === 0 && <p className="p-2 text-muted-foreground">No keys yet.</p>}
                  {a.keys.map((k) => (
                    <div key={k.id} className="flex items-center gap-2 p-2">
                      <span className="font-mono text-xs">erp_{k.prefix}_••••</span>
                      <span className="flex-1 truncate text-xs text-muted-foreground">{k.revoked_at ? "Revoked" : k.last_used_at ? `Used ${dateTime(k.last_used_at)}` : "Never used"}</span>
                      {!k.revoked_at && <>
                        <Button size="sm" variant="ghost" onClick={() => keyAction.mutate({ path: `/developer/apps/${a.id}/keys/${k.id}/rotate` })}><RefreshCwIcon /> Rotate</Button>
                        <Button size="sm" variant="ghost" className="text-destructive" onClick={() => confirm("Revoke this key? Requests using it fail immediately.") && keyAction.mutate({ path: `/developer/apps/${a.id}/keys/${k.id}/revoke` })}>Revoke</Button>
                      </>}
                    </div>
                  ))}
                </div>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => keyAction.mutate({ path: `/developer/apps/${a.id}/keys` })}><KeyRoundIcon /> New key</Button>
                  <Button size="sm" variant="outline" onClick={() => setSelected(a.id)}>Request log</Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      {app && <AppLogs app={app} onClose={() => setSelected(null)} />}
      <CreateApp open={create} onOpenChange={setCreate} />
      <SecretReveal title="Your new API key" secret={secret} onClose={() => setSecret(null)} note="Send it as “Authorization: Bearer <key>”. It is stored only as a hash — copy it now." />
    </div>
  );
}

function AppLogs({ app, onClose }: { app: App; onClose: () => void }) {
  const logs = useQuery({ queryKey: ["dev-logs", app.id], queryFn: () => api<Page<ReqLog>>(`/developer/apps/${app.id}/logs`, { query: { limit: 100 } }) });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>{app.name} — recent requests</DialogTitle></DialogHeader>
        {logs.isLoading ? <Skeleton className="h-40" /> : !logs.data?.items.length ? <p className="py-6 text-center text-sm text-muted-foreground">No requests yet.</p> : (
          <div className="max-h-[60vh] overflow-y-auto font-mono text-xs">
            {logs.data.items.map((l) => (
              <div key={l.request_id + l.created_at} className="flex items-center gap-3 border-b py-1.5">
                <Badge variant={l.status < 400 ? "default" : l.status < 500 ? "warning" : "destructive"}>{l.status}</Badge>
                <span className="w-14">{l.method}</span><span className="flex-1 truncate">{l.path}</span>
                <span className="text-muted-foreground">{l.duration_ms} ms</span><span className="text-muted-foreground">{dateTime(l.created_at)}</span>
              </div>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function CreateApp({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const qc = useQueryClient();
  const { businesses, id } = useBusiness();
  const scopes = useApiQuery<{ scope: string; description: string }[]>(["dev-scopes"], "/developer/scopes", undefined, { enabled: open });
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [biz, setBiz] = useState<string[]>([id]);
  const save = useMutation({
    mutationFn: () => api("/developer/apps", { body: { name, scopes: picked, business_ids: biz } }),
    onSuccess: () => { toast.success("Application created — now issue a key"); qc.invalidateQueries({ queryKey: ["dev-apps"] }); onOpenChange(false); setName(""); setPicked([]); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>New API application</DialogTitle></DialogHeader>
        <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Website orders" /></Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <div><p className="mb-2 text-sm font-medium">Businesses</p>
            {businesses.map((b) => <label key={b.business_id} className="flex items-center gap-2 py-1 text-sm"><Checkbox checked={biz.includes(b.business_id)}
              onCheckedChange={(v) => setBiz((s) => v ? [...s, b.business_id] : s.filter((x) => x !== b.business_id))} />{b.business_name}</label>)}</div>
          <div><p className="mb-2 text-sm font-medium">Scopes <span className="font-normal text-muted-foreground">(least privilege)</span></p>
            <div className="max-h-64 overflow-y-auto pr-1">{scopes.data?.map((s) => (
              <label key={s.scope} className="flex items-start gap-2 py-1 text-sm"><Checkbox className="mt-0.5" checked={picked.includes(s.scope)}
                onCheckedChange={(v) => setPicked((p) => v ? [...p, s.scope] : p.filter((x) => x !== s.scope))} />
                <span><span className="font-mono text-xs">{s.scope}</span><span className="block text-xs text-muted-foreground">{s.description}</span></span></label>))}</div></div>
        </div>
        <DialogFooter><Button disabled={!name || !picked.length || !biz.length || save.isPending} onClick={() => save.mutate()}>{save.isPending && <Loader2Icon className="animate-spin" />} Create</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Webhooks() {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const eps = useBizQuery<Endpoint[]>(["webhooks"], "/webhooks");
  const events = useApiQuery<{ type: string; description: string }[]>(["dev-events"], "/developer/events");
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [secret, setSecret] = useState<string | null>(null);
  const [selected, setSelected] = useState<Endpoint | null>(null);
  const create = useMutation({
    mutationFn: () => api<{ secret: string }>(`${base}/webhooks`, { body: { url, events: picked } }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["biz", id, "webhooks"] }); setOpen(false); setSecret(r.secret); setUrl(""); setPicked([]); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const act = useMutation({
    mutationFn: ({ path }: { path: string }) => api<{ secret?: string }>(`${base}${path}`, { body: {} }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["biz", id] }); if (r.secret) setSecret(r.secret); else toast.success("Test event queued"); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  return (
    <div className="space-y-4">
      <div className="flex justify-end"><Button onClick={() => setOpen(true)}><PlusIcon /> Add endpoint</Button></div>
      {eps.isLoading ? <Skeleton className="h-32" /> : eps.error ? <Card><ErrorState error={eps.error} /></Card> : !eps.data?.length ? (
        <Card><EmptyState icon={WebhookIcon} title="No webhook endpoints" description="Get a signed HTTPS POST when invoices, payments or stock change. Private network addresses are refused." /></Card>
      ) : eps.data.map((e) => (
        <Card key={e.id}>
          <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-sm">{e.url}</p>
              <div className="mt-1 flex flex-wrap gap-1">{e.events.map((t) => <Badge key={t} variant="outline">{t}</Badge>)}</div>
              {e.disabled_reason && <p className="mt-1 text-xs text-destructive">{e.disabled_reason}</p>}
            </div>
            <Badge variant={e.status === "active" ? "default" : "destructive"}>{titleCase(e.status)}</Badge>
            <div className="flex gap-1">
              <Button size="sm" variant="outline" onClick={() => act.mutate({ path: `/webhooks/${e.id}/test` })}><SendIcon /> Test</Button>
              <Button size="sm" variant="outline" onClick={() => setSelected(e)}>Deliveries</Button>
              <Button size="sm" variant="ghost" onClick={() => act.mutate({ path: `/webhooks/${e.id}/rotate-secret` })}>Rotate secret</Button>
            </div>
          </CardContent>
        </Card>
      ))}
      {selected && <Deliveries endpoint={selected} onClose={() => setSelected(null)} />}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent wide>
          <DialogHeader><DialogTitle>Add webhook endpoint</DialogTitle></DialogHeader>
          <Field label="HTTPS URL"><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/erp-webhook" /></Field>
          <div><p className="mb-2 text-sm font-medium">Events</p><div className="grid max-h-64 gap-1 overflow-y-auto sm:grid-cols-2">{events.data?.map((ev) => (
            <label key={ev.type} className="flex items-start gap-2 text-sm"><Checkbox className="mt-0.5" checked={picked.includes(ev.type)} onCheckedChange={(v) => setPicked((p) => v ? [...p, ev.type] : p.filter((x) => x !== ev.type))} />
              <span><span className="font-mono text-xs">{ev.type}</span><span className="block text-xs text-muted-foreground">{ev.description}</span></span></label>))}</div></div>
          <DialogFooter><Button disabled={!url.startsWith("https://") || !picked.length || create.isPending} onClick={() => create.mutate()}>{create.isPending && <Loader2Icon className="animate-spin" />} Add endpoint</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <SecretReveal title="Signing secret" secret={secret} onClose={() => setSecret(null)} note="Verify the ERP-Signature header (t=…,v1=HMAC-SHA256 of “t.body”) with this secret — see docs/api.md. Shown only once." />
    </div>
  );
}

function Deliveries({ endpoint, onClose }: { endpoint: Endpoint; onClose: () => void }) {
  const { base } = useBusiness();
  const qc = useQueryClient();
  const cols: Column<Delivery>[] = [
    { key: "e", header: "Event", cell: (d) => <span className="font-mono text-xs">{d.event_type}</span> },
    { key: "s", header: "Status", cell: (d) => <Badge variant={d.status === "succeeded" ? "default" : d.status === "failed" ? "destructive" : "warning"}>{titleCase(d.status)}{d.response_status ? ` · ${d.response_status}` : ""}</Badge> },
    { key: "a", header: "Attempts", align: "right", cell: (d) => d.attempts },
    { key: "t", header: "Last attempt", cell: (d) => dateTime(d.last_attempt_at) },
    { key: "r", header: "", cell: (d) => d.status === "failed" ? <Button size="sm" variant="ghost" onClick={async () => { await api(`${base}/webhooks/deliveries/${d.id}/retry`, { body: {} }); toast.success("Retry queued"); qc.invalidateQueries({ queryKey: ["biz"] }); }}>Retry</Button> : null },
  ];
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>Deliveries</DialogTitle></DialogHeader>
        <DataTable<Delivery> queryKey={["deliveries", endpoint.id]} path={`/webhooks/${endpoint.id}/deliveries`} columns={cols} searchParam={null} pageSize={15} />
      </DialogContent>
    </Dialog>
  );
}

function QuickStart() {
  const { id } = useBusiness();
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  return (
    <Card>
      <CardContent className="prose-sm space-y-4 text-sm">
        <p>Every request needs an API key from an application above. Keys can only do what their scopes allow — and never more than the person who created them.</p>
        <pre className="overflow-x-auto rounded-lg bg-muted p-4 font-mono text-xs">{`curl ${origin}/api/v1/businesses/${id}/customers?limit=5 \\
  -H "Authorization: Bearer erp_xxxxxxxx_yyyyyyyy"`}</pre>
        <p>Send an <code className="rounded bg-muted px-1">Idempotency-Key</code> header on POST requests so retries never create duplicates. Rate-limit headers tell you how many requests remain.</p>
        <p>The full, interactive reference is at <a className="text-blue underline" href="/api/v1/docs" target="_blank" rel="noopener">/api/v1/docs</a>; the guide with Python and JavaScript examples is <span className="font-mono">docs/api.md</span> in the repository.</p>
      </CardContent>
    </Card>
  );
}
