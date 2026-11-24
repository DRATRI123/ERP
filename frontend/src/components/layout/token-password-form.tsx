"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2Icon, Loader2Icon } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";

const schema = z.object({
  token: z.string().min(10, "Paste the code from your invitation"),
  password: z.string().min(10, "At least 10 characters"),
  confirm: z.string(),
}).refine((v) => v.password === v.confirm, { message: "Passwords do not match", path: ["confirm"] });

function Inner({ endpoint, title, description, submit }: { endpoint: string; title: string; description: string; submit: string }) {
  const params = useSearchParams();
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const form = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema), defaultValues: { token: params.get("token") ?? "", password: "", confirm: "" } });

  async function onSubmit(v: z.infer<typeof schema>) {
    setError(null);
    try {
      await api(endpoint, { body: { token: v.token, password: v.password } });
      setDone(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not reach the server");
    }
  }

  if (done) {
    return (
      <div className="space-y-4 text-center">
        <CheckCircle2Icon className="mx-auto size-12 text-primary" />
        <h1 className="text-xl font-semibold">All set</h1>
        <Button asChild className="w-full"><Link href="/login">Sign in</Link></Button>
      </div>
    );
  }
  return (
    <form method="post" onSubmit={form.handleSubmit(onSubmit)} className="space-y-5" noValidate>
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      {error && <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</div>}
      {!params.get("token") && (
        <Field label="Code" error={form.formState.errors.token?.message}><Input {...form.register("token")} /></Field>
      )}
      <Field label="New password" error={form.formState.errors.password?.message} hint="At least 10 characters; avoid your name or e-mail.">
        <Input type="password" autoComplete="new-password" {...form.register("password")} />
      </Field>
      <Field label="Repeat password" error={form.formState.errors.confirm?.message}>
        <Input type="password" autoComplete="new-password" {...form.register("confirm")} />
      </Field>
      <Button type="submit" className="w-full" size="lg" disabled={form.formState.isSubmitting}>
        {form.formState.isSubmitting && <Loader2Icon className="animate-spin" />} {submit}
      </Button>
    </form>
  );
}

export function TokenPasswordForm(props: { endpoint: string; title: string; description: string; submit: string }) {
  return <Suspense><Inner {...props} /></Suspense>;
}
