"use client";

import { GiftIcon, Loader2Icon, PlusIcon, StarIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { StaticTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { money } from "@/lib/format";

interface Overview {
  settings: { earn_per_100: string; point_value: string; min_redeem: string }; active: boolean;
  outstanding_points: string; outstanding_value: string;
  members: { party_id: string; name: string; phone: string | null; points: string; value: string }[];
}

export default function LoyaltyPage() {
  const { can, isOwner } = useBusiness();
  const q = useBizQuery<Overview>(["loyalty"], "/loyalty");
  const [adjusting, setAdjusting] = useState<{ party_id: string; name: string } | null>(null);
  const d = q.data;
  return (
    <>
      <PageHeader title="Loyalty points" description="Customers earn points on every bill and spend them as payment at the counter (the “Points” button). Points on a cancelled bill are taken back." />
      <LoyaltySettings current={d?.settings} editable={isOwner || can("business.settings")} />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:mb-6 sm:gap-4 lg:grid-cols-3">
        <StatCard label="Points with customers" icon={StarIcon} loading={q.isLoading} value={d ? Number(d.outstanding_points).toLocaleString("en-IN") : "—"} />
        <StatCard label="Worth (to honour)" icon={GiftIcon} tone="amber" loading={q.isLoading} value={d ? money(d.outstanding_value) : "—"} />
      </div>
      <Card className="overflow-hidden">
        {q.error ? <ErrorState error={q.error} retry={() => q.refetch()} /> : q.isLoading ? <TableSkeleton /> : !d?.members.length ? (
          <EmptyState icon={StarIcon} title={d?.active ? "No points earned yet" : "Set the earning rate to start"} description="Points are earned on finalized sales invoices that have a customer (not walk-in sales)." />
        ) : (
          <StaticTable rows={d.members} rowKey={(m) => m.party_id} columns={[
            { key: "n", header: "Customer", mobile: "title", cell: (m) => <div><Link href={`/customers/${m.party_id}`} className="font-medium hover:underline">{m.name}</Link>{m.phone && <p className="text-xs text-muted-foreground">{m.phone}</p>}</div> },
            { key: "p", header: "Points", align: "right", cell: (m) => <span className="num">{Number(m.points).toLocaleString("en-IN")}</span> },
            { key: "v", header: "Worth", align: "right", mobile: "aside", cell: (m) => <span className="num font-medium">{money(m.value)}</span> },
            ...((isOwner || can("customers.manage")) ? [{ key: "a", header: "", mobile: "row" as const, cell: (m: Overview["members"][number]) =>
              <Button size="sm" variant="ghost" onClick={() => setAdjusting({ party_id: m.party_id, name: m.name })}><PlusIcon /> Adjust</Button> }] : []),
          ]} />
        )}
      </Card>
      {adjusting && <AdjustDialog who={adjusting} onClose={() => setAdjusting(null)} />}
    </>
  );
}

function LoyaltySettings({ current, editable }: { current?: Overview["settings"]; editable: boolean }) {
  const [edit, setEdit] = useState<Record<string, string> | null>(null);
  const v = edit ?? { earn_per_100: current ? String(Number(current.earn_per_100) || "") : "", point_value: current ? String(Number(current.point_value) || "") : "", min_redeem: current ? String(Number(current.min_redeem) || "") : "" };
  const save = useBizMutation<Record<string, string>>("PUT", "/loyalty/settings", { invalidate: [["loyalty"]], success: "Loyalty settings saved", onSuccess: () => setEdit(null) });
  const pct = Number(v.earn_per_100) * Number(v.point_value);
  const field = (k: string, label: string, hint?: string) => (
    <Field label={label} htmlFor={`ly-${k}`} hint={hint}><Input id={`ly-${k}`} inputMode="decimal" className="num w-36" disabled={!editable} value={v[k]}
      onChange={(e) => setEdit({ ...v, [k]: e.target.value.replace(/[^\d.]/g, "") })} /></Field>
  );
  return (
    <Card className="mb-4"><CardContent className="flex flex-wrap items-end gap-3">
      {field("earn_per_100", "Points per ₹100 billed", "e.g. 1")}
      {field("point_value", "Value of 1 point (₹)", "e.g. 1")}
      {field("min_redeem", "Use from (points)", "0 = any")}
      {editable && edit && <Button disabled={save.isPending} onClick={() => save.mutate({ earn_per_100: v.earn_per_100 || "0", point_value: v.point_value || "0", min_redeem: v.min_redeem || "0" })}>Save</Button>}
      <p className="basis-full text-xs text-muted-foreground">{pct > 0 ? `Customers get back ${pct.toFixed(2)}% of what they spend.` : "Off until both values are set."} Points used are booked as “Loyalty Rewards” (an expense).</p>
    </CardContent></Card>
  );
}

function AdjustDialog({ who, onClose }: { who: { party_id: string; name: string }; onClose: () => void }) {
  const [points, setPoints] = useState("");
  const [note, setNote] = useState("");
  const save = useBizMutation<{ points: string; note: string }>("POST", `/customers/${who.party_id}/loyalty/adjust`, { invalidate: [["loyalty"]], success: "Points adjusted", onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Adjust points — {who.name}</DialogTitle><DialogDescription>Add points (welcome bonus, birthday) or remove them with a minus sign.</DialogDescription></DialogHeader>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Points" htmlFor="adj-p"><Input id="adj-p" inputMode="decimal" className="num" value={points} onChange={(e) => setPoints(e.target.value.replace(/[^\d.-]/g, ""))} placeholder="50 or -20" /></Field>
          <Field label="Reason" htmlFor="adj-n" className="sm:col-span-2"><Input id="adj-n" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!Number(points) || note.trim().length < 3 || save.isPending} onClick={() => save.mutate({ points, note })}>{save.isPending && <Loader2Icon className="animate-spin" />} Save</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
