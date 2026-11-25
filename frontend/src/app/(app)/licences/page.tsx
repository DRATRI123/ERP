"use client";

import {
  BadgeCheckIcon, CalendarClockIcon, FileCheck2Icon, Loader2Icon, PaperclipIcon, PencilIcon, PlusIcon, PrinterIcon,
  ShieldAlertIcon, ShieldCheckIcon, Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/data/confirm";
import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { ApiError } from "@/lib/api";
import { date } from "@/lib/format";
import { ACCEPT, download, uploadFile } from "@/features/files/files";

interface Licence {
  id: string; kind: string; kind_label: string; name: string; number: string; branch_id: string | null;
  issued_by: string | null; issue_date: string | null; expiry_date: string | null; print_on_invoice: boolean;
  file_id: string | null; notes: string | null; is_active: boolean; status: "valid" | "due" | "expired" | "inactive";
  days_left: number | null; updated_at: string;
}
type Draft = Omit<Licence, "id" | "kind_label" | "status" | "days_left" | "updated_at">;

const STATUS: Record<Licence["status"], ["default" | "warning" | "destructive" | "secondary", string]> = {
  valid: ["default", "Valid"], due: ["warning", "Renewal due"], expired: ["destructive", "Expired"], inactive: ["secondary", "Inactive"],
};
const EMPTY: Draft = { kind: "drug_retail", name: "", number: "", branch_id: null, issued_by: "", issue_date: null, expiry_date: null,
  print_on_invoice: true, file_id: null, notes: "", is_active: true };

export default function LicencesPage() {
  const { can, isOwner } = useBusiness();
  const manage = isOwner || can("licences.manage");
  const list = useBizQuery<Licence[]>(["licences"], "/licences");
  const [editing, setEditing] = useState<{ id?: string; draft: Draft; key: string } | null>(null);
  const [deleting, setDeleting] = useState<Licence | null>(null);
  const remove = useBizMutation("DELETE", () => `/licences/${deleting?.id}`, { invalidate: [["licences"]], success: "Licence removed" });
  const rows = list.data ?? [];
  const count = (s: Licence["status"]) => rows.filter((r) => r.status === s).length;
  const open = (draft: Draft, id?: string) => setEditing({ id, draft, key: `${id ?? "new"}-${Date.now()}` });

  return (
    <>
      <PageHeader title="Licences & compliance" description="Drug, FSSAI, AYUSH, trade and other licences — numbers on your invoices and renewal reminders before they lapse."
        actions={manage && <Button onClick={() => open(EMPTY)}><PlusIcon /> Add licence</Button>} />
      <div className="mb-4 sm:mb-6 grid grid-cols-2 gap-3 sm:gap-4 sm:grid-cols-3">
        <StatCard label="Active licences" icon={ShieldCheckIcon} loading={list.isLoading} value={rows.filter((r) => r.is_active).length} />
        <StatCard label="Renewal due (60 days)" icon={CalendarClockIcon} tone={count("due") ? "amber" : "green"} loading={list.isLoading} value={count("due")} />
        <StatCard label="Expired" icon={ShieldAlertIcon} tone={count("expired") ? "red" : "green"} loading={list.isLoading} value={count("expired")} />
      </div>

      {list.error ? <Card><ErrorState error={list.error} retry={() => list.refetch()} /></Card> : list.isLoading ? (
        <div className="grid gap-3 md:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-36" />)}</div>
      ) : rows.length === 0 ? (
        <Card><EmptyState icon={FileCheck2Icon} title="No licences recorded" description="Add your drug licence, FSSAI and others. Marked ones print on every invoice, and you get reminders 90, 60 and 30 days before expiry."
          action={manage && <Button onClick={() => open(EMPTY)}><PlusIcon /> Add licence</Button>} /></Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {rows.map((l) => (
            <Card key={l.id} className={l.is_active ? "" : "opacity-70"}>
              <CardContent className="space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-muted-foreground uppercase">{l.kind_label}</p>
                    <p className="truncate font-semibold">{l.name}</p>
                    <p className="num text-lg">{l.number}</p>
                  </div>
                  <Badge variant={STATUS[l.status][0]}>{STATUS[l.status][1]}</Badge>
                </div>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div><p className="text-xs text-muted-foreground">Valid until</p><p className="font-medium">{l.expiry_date ? date(l.expiry_date) : "No expiry"}</p>
                    {l.days_left !== null && l.is_active && <p className={l.days_left < 0 ? "text-xs text-destructive" : l.days_left <= 60 ? "text-xs text-amber-700 dark:text-warning" : "text-xs text-muted-foreground"}>
                      {l.days_left < 0 ? `${-l.days_left} days ago` : `${l.days_left} days left`}</p>}</div>
                  <div><p className="text-xs text-muted-foreground">Issued by</p><p className="truncate">{l.issued_by || "—"}</p></div>
                </div>
                <div className="flex flex-wrap items-center gap-2 border-t pt-3">
                  {l.print_on_invoice && <Badge variant="blue"><PrinterIcon className="size-3" /> On invoices</Badge>}
                  {l.file_id && <CertificateButton fileId={l.file_id} name={`${l.name} certificate`} />}
                  <span className="flex-1" />
                  {manage && <>
                    <Button size="icon" variant="ghost" aria-label="Edit licence" onClick={() => open({
                      kind: l.kind, name: l.name, number: l.number, branch_id: l.branch_id, issued_by: l.issued_by ?? "", issue_date: l.issue_date,
                      expiry_date: l.expiry_date, print_on_invoice: l.print_on_invoice, file_id: l.file_id, notes: l.notes ?? "", is_active: l.is_active }, l.id)}><PencilIcon /></Button>
                    <Button size="icon" variant="ghost" aria-label="Delete licence" onClick={() => setDeleting(l)}><Trash2Icon /></Button>
                  </>}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <p className="mt-4 text-sm text-muted-foreground">
        <BadgeCheckIcon className="mr-1 inline size-4 text-primary" />
        Renewal reminders: add an automation rule on “A licence nears expiry” in <Link href="/automation" className="text-blue hover:underline">Automation</Link>.
      </p>

      {editing && <LicenceDialog key={editing.key} id={editing.id} initial={editing.draft} onClose={() => setEditing(null)} />}
      <ConfirmDialog open={!!deleting} onOpenChange={(v) => !v && setDeleting(null)} destructive confirm="Delete"
        title={`Delete ${deleting?.name} (${deleting?.number})?`} description="It stops printing on new invoices. Invoices already issued keep it. To keep history, edit it and switch it off instead."
        onConfirm={() => remove.mutateAsync(undefined)} />
    </>
  );
}

function CertificateButton({ fileId, name }: { fileId: string; name: string }) {
  const { base } = useBusiness();
  return <Button size="sm" variant="ghost" onClick={() => download(base, { id: fileId, name })}><PaperclipIcon /> Certificate</Button>;
}

function LicenceDialog({ id, initial, onClose }: { id?: string; initial: Draft; onClose: () => void }) {
  const { base } = useBusiness();
  const kinds = useBizQuery<{ kind: string; label: string }[]>(["licence-kinds"], "/licences/kinds", undefined, { staleTime: Infinity });
  const branches = useBizQuery<{ id: string; name: string }[]>(["branches"], "/branches");
  const [d, setD] = useState<Draft>(initial);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }));
  const save = useBizMutation<Draft>(id ? "PATCH" : "POST", id ? `/licences/${id}` : "/licences",
    { invalidate: [["licences"]], success: id ? "Licence saved" : "Licence added", onSuccess: onClose });
  const nullify = (v: string | null) => (v ? v : null);

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>{id ? "Edit licence" : "Add licence"}</DialogTitle></DialogHeader>
        <form className="grid gap-4" onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ ...d, issued_by: nullify(d.issued_by), notes: nullify(d.notes), issue_date: nullify(d.issue_date), expiry_date: nullify(d.expiry_date) });
        }}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Type" htmlFor="lic-kind"><NativeSelect id="lic-kind" value={d.kind} onChange={(e) => set({ kind: e.target.value })}>
              {kinds.data?.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}</NativeSelect></Field>
            <Field label="Name" htmlFor="lic-name" hint="Leave empty to use the type"><Input id="lic-name" value={d.name} maxLength={120} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Retail drug licence — FC Road" /></Field>
            <Field label="Licence number" htmlFor="lic-no" required><Input id="lic-no" value={d.number} maxLength={80} onChange={(e) => set({ number: e.target.value })} placeholder="20-21/MH/PZ/123456" /></Field>
            <Field label="Issued by" htmlFor="lic-by"><Input id="lic-by" value={d.issued_by ?? ""} maxLength={160} onChange={(e) => set({ issued_by: e.target.value })} placeholder="FDA Maharashtra" /></Field>
            <Field label="Issue date" htmlFor="lic-issue"><Input id="lic-issue" type="date" value={d.issue_date ?? ""} onChange={(e) => set({ issue_date: e.target.value })} /></Field>
            <Field label="Valid until" htmlFor="lic-exp" hint="Empty = does not expire"><Input id="lic-exp" type="date" value={d.expiry_date ?? ""} onChange={(e) => set({ expiry_date: e.target.value })} /></Field>
            <Field label="Applies to" htmlFor="lic-branch"><NativeSelect id="lic-branch" value={d.branch_id ?? ""} onChange={(e) => set({ branch_id: e.target.value || null })}>
              <option value="">All branches</option>{branches.data?.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</NativeSelect></Field>
            <Field label="Certificate">
              <div className="flex items-center gap-2">
                <Button type="button" variant="outline" size="sm" disabled={uploading} onClick={() => fileRef.current?.click()}>
                  {uploading ? <Loader2Icon className="animate-spin" /> : <PaperclipIcon />} {d.file_id ? "Replace" : "Upload"}</Button>
                {d.file_id && <span className="text-sm text-muted-foreground">Attached</span>}
                <input ref={fileRef} type="file" hidden accept={ACCEPT} onChange={async (e) => {
                  const f = e.target.files?.[0]; e.target.value = "";
                  if (!f) return;
                  setUploading(true);
                  try { const up = await uploadFile(base, f, { folder: "/Licences", tags: "licence" }); set({ file_id: up.id }); }
                  catch (err) { toast.error(err instanceof ApiError ? err.message : "Upload failed"); }
                  finally { setUploading(false); }
                }} />
              </div>
            </Field>
          </div>
          <Field label="Notes" htmlFor="lic-notes"><Textarea id="lic-notes" rows={2} value={d.notes ?? ""} onChange={(e) => set({ notes: e.target.value })} /></Field>
          <div className="flex flex-wrap gap-6">
            <label className="flex items-center gap-2 text-sm"><Switch checked={d.print_on_invoice} onCheckedChange={(v) => set({ print_on_invoice: v })} /> Print number on invoices</label>
            <label className="flex items-center gap-2 text-sm"><Switch checked={d.is_active} onCheckedChange={(v) => set({ is_active: v })} /> Active</label>
          </div>
          <DialogFooter><Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!d.number.trim() || save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />}{id ? "Save" : "Add licence"}</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
