"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArchiveIcon, Building2Icon, Loader2Icon, PlusIcon, RotateCcwIcon } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";
import { date, titleCase } from "@/lib/format";
import { GSTIN_RE, STATES } from "@/lib/india";
import { loadMe } from "@/lib/session";
import { useWorkspace } from "@/stores/business";

interface Biz { id: string; name: string; legal_name: string | null; gstin: string | null; state_code: string | null; status: string; created_at: string; inventory_valuation: string }

function BusinessesPage() {
  const params = useSearchParams();
  const { isOwner } = useBusiness();
  const qc = useQueryClient();
  const setBusiness = useWorkspace((s) => s.setBusiness);
  const list = useApiQuery<Biz[]>(["businesses"], "/businesses", { include_archived: true });
  const [open, setOpen] = useState(params.get("new") === "1");
  const [v, setV] = useState({ name: "", legal_name: "", gstin: "", state_code: "27", inventory_valuation: "weighted_average", fy_start_month: "4" });
  const create = useMutation({
    mutationFn: () => api<Biz>("/businesses", { body: { ...v, legal_name: v.legal_name || null, gstin: v.gstin || null, fy_start_month: Number(v.fy_start_month) } }),
    onSuccess: async (b) => { toast.success(`${b.name} created`); await loadMe(); setBusiness(b.id); qc.invalidateQueries(); setOpen(false); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const archive = useMutation({
    mutationFn: (b: Biz) => api(`/businesses/${b.id}/${b.status === "archived" ? "restore" : "archive"}`, { body: {} }),
    onSuccess: async () => { await loadMe(); qc.invalidateQueries({ queryKey: ["businesses"] }); }, onError: (e: ApiError) => toast.error(e.message),
  });
  if (!isOwner) return <EmptyState title="Only the owner manages businesses" />;
  const gstinBad = !!v.gstin && !GSTIN_RE.test(v.gstin);
  return (
    <>
      <PageHeader title="Businesses" description="Each business has its own books, GST, stock, numbering and team." actions={<Button onClick={() => setOpen(true)}><PlusIcon /> New business</Button>} />
      {list.isLoading ? <Skeleton className="h-40" /> : list.error ? <Card><ErrorState error={list.error} /></Card> : !list.data?.length ? (
        <Card><EmptyState icon={Building2Icon} title="No businesses yet" action={<Button onClick={() => setOpen(true)}>Create your first business</Button>} /></Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {list.data.map((b) => (
            <Card key={b.id} className={b.status === "archived" ? "opacity-60" : ""}>
              <CardHeader className="flex-row items-start justify-between"><div><CardTitle>{b.name}</CardTitle><p className="mt-1 text-xs text-muted-foreground">{b.legal_name ?? "—"}</p></div>
                <Badge variant={b.status === "active" ? "default" : "secondary"}>{titleCase(b.status)}</Badge></CardHeader>
              <CardContent className="space-y-3 text-sm">
                <p className="text-muted-foreground">{b.gstin ? <span className="font-mono">{b.gstin}</span> : "No GSTIN"} · since {date(b.created_at)}</p>
                <div className="flex gap-2">
                  {b.status === "active" && <Button size="sm" variant="outline" onClick={() => { setBusiness(b.id); qc.invalidateQueries({ queryKey: ["biz"] }); toast.success(`Switched to ${b.name}`); }}>Open</Button>}
                  <Button size="sm" variant="ghost" onClick={() => (b.status === "archived" || confirm(`Archive ${b.name}? Its data is kept; nobody can use it until restored.`)) && archive.mutate(b)}>
                    {b.status === "archived" ? <><RotateCcwIcon /> Restore</> : <><ArchiveIcon /> Archive</>}</Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>New business</DialogTitle><DialogDescription>A head office, main warehouse, roles, chart of accounts and the current financial year are created for you.</DialogDescription></DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Trade name" className="sm:col-span-2"><Input value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} autoFocus /></Field>
            <Field label="Legal name"><Input value={v.legal_name} onChange={(e) => setV({ ...v, legal_name: e.target.value })} /></Field>
            <Field label="GSTIN" error={gstinBad ? "Not a valid GSTIN" : undefined}><Input className="uppercase" value={v.gstin} onChange={(e) => { const g = e.target.value.toUpperCase(); setV({ ...v, gstin: g, state_code: GSTIN_RE.test(g) ? g.slice(0, 2) : v.state_code }); }} /></Field>
            <Field label="State"><NativeSelect value={v.state_code} onChange={(e) => setV({ ...v, state_code: e.target.value })}>{STATES.map(([c, n]) => <option key={c} value={c}>{n}</option>)}</NativeSelect></Field>
            <Field label="Stock valuation" hint="Cannot change once stock exists"><NativeSelect value={v.inventory_valuation} onChange={(e) => setV({ ...v, inventory_valuation: e.target.value })}><option value="weighted_average">Weighted average</option><option value="fifo">FIFO</option></NativeSelect></Field>
          </div>
          <DialogFooter><Button disabled={v.name.length < 2 || gstinBad || create.isPending} onClick={() => create.mutate()}>{create.isPending && <Loader2Icon className="animate-spin" />} Create business</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export default function Page() {
  return <Suspense><BusinessesPage /></Suspense>;
}
