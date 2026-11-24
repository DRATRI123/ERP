"use client";

import { useQueryClient } from "@tanstack/react-query";
import { DownloadIcon, Loader2Icon, PaperclipIcon, Trash2Icon, UploadIcon } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import type { Page } from "@/lib/api";
import { ApiError, api } from "@/lib/api";
import { date } from "@/lib/format";
import { cn } from "@/lib/utils";

import { FileTypeIcon } from "./file-icon";
import { ACCEPT, download, type EntityType, fileSize, type StoredFile, uploadFile } from "./files";

/** Files attached to one record (an invoice, a customer, a product…). Hidden without files.view. */
export function Attachments({ entityType, entityId, folder = "/Attachments", className }: {
  entityType: EntityType; entityId: string; folder?: string; className?: string;
}) {
  const { base, id: bizId, can } = useBusiness();
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const canView = can("files.view");
  const canManage = can("files.view", "files.manage");
  const list = useBizQuery<Page<StoredFile>>(["files", "entity", entityType, entityId], "/files",
    { entity_type: entityType, entity_id: entityId, limit: 50 }, { enabled: canView });
  if (!canView) return null;

  const refresh = () => qc.invalidateQueries({ queryKey: ["biz", bizId, "files"] });
  async function add(list: FileList | File[]) {
    const files = Array.from(list); // a FileList empties when its input is reset
    setBusy(true);
    try {
      for (const f of files) {
        await uploadFile(base, f, { folder, entity_type: entityType, entity_id: entityId });
      }
      toast.success(files.length === 1 ? "File attached" : `${files.length} files attached`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Upload failed");
    } finally {
      setBusy(false);
      refresh();
    }
  }
  async function remove(f: StoredFile) {
    try {
      await api(`${base}/files/${f.id}`, { method: "DELETE" });
      toast.success("Moved to the trash", { description: "Restore it from Documents → Trash." });
      refresh();
    } catch (e) { toast.error(e instanceof ApiError ? e.message : "Could not remove"); }
  }

  const items = list.data?.items ?? [];
  return (
    <Card className={cn("transition-colors", over && "border-primary bg-primary-soft/40", className)}
      onDragOver={(e) => { if (canManage) { e.preventDefault(); setOver(true); } }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); if (canManage && e.dataTransfer.files.length) add(e.dataTransfer.files); }}>
      <CardHeader className="flex-row items-center justify-between pb-3">
        <CardTitle className="flex items-center gap-2"><PaperclipIcon className="size-4 text-muted-foreground" /> Attachments
          {items.length > 0 && <span className="text-sm font-normal text-muted-foreground">{items.length}</span>}</CardTitle>
        {canManage && <>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => input.current?.click()}>
            {busy ? <Loader2Icon className="animate-spin" /> : <UploadIcon />} Attach</Button>
          <input ref={input} type="file" multiple hidden accept={ACCEPT}
            onChange={(e) => { if (e.target.files?.length) add(e.target.files); e.target.value = ""; }} />
        </>}
      </CardHeader>
      <CardContent className="pt-0">
        {list.isLoading ? <Skeleton className="h-12" /> : items.length === 0 ? (
          <p className="rounded-md border border-dashed px-3 py-4 text-center text-sm text-muted-foreground">
            {canManage ? "Drop files here, or use Attach — bills, prescriptions, photos, agreements." : "No files attached."}</p>
        ) : (
          <ul className="divide-y">
            {items.map((f) => (
              <li key={f.id} className="flex items-center gap-3 py-2">
                <FileTypeIcon contentType={f.content_type} className="size-8" />
                <div className="min-w-0 flex-1">
                  <Link href={`/documents?file=${f.id}`} className="block truncate text-sm font-medium hover:underline">{f.name}</Link>
                  <p className="text-xs text-muted-foreground">{fileSize(f.size)} · {date(f.updated_at)}{f.current_version > 1 && ` · v${f.current_version}`}</p>
                </div>
                <Button size="icon" variant="ghost" aria-label={`Download ${f.name}`} onClick={() => download(base, f)}><DownloadIcon /></Button>
                {canManage && <Button size="icon" variant="ghost" aria-label={`Remove ${f.name}`} onClick={() => remove(f)}><Trash2Icon /></Button>}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
