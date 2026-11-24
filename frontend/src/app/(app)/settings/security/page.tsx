"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2Icon, Loader2Icon, LogOutIcon, MonitorSmartphoneIcon, ShieldAlertIcon, ShieldCheckIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/data/page-header";
import { SecretReveal } from "@/components/data/secret-reveal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery } from "@/hooks/use-business";
import { api, ApiError, type Page } from "@/lib/api";
import { dateTime, titleCase } from "@/lib/format";
import { loadMe } from "@/lib/session";

interface MfaStatus { enabled: boolean; enabled_at: string | null; recovery_codes_left: number }
interface Session { id: string; user_agent: string | null; ip_address: string | null; last_used_at: string; current?: boolean }
interface SecEvent { id: string; kind: string; detail: Record<string, string>; created_at: string }

export default function SecurityPage() {
  const qc = useQueryClient();
  const mfa = useApiQuery<MfaStatus>(["mfa"], "/auth/2fa");
  const sessions = useApiQuery<Session[]>(["sessions"], "/auth/sessions");
  const events = useApiQuery<Page<SecEvent>>(["security-events"], "/auth/security-events", { limit: 10 });
  const [setup, setSetup] = useState<{ secret: string; qr_svg: string } | null>(null);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string | null>(null);
  const [pw, setPw] = useState({ current: "", next: "", repeat: "" });
  const refresh = () => qc.invalidateQueries({ queryKey: ["mfa"] });

  const begin = useMutation({ mutationFn: () => api<{ secret: string; qr_svg: string }>("/auth/2fa/setup", { body: { password } }),
    onSuccess: (r) => { setSetup(r); setPassword(""); }, onError: (e: ApiError) => toast.error(e.message) });
  const enable = useMutation({ mutationFn: () => api<{ recovery_codes: string[] }>("/auth/2fa/enable", { body: { code } }),
    onSuccess: (r) => { setCodes(r.recovery_codes.join("\n")); setSetup(null); setCode(""); refresh(); void loadMe(); toast.success("Two-factor authentication is on"); },
    onError: (e: ApiError) => toast.error(e.message) });
  const disable = useMutation({ mutationFn: () => api("/auth/2fa/disable", { body: { password, code } }),
    onSuccess: () => { setPassword(""); setCode(""); refresh(); void loadMe(); toast.success("Two-factor authentication turned off"); }, onError: (e: ApiError) => toast.error(e.message) });
  const regen = useMutation({ mutationFn: () => api<{ recovery_codes: string[] }>("/auth/2fa/recovery-codes", { body: { password, code } }),
    onSuccess: (r) => { setCodes(r.recovery_codes.join("\n")); setPassword(""); setCode(""); refresh(); }, onError: (e: ApiError) => toast.error(e.message) });
  const revoke = useMutation({ mutationFn: (id: string) => api(`/auth/sessions/${id}`, { method: "DELETE" }),
    onSuccess: () => { toast.success("Signed out on that device"); qc.invalidateQueries({ queryKey: ["sessions"] }); }, onError: (e: ApiError) => toast.error(e.message) });
  const change = useMutation({ mutationFn: () => api("/auth/change-password", { body: { current_password: pw.current, new_password: pw.next } }),
    onSuccess: () => { toast.success("Password changed — other devices were signed out"); setPw({ current: "", next: "", repeat: "" }); }, onError: (e: ApiError) => toast.error(e.message) });

  return (
    <>
      <PageHeader title="Security" description="Two-factor sign-in, devices and your password." />
      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">{mfa.data?.enabled ? <ShieldCheckIcon className="size-5 text-primary" /> : <ShieldAlertIcon className="size-5 text-warning" />}Two-factor authentication</CardTitle>
            <CardDescription>A 6-digit code from Google Authenticator, Microsoft Authenticator or similar, on top of your password.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {mfa.isLoading ? <Skeleton className="h-20" /> : mfa.data?.enabled ? (
              <>
                <p className="flex items-center gap-2 text-sm"><Badge>On</Badge> since {dateTime(mfa.data.enabled_at)} · {mfa.data.recovery_codes_left} recovery codes left</p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Password"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></Field>
                  <Field label="Code from the app"><Input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" maxLength={6} /></Field>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" disabled={!password || code.length < 6 || regen.isPending} onClick={() => regen.mutate()}>New recovery codes</Button>
                  <Button variant="destructive" disabled={!password || code.length < 6 || disable.isPending} onClick={() => disable.mutate()}>Turn off</Button>
                </div>
              </>
            ) : setup ? (
              <div className="grid gap-4 sm:grid-cols-[180px_1fr]">
                {/* The QR SVG is generated by our own server (segno), not user content. */}
                <div className="rounded-lg border bg-white p-2 [&_svg]:h-auto [&_svg]:w-full" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
                <div className="space-y-3 text-sm">
                  <p>1. Scan the QR code with your authenticator app (or enter the key below).</p>
                  <p className="rounded bg-muted px-2 py-1 font-mono text-xs break-all">{setup.secret}</p>
                  <p>2. Enter the 6-digit code it shows.</p>
                  <div className="flex gap-2"><Input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" maxLength={6} placeholder="123456" className="w-32 text-center tracking-widest" />
                    <Button disabled={code.length < 6 || enable.isPending} onClick={() => enable.mutate()}>{enable.isPending && <Loader2Icon className="animate-spin" />} Turn on</Button></div>
                </div>
              </div>
            ) : (
              <div className="flex items-end gap-2">
                <Field label="Confirm your password to start" className="flex-1"><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></Field>
                <Button disabled={!password || begin.isPending} onClick={() => begin.mutate()}>{begin.isPending && <Loader2Icon className="animate-spin" />} Set up</Button>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Change password</CardTitle><CardDescription>At least 10 characters. Other devices are signed out.</CardDescription></CardHeader>
          <CardContent className="grid gap-3">
            <Field label="Current password"><Input type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></Field>
            <Field label="New password"><Input type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></Field>
            <Field label="Repeat new password" error={pw.repeat && pw.repeat !== pw.next ? "Doesn't match" : undefined}><Input type="password" autoComplete="new-password" value={pw.repeat} onChange={(e) => setPw({ ...pw, repeat: e.target.value })} /></Field>
            <Button className="justify-self-start" disabled={!pw.current || pw.next.length < 10 || pw.next !== pw.repeat || change.isPending} onClick={() => change.mutate()}>Change password</Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Signed-in devices</CardTitle></CardHeader>
          <CardContent className="divide-y p-0">
            {sessions.data?.map((s) => (
              <div key={s.id} className="flex items-center gap-3 px-5 py-3 text-sm">
                <MonitorSmartphoneIcon className="size-5 text-muted-foreground" />
                <div className="min-w-0 flex-1"><p className="truncate">{s.user_agent ?? "Unknown device"}</p><p className="text-xs text-muted-foreground">{s.ip_address} · active {dateTime(s.last_used_at)}</p></div>
                {s.current ? <Badge>This device</Badge> : <Button size="sm" variant="ghost" onClick={() => revoke.mutate(s.id)}><LogOutIcon /> Sign out</Button>}
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Security events</CardTitle><CardDescription>New devices, lockouts and 2FA changes on your account.</CardDescription></CardHeader>
          <CardContent className="divide-y p-0">
            {events.data?.items.length === 0 && <p className="px-5 py-6 text-sm text-muted-foreground"><CheckCircle2Icon className="mr-1 inline size-4 text-primary" />Nothing unusual.</p>}
            {events.data?.items.map((e) => (
              <div key={e.id} className="px-5 py-2.5 text-sm"><p className="font-medium">{titleCase(e.kind)}</p>
                <p className="truncate text-xs text-muted-foreground">{dateTime(e.created_at)} · {e.detail.ip} · {e.detail.user_agent}</p></div>
            ))}
          </CardContent>
        </Card>
      </div>
      <SecretReveal title="Recovery codes" secret={codes} onClose={() => setCodes(null)}
        note="Each code signs you in once if you lose your phone. Print them or keep them in a password manager — they won't be shown again." />
    </>
  );
}
