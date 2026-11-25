"use client";

import { BadgePercentIcon, CheckIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizMutation, useBizQuery } from "@/hooks/use-business";
import { money } from "@/lib/format";

interface Approval {
  id: string; document_id: string; party_name: string | null; amount_payable: string; discount_percent: string; discount_amount: string;
  limit_percent: string; reason: string | null; status: "pending" | "approved" | "rejected"; requested_by: string | null; requested_at: string;
  expires_at: string; note: string | null; code: string | null;
}

export default function ApprovalsPage() {
  const [tab, setTab] = useState("pending");
  const list = useBizQuery<Approval[]>(["discount-approvals", tab], "/discount-approvals", { pending: tab === "pending" }, { refetchInterval: tab === "pending" ? 15_000 : false });
  const decide = useBizMutation<{ id: string; approve: boolean }>("POST", (b) => `/discount-approvals/${b.id}/decide`, { invalidate: [["discount-approvals"]], success: "Done" });
  return (
    <>
      <PageHeader title="Discount approvals" description="Bills with more discount than the biller may give. Approve here, or read the code to the biller — it works once, for 15 minutes." />
      <div className="mb-3"><Tabs value={tab} onValueChange={setTab}><TabsList><TabsTrigger value="pending">Waiting</TabsTrigger><TabsTrigger value="all">History</TabsTrigger></TabsList></Tabs></div>
      {list.error ? <Card><ErrorState error={list.error} retry={() => list.refetch()} /></Card> : list.isLoading ? <Card><TableSkeleton /></Card> : !list.data?.length ? (
        <Card><EmptyState icon={BadgePercentIcon} title="Nothing waiting" description="Set the limit in Settings → Billing. Requests from billers appear here." /></Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {list.data.map((a) => (
            <Card key={a.id}>
              <CardContent className="space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0"><p className="truncate font-semibold">{a.party_name ?? "Walk-in"}</p>
                    <p className="text-xs text-muted-foreground">{a.requested_by} · {new Date(a.requested_at).toLocaleTimeString("en-IN", { timeStyle: "short" })}</p></div>
                  <Badge variant={a.status === "approved" ? "default" : a.status === "rejected" ? "destructive" : "warning"}>{a.status}</Badge>
                </div>
                <p className="text-sm"><span className="text-lg font-semibold">{Number(a.discount_percent)} %</span> off (limit {Number(a.limit_percent)} %) · {money(a.discount_amount)} on a bill of {money(a.amount_payable)}</p>
                {a.reason && <p className="text-sm text-muted-foreground">“{a.reason}”</p>}
                {a.code && <div className="rounded-lg bg-muted p-2 text-center"><p className="text-xs text-muted-foreground">Code to read out</p><p className="num text-2xl font-bold tracking-[0.3em]">{a.code}</p></div>}
                {a.note && <p className="text-xs text-muted-foreground">{a.note}</p>}
                <div className="flex flex-wrap justify-end gap-2">
                  <Button asChild size="sm" variant="ghost"><Link href={`/sales/new?draft=${a.document_id}`}>Open bill</Link></Button>
                  {a.status === "pending" && <>
                    <Button size="sm" variant="outline" className="text-destructive" disabled={decide.isPending} onClick={() => decide.mutate({ id: a.id, approve: false })}><XIcon /> Refuse</Button>
                    <Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate({ id: a.id, approve: true })}><CheckIcon /> Approve</Button></>}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
