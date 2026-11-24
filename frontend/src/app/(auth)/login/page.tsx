"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRoundIcon, Loader2Icon, LogInIcon, ShieldIcon } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { loadMe } from "@/lib/session";
import { useAuth } from "@/stores/auth";

const schema = z.object({
  email: z.email("Enter a valid e-mail address"),
  password: z.string().min(1, "Enter your password"),
});

interface LoginOut { access_token?: string; refresh_token?: string; mfa_required?: boolean; mfa_token?: string }

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next");
  const [error, setError] = useState<string | null>(null);
  const [mfa, setMfa] = useState<string | null>(null);
  const [useRecovery, setUseRecovery] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const form = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema), defaultValues: { email: "", password: "" } });

  async function finish(out: LoginOut) {
    useAuth.getState().setTokens(out.access_token!, out.refresh_token ?? null);
    await loadMe();
    router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard");
  }

  async function onSubmit(values: z.infer<typeof schema>) {
    setError(null);
    setBusy(true);
    try {
      const out = await api<LoginOut>("/auth/login", { body: values });
      if (out.mfa_required) setMfa(out.mfa_token!);
      else await finish(out);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  async function onMfa(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const out = await api<LoginOut>("/auth/login/mfa", {
        body: { mfa_token: mfa, ...(useRecovery ? { recovery_code: code } : { code }) },
      });
      await finish(out);
    } catch (err) {
      if (err instanceof ApiError && err.code === "mfa_token_invalid") {
        setMfa(null);
        setCode("");
      }
      setError(err instanceof ApiError ? err.message : "Could not reach the server");
    } finally {
      setBusy(false);
    }
  }

  if (mfa) {
    return (
      <form method="post" onSubmit={onMfa} className="space-y-6">
        <div className="space-y-2">
          <div className="flex size-11 items-center justify-center rounded-full bg-primary-soft text-primary"><ShieldIcon className="size-5" /></div>
          <h1 className="text-2xl font-semibold tracking-tight">Two-step verification</h1>
          <p className="text-sm text-muted-foreground">
            {useRecovery ? "Enter one of your recovery codes." : "Enter the 6-digit code from your authenticator app."}
          </p>
        </div>
        <Field label={useRecovery ? "Recovery code" : "Code"} error={error ?? undefined}>
          <Input autoFocus value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code"
            inputMode={useRecovery ? "text" : "numeric"} placeholder={useRecovery ? "xxxxx-xxxxx" : "123 456"}
            className="h-11 text-center text-lg tracking-[0.3em]" maxLength={useRecovery ? 20 : 7} />
        </Field>
        <Button type="submit" size="lg" className="w-full" disabled={busy || code.length < 6}>
          {busy ? <Loader2Icon className="animate-spin" /> : <KeyRoundIcon />} Verify
        </Button>
        <button type="button" className="w-full text-center text-sm text-blue hover:underline"
          onClick={() => { setUseRecovery(!useRecovery); setCode(""); setError(null); }}>
          {useRecovery ? "Use the authenticator app instead" : "Lost your phone? Use a recovery code"}
        </button>
      </form>
    );
  }

  return (
    <form method="post" onSubmit={form.handleSubmit(onSubmit)} className="space-y-6" noValidate>
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Welcome back</h1>
        <p className="text-sm text-muted-foreground">Sign in to manage your businesses.</p>
      </div>
      {error && <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</div>}
      <div className="space-y-4">
        <Field label="E-mail" htmlFor="email" error={form.formState.errors.email?.message}>
          <Input id="email" type="email" autoComplete="username" autoFocus className="h-10" {...form.register("email")} />
        </Field>
        <Field label="Password" htmlFor="password" error={form.formState.errors.password?.message}>
          <Input id="password" type="password" autoComplete="current-password" className="h-10" {...form.register("password")} />
        </Field>
      </div>
      <Button type="submit" size="lg" className="w-full" disabled={busy}>
        {busy ? <Loader2Icon className="animate-spin" /> : <LogInIcon />} Sign in
      </Button>
      <p className="text-center text-xs text-muted-foreground">
        Accounts are created by invitation. Forgot your password? Ask the owner for a reset link.
      </p>
    </form>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
