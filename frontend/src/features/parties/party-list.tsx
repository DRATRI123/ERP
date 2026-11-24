"use client";

import { DownloadIcon, UserPlusIcon } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { DataTable, type Column } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { EmptyState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PartyDialog } from "@/features/parties/party-form";
import { useBusiness } from "@/hooks/use-business";
import { openFile } from "@/lib/api";
import { money } from "@/lib/format";
import type { Party } from "@/lib/types";

export function PartyList({ kind }: { kind: "customers" | "suppliers" }) {
  const router = useRouter();
  const params = useSearchParams();
  const { can, isOwner, base } = useBusiness();
  const [open, setOpen] = useState(params.get("new") === "1");
  const manage = isOwner || can(`${kind}.manage`);
  const cols: Column<Party>[] = [
    { key: "n", header: "Name", cell: (p) => <div><p className="font-medium">{p.name}</p>{p.email && <p className="text-xs text-muted-foreground">{p.email}</p>}</div>, sortable: true, sortValue: (p) => p.name },
    { key: "g", header: "GSTIN", cell: (p) => p.gstin ? <span className="font-mono text-xs">{p.gstin}</span> : <Badge variant="secondary">Unregistered</Badge>, hideOnMobile: true },
    { key: "p", header: "Phone", cell: (p) => p.phone ?? "—" },
    { key: "s", header: "State", cell: (p) => p.state_code ?? "—", hideOnMobile: true },
    { key: "c", header: "Credit", align: "right", hideOnMobile: true, cell: (p) => p.credit_limit ? `${money(p.credit_limit)}${p.credit_days ? ` · ${p.credit_days}d` : ""}` : p.credit_days ? `${p.credit_days} days` : "—" },
  ];
  return (
    <>
      <PageHeader title={kind === "customers" ? "Customers" : "Suppliers"} description={kind === "customers" ? "People and businesses you sell to." : "Who you buy from."}
        actions={<>
          {(isOwner || can("data.export")) && <Button variant="outline" onClick={() => openFile(`${base}/data/export/${kind}`, { format: "xlsx" }, `${kind}.xlsx`)}><DownloadIcon /> Export</Button>}
          {manage && <Button onClick={() => setOpen(true)}><UserPlusIcon /> New {kind === "customers" ? "customer" : "supplier"}</Button>}
        </>} />
      <DataTable<Party> queryKey={[kind]} path={`/${kind}`} columns={cols} searchPlaceholder="Name, phone, e-mail or GSTIN…"
        onRowClick={(p) => router.push(`/${kind}/${p.id}`)}
        empty={<EmptyState title={`No ${kind} yet`} action={manage && <Button onClick={() => setOpen(true)}>Add one</Button>} />} />
      <PartyDialog kind={kind} open={open} onOpenChange={setOpen} />
    </>
  );
}
