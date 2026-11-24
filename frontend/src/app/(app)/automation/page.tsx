"use client";

import { BellRingIcon, BotIcon, DatabaseBackupIcon, SendIcon, WebhookIcon } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/data/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

export default function AutomationPage() {
  const running = [
    { icon: DatabaseBackupIcon, title: "Daily encrypted backups", text: "With retention, off-site copy and a monthly restore drill.", href: "/settings" },
    { icon: BellRingIcon, title: "Overdue invoice events", text: "Raised daily for unpaid invoices past their due date.", href: "/developer" },
    { icon: SendIcon, title: "Invoice delivery", text: "E-mail, WhatsApp and SMS sending with automatic retries.", href: "/integrations" },
    { icon: WebhookIcon, title: "Webhooks", text: "Signed notifications to your other tools when things change.", href: "/developer" },
  ];
  return (
    <>
      <PageHeader title="Automation" description="Background jobs that already run for you." />
      <div className="grid gap-4 md:grid-cols-2">
        {running.map((r) => (
          <Link key={r.title} href={r.href}><Card className="h-full transition-colors hover:border-primary/40"><CardContent className="flex gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary"><r.icon className="size-5" /></span>
            <div><p className="flex items-center gap-2 font-medium">{r.title}<Badge>Active</Badge></p><p className="text-sm text-muted-foreground">{r.text}</p></div>
          </CardContent></Card></Link>
        ))}
      </div>
      <Card className="mt-6 border-dashed"><CardContent className="flex items-start gap-3">
        <BotIcon className="mt-0.5 size-5 text-muted-foreground" />
        <div className="text-sm"><p className="font-medium">Your own rules — not built yet</p>
          <p className="text-muted-foreground">“When an invoice is 7 days overdue, send a WhatsApp reminder” style rules need the automation engine on the server, which is still to be built. Until then, webhooks can drive external tools.</p></div>
      </CardContent></Card>
    </>
  );
}
