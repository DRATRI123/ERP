"use client";

import { FileTextIcon, FolderOpenIcon, ReceiptTextIcon, ShoppingCartIcon } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/data/page-header";
import { Card, CardContent } from "@/components/ui/card";

export default function DocumentsPage() {
  const links = [
    { href: "/sales", icon: ReceiptTextIcon, title: "Sales documents", text: "Invoices, quotations, orders, challans, credit & debit notes — each with print and PDF." },
    { href: "/purchases", icon: ShoppingCartIcon, title: "Purchase documents", text: "Purchase bills, orders and returns." },
    { href: "/inventory?tab=movements", icon: FileTextIcon, title: "Stock documents", text: "Opening stock, adjustments, transfers and counts." },
  ];
  return (
    <>
      <PageHeader title="Documents" description="Everything the ERP has issued, plus — soon — your own files." />
      <div className="grid gap-4 md:grid-cols-3">
        {links.map((l) => (
          <Link key={l.href} href={l.href}><Card className="h-full transition-colors hover:border-primary/40"><CardContent className="space-y-2">
            <l.icon className="size-6 text-primary" /><p className="font-medium">{l.title}</p><p className="text-sm text-muted-foreground">{l.text}</p></CardContent></Card></Link>
        ))}
      </div>
      <Card className="mt-6 border-dashed"><CardContent className="flex items-start gap-3">
        <FolderOpenIcon className="mt-0.5 size-5 text-muted-foreground" />
        <div className="text-sm"><p className="font-medium">File library — not built yet</p>
          <p className="text-muted-foreground">Uploading your own files (supplier bills, licences, prescriptions) with versions and access control is the next backend module. This page will list them once it exists; nothing here is simulated.</p></div>
      </CardContent></Card>
    </>
  );
}
