"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRightIcon, DownloadIcon, FileTextIcon, FolderIcon, FolderOpenIcon, FolderPlusIcon, HardDriveIcon, LayoutGridIcon,
  LinkIcon, ListIcon, Loader2Icon, ReceiptTextIcon, RotateCcwIcon, SearchIcon, ShoppingCartIcon, Trash2Icon, UploadCloudIcon,
  UploadIcon, XIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/data/confirm";
import { PageHeader } from "@/components/data/page-header";
import { EmptyState, ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, Sheet } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { useDebounced } from "@/hooks/use-debounce";
import { api, ApiError, type Page } from "@/lib/api";
import { date, dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { FileTypeIcon } from "@/features/files/file-icon";
import {
  ACCEPT, download, fileBlob, type FileDetail, fileSize, type FolderInfo, kind, type StoredFile, uploadFile, uploadVersion,
} from "@/features/files/files";

const TRASH = "__trash__";
const LINK_HREF: Record<string, (id: string) => string> = {
  document: (id) => `/documents/${id}`, product: (id) => `/inventory/products/${id}`,
};

function useFolderTree(folders: FolderInfo["folders"], extra: string[]) {
  return useMemo(() => {
    const all = new Map<string, number>();
    for (const f of folders) all.set(f.folder, f.files);
    for (const f of extra) if (!all.has(f)) all.set(f, 0);
    // Make every ancestor visible so the tree has no gaps.
    for (const f of [...all.keys()]) {
      const parts = f.split("/").filter(Boolean);
      for (let i = 1; i < parts.length; i++) {
        const p = "/" + parts.slice(0, i).join("/");
        if (!all.has(p)) all.set(p, 0);
      }
    }
    all.delete("/");
    return [...all.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([folder, files]) => ({
      folder, files, depth: folder.split("/").filter(Boolean).length - 1, name: folder.split("/").pop() ?? folder,
    }));
  }, [folders, extra]);
}

function LibraryPage() {
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const { base, id: bizId, can } = useBusiness();
  const canManage = can("files.view", "files.manage");
  const [folder, setFolder] = useState<string>("/"); // "/" = all files; TRASH = trash
  const [newFolders, setNewFolders] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [view, setView] = useState<"list" | "grid">("list");
  const [limit, setLimit] = useState(60);
  const [over, setOver] = useState(false);
  const [uploading, setUploading] = useState<{ done: number; total: number } | null>(null);
  const [folderDialog, setFolderDialog] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const q = useDebounced(search);
  const openId = params.get("file");
  const trash = folder === TRASH;

  const info = useBizQuery<FolderInfo>(["files", "folders"], "/files/folders");
  const list = useBizQuery<Page<StoredFile>>(["files", "list"], "/files", {
    q, limit, trash: trash || undefined, folder: trash || folder === "/" ? undefined : folder,
    recursive: folder === "/" ? undefined : true,
  });
  const tree = useFolderTree(info.data?.folders ?? [], newFolders);
  const refresh = () => qc.invalidateQueries({ queryKey: ["biz", bizId, "files"] });
  const open = (id: string | null) => router.replace(id ? `/documents?file=${id}` : "/documents", { scroll: false });

  async function upload(files: FileList | File[]) {
    if (trash) return;
    const arr = Array.from(files);
    setUploading({ done: 0, total: arr.length });
    let ok = 0;
    for (const f of arr) {
      try {
        await uploadFile(base, f, { folder });
        ok++;
      } catch (e) {
        toast.error(`${f.name}: ${e instanceof ApiError ? e.message : "upload failed"}`);
      }
      setUploading({ done: ok, total: arr.length });
    }
    setUploading(null);
    if (ok) toast.success(ok === 1 ? "File uploaded" : `${ok} files uploaded`);
    refresh();
  }

  const items = list.data?.items ?? [];
  const usage = info.data?.usage;
  const crumbs = folder === "/" || trash ? [] : folder.split("/").filter(Boolean);

  return (
    <>
      <PageHeader title="Documents" description="Your file library — licences, supplier bills, prescriptions, agreements — plus everything the ERP issues."
        actions={canManage && !trash && <>
          <Button variant="outline" onClick={() => setFolderDialog(true)}><FolderPlusIcon /> New folder</Button>
          <Button onClick={() => input.current?.click()} disabled={!!uploading}>
            {uploading ? <Loader2Icon className="animate-spin" /> : <UploadIcon />} Upload</Button>
          <input ref={input} type="file" multiple hidden accept={ACCEPT}
            onChange={(e) => { if (e.target.files?.length) upload(e.target.files); e.target.value = ""; }} />
        </>} />

      <div className="mb-5 grid gap-3 sm:grid-cols-3">
        {[{ href: "/sales", icon: ReceiptTextIcon, title: "Sales documents", text: "Invoices, quotations, challans, notes" },
          { href: "/purchases", icon: ShoppingCartIcon, title: "Purchase documents", text: "Bills, orders and returns" },
          { href: "/inventory?tab=movements", icon: FileTextIcon, title: "Stock documents", text: "Opening, adjustments, transfers" }].map((l) => (
          <Link key={l.href} href={l.href} className="group flex items-center gap-3 rounded-lg border bg-card px-3 py-2.5 transition-colors hover:border-primary/40">
            <l.icon className="size-5 text-primary" />
            <div className="min-w-0 flex-1"><p className="text-sm font-medium">{l.title}</p><p className="truncate text-xs text-muted-foreground">{l.text}</p></div>
            <ChevronRightIcon className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
          </Link>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
        <aside className="space-y-3">
          <Card className="py-2"><nav className="px-2 text-sm" aria-label="Folders">
            <FolderRow active={folder === "/"} onClick={() => setFolder("/")} icon={HardDriveIcon} name="All files" />
            {tree.map((t) => (
              <FolderRow key={t.folder} active={folder === t.folder} onClick={() => setFolder(t.folder)} depth={t.depth}
                icon={folder === t.folder ? FolderOpenIcon : FolderIcon} name={t.name} count={t.files || undefined} />
            ))}
            <div className="my-1 border-t" />
            <FolderRow active={trash} onClick={() => setFolder(TRASH)} icon={Trash2Icon} name="Trash" />
          </nav></Card>
          {usage && <Card><CardContent className="space-y-1.5 text-xs text-muted-foreground">
            <p className="flex justify-between"><span>Storage used</span><span className="num font-medium text-foreground">{fileSize(usage.stored_bytes)}</span></p>
            <p>Identical files are stored once. Up to {info.data?.max_upload_mb} MB per file.</p>
          </CardContent></Card>}
        </aside>

        <section className="min-w-0"
          onDragOver={(e) => { if (canManage && !trash && e.dataTransfer.types.includes("Files")) { e.preventDefault(); setOver(true); } }}
          onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver(false); }}
          onDrop={(e) => { e.preventDefault(); setOver(false); if (canManage && !trash && e.dataTransfer.files.length) upload(e.dataTransfer.files); }}>
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center">
            <div className="flex min-w-0 flex-1 items-center gap-1 text-sm">
              <button className="font-medium hover:underline" onClick={() => setFolder("/")}>{trash ? "Trash" : "All files"}</button>
              {crumbs.map((c, i) => {
                const path = "/" + crumbs.slice(0, i + 1).join("/");
                return <span key={path} className="flex items-center gap-1"><ChevronRightIcon className="size-3.5 text-muted-foreground" />
                  <button className={cn("truncate hover:underline", i === crumbs.length - 1 && "font-medium")} onClick={() => setFolder(path)}>{c}</button></span>;
              })}
              {list.data && <span className="ml-2 text-muted-foreground">· {list.data.total}</span>}
            </div>
            <div className="relative sm:w-64">
              <SearchIcon className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search names and notes…" className="pl-8" aria-label="Search files" />
            </div>
            <div className="flex rounded-md border p-0.5">
              {(["list", "grid"] as const).map((v) => (
                <Button key={v} size="icon" variant={view === v ? "secondary" : "ghost"} className="size-8" aria-label={`${v} view`} aria-pressed={view === v} onClick={() => setView(v)}>
                  {v === "list" ? <ListIcon /> : <LayoutGridIcon />}</Button>
              ))}
            </div>
          </div>

          {uploading && <div className="mb-3 overflow-hidden rounded-md border bg-card text-sm">
            <div className="flex items-center gap-2 px-3 py-2"><Loader2Icon className="size-4 animate-spin text-primary" /> Uploading {uploading.done} of {uploading.total}…</div>
            <div className="h-1 bg-primary/70 transition-all" style={{ width: `${(uploading.done / uploading.total) * 100}%` }} />
          </div>}

          <Card className={cn("relative min-h-72 overflow-hidden transition-colors", over && "border-primary")}>
            {over && <div className="pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-primary-soft/80 text-primary">
              <UploadCloudIcon className="size-10" /><p className="font-medium">Drop to upload to {folder === "/" ? "the library" : folder}</p></div>}
            {list.error ? <ErrorState error={list.error} retry={() => list.refetch()} /> : list.isLoading ? (
              <div className="space-y-2 p-4">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-12" />)}</div>
            ) : items.length === 0 ? (
              trash ? <EmptyState icon={Trash2Icon} title="The trash is empty" description="Removed files stay here until you delete them for good." />
                : <EmptyState icon={UploadCloudIcon} title={q ? "No files match" : "No files here yet"}
                  description={canManage ? "Drag files onto this area or use Upload. PDFs, images, Office files, CSV and text are accepted." : "Files your team uploads appear here."}
                  action={canManage && !q && <Button onClick={() => input.current?.click()}><UploadIcon /> Upload files</Button>} />
            ) : view === "list" ? (
              <ul className="divide-y">
                {items.map((f) => (
                  <li key={f.id}>
                    <button className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-accent/60" onClick={() => open(f.id)}>
                      <FileTypeIcon contentType={f.content_type} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{f.name}</p>
                        <p className="truncate text-xs text-muted-foreground">
                          {folder === "/" || trash ? `${f.folder} · ` : ""}{fileSize(f.size)}{f.current_version > 1 ? ` · v${f.current_version}` : ""}{f.description ? ` · ${f.description}` : ""}</p>
                      </div>
                      <div className="hidden gap-1 md:flex">{f.entity_type && <Badge variant="blue"><LinkIcon className="size-3" /> {f.entity_type}</Badge>}
                        {f.tags.slice(0, 3).map((t) => <Badge key={t} variant="secondary">{t}</Badge>)}</div>
                      <span className="hidden w-24 text-right text-xs text-muted-foreground sm:block">{date(trash ? f.deleted_at : f.updated_at)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-3 xl:grid-cols-5">
                {items.map((f) => (
                  <button key={f.id} onClick={() => open(f.id)} className="group overflow-hidden rounded-lg border bg-background text-left transition-shadow hover:shadow-md">
                    <div className="flex aspect-[4/3] items-center justify-center bg-muted/60">
                      {kind(f.content_type) === "image" ? <Thumb fileId={f.id} version={f.current_version} alt={f.name} /> : <FileTypeIcon contentType={f.content_type} className="size-12" />}
                    </div>
                    <div className="p-2"><p className="truncate text-sm font-medium">{f.name}</p><p className="text-xs text-muted-foreground">{fileSize(f.size)}</p></div>
                  </button>
                ))}
              </div>
            )}
            {list.data && list.data.total > items.length && <div className="border-t p-2 text-center">
              <Button variant="ghost" size="sm" onClick={() => setLimit((l) => l + 60)}>Show more ({list.data.total - items.length})</Button></div>}
          </Card>
        </section>
      </div>

      <NewFolderDialog open={folderDialog} onOpenChange={setFolderDialog} parent={folder === "/" || trash ? "" : folder}
        onCreate={(f) => { setNewFolders((x) => [...x, f]); setFolder(f); }} />
      <Sheet open={!!openId} onOpenChange={(v) => !v && open(null)} side="right" className="w-[min(560px,100vw)] max-w-none">
        {openId && <FilePanel key={openId} id={openId} onClose={() => open(null)} canManage={canManage} />}
      </Sheet>
    </>
  );
}

function FolderRow({ active, onClick, icon: Icon, name, depth = 0, count }: {
  active: boolean; onClick: () => void; icon: React.ComponentType<{ className?: string }>; name: string; depth?: number; count?: number;
}) {
  return (
    <button onClick={onClick} style={{ paddingLeft: 8 + depth * 14 }} aria-current={active ? "true" : undefined}
      className={cn("flex w-full items-center gap-2 rounded-md py-1.5 pr-2 text-left transition-colors",
        active ? "bg-sidebar-active font-medium text-primary" : "hover:bg-accent")}>
      <Icon className="size-4 shrink-0" /><span className="flex-1 truncate">{name}</span>
      {count !== undefined && <span className="text-xs text-muted-foreground">{count}</span>}
    </button>
  );
}

function useBlobUrl(fileId: string, version: number, enabled = true) {
  const { base, id: bizId } = useBusiness();
  const q = useQuery({
    queryKey: ["biz", bizId, "files", "blob", fileId, version],
    queryFn: async () => URL.createObjectURL(await fileBlob(base, fileId, version)),
    enabled, staleTime: Infinity, gcTime: 5 * 60_000,
  });
  return q;
}

function Thumb({ fileId, version, alt }: { fileId: string; version: number; alt: string }) {
  const { data } = useBlobUrl(fileId, version);
  // eslint-disable-next-line @next/next/no-img-element -- a private blob URL; next/image cannot optimise it
  return data ? <img src={data} alt={alt} className="size-full object-cover" /> : <Skeleton className="size-full rounded-none" />;
}

function Preview({ file }: { file: FileDetail }) {
  const k = kind(file.content_type);
  const showable = k === "image" || k === "pdf" || file.content_type.startsWith("text/");
  const blob = useBlobUrl(file.id, file.current_version, showable);
  const text = useQuery({
    queryKey: ["file-text", blob.data], enabled: !!blob.data && file.content_type.startsWith("text/"),
    queryFn: async () => (await (await fetch(blob.data!)).text()).slice(0, 20_000),
  });
  if (!showable) return <div className="flex h-40 flex-col items-center justify-center gap-2 rounded-lg bg-muted/60 text-sm text-muted-foreground">
    <FileTypeIcon contentType={file.content_type} className="size-12" />No preview for this type — download to open it.</div>;
  if (!blob.data) return <Skeleton className="h-72" />;
  if (k === "image") {
    // eslint-disable-next-line @next/next/no-img-element -- private blob URL
    return <img src={blob.data} alt={file.name} className="max-h-[50vh] w-full rounded-lg border bg-muted/40 object-contain" />;
  }
  if (k === "pdf") return <iframe title={`Preview of ${file.name}`} src={blob.data} className="h-[55vh] w-full rounded-lg border bg-white" />;
  return <pre className="max-h-[50vh] overflow-auto rounded-lg border bg-muted/40 p-3 text-xs whitespace-pre-wrap">{text.data ?? "…"}</pre>;
}

function FilePanel({ id, onClose, canManage }: { id: string; onClose: () => void; canManage: boolean }) {
  const { base, id: bizId } = useBusiness();
  const qc = useQueryClient();
  const f = useBizQuery<FileDetail>(["files", "detail", id], `/files/${id}`);
  const [confirm, setConfirm] = useState<"purge" | null>(null);
  const [busy, setBusy] = useState(false);
  const versionInput = useRef<HTMLInputElement>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["biz", bizId, "files"] });

  async function act(fn: () => Promise<unknown>, ok: string, close = false) {
    setBusy(true);
    try { await fn(); toast.success(ok); refresh(); if (close) onClose(); } catch (e) { toast.error(e instanceof ApiError ? e.message : "Failed"); } finally { setBusy(false); }
  }

  if (f.error) return <div className="p-6"><ErrorState error={f.error} retry={() => f.refetch()} /></div>;
  if (!f.data) return <div className="space-y-3 p-6"><Skeleton className="h-8 w-2/3" /><Skeleton className="h-72" /></div>;
  const file = f.data;
  const deleted = !!file.deleted_at;
  const linkHref = file.entity_type && file.entity_id ? LINK_HREF[file.entity_type]?.(file.entity_id) : undefined;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start gap-3 border-b p-4">
        <FileTypeIcon contentType={file.content_type} />
        <div className="min-w-0 flex-1"><p className="truncate font-semibold">{file.name}</p>
          <p className="text-xs text-muted-foreground">{file.folder} · {fileSize(file.size)} · {file.content_type}</p></div>
        <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close"><XIcon /></Button>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        {deleted && <p className="rounded-md bg-warning/15 px-3 py-2 text-sm">In the trash since {dateTime(file.deleted_at)}.</p>}
        <Preview file={file} />
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => download(base, file)}><DownloadIcon /> Download</Button>
          {canManage && !deleted && <>
            <Button variant="outline" disabled={busy} onClick={() => versionInput.current?.click()}><UploadIcon /> New version</Button>
            <input ref={versionInput} type="file" hidden accept={ACCEPT} onChange={(e) => {
              const nf = e.target.files?.[0]; e.target.value = "";
              if (nf) act(() => uploadVersion(base, id, nf), "New version saved");
            }} />
            <Button variant="ghost" disabled={busy} className="text-destructive" onClick={() => act(() => api(`${base}/files/${id}`, { method: "DELETE" }), "Moved to the trash", true)}><Trash2Icon /> Remove</Button>
          </>}
          {canManage && deleted && <>
            <Button variant="outline" disabled={busy} onClick={() => act(() => api(`${base}/files/${id}/restore`, { method: "POST", body: {} }), "File restored")}><RotateCcwIcon /> Restore</Button>
            <Button variant="destructive" disabled={busy} onClick={() => setConfirm("purge")}><Trash2Icon /> Delete for ever</Button>
          </>}
        </div>
        {linkHref ? <Link href={linkHref} className="flex items-center gap-2 text-sm text-blue hover:underline"><LinkIcon className="size-4" /> Attached to a {file.entity_type} — open it</Link>
          : file.entity_type && <p className="flex items-center gap-2 text-sm text-muted-foreground"><LinkIcon className="size-4" /> Attached to a {file.entity_type === "party" ? "customer or supplier" : file.entity_type}</p>}
        {canManage && !deleted ? <DetailsForm key={file.updated_at} file={file} /> : (
          <div className="space-y-1 text-sm">{file.description && <p>{file.description}</p>}
            <div className="flex flex-wrap gap-1">{file.tags.map((t) => <Badge key={t} variant="secondary">{t}</Badge>)}</div></div>
        )}
        <div>
          <h3 className="mb-2 text-sm font-semibold">Versions</h3>
          <ol className="divide-y rounded-lg border">
            {file.versions.map((v) => (
              <li key={v.version} className="flex items-center gap-3 px-3 py-2 text-sm">
                <Badge variant={v.version === file.current_version ? "default" : "secondary"}>v{v.version}</Badge>
                <div className="min-w-0 flex-1"><p className="truncate">{v.note || v.original_name}</p>
                  <p className="text-xs text-muted-foreground">{dateTime(v.created_at)} · {fileSize(v.size)}</p></div>
                <Button size="icon" variant="ghost" aria-label={`Download version ${v.version}`} onClick={() => download(base, file, v.version)}><DownloadIcon /></Button>
              </li>
            ))}
          </ol>
        </div>
      </div>
      <ConfirmDialog open={confirm === "purge"} onOpenChange={(v) => !v && setConfirm(null)} destructive confirm="Delete for ever"
        title={`Delete “${file.name}” for ever?`} description="All its versions are removed. This cannot be undone."
        onConfirm={() => act(() => api(`${base}/files/${id}?permanent=true`, { method: "DELETE" }), "File deleted", true)} />
    </div>
  );
}

function DetailsForm({ file }: { file: FileDetail }) {
  const { base, id: bizId } = useBusiness();
  const qc = useQueryClient();
  const [name, setName] = useState(file.name);
  const [folder, setFolder] = useState(file.folder);
  const [tags, setTags] = useState(file.tags.join(", "));
  const [description, setDescription] = useState(file.description ?? "");
  const [busy, setBusy] = useState(false);
  const dirty = name !== file.name || folder !== file.folder || tags !== file.tags.join(", ") || description !== (file.description ?? "");
  return (
    <form className="grid gap-3" onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true);
      try {
        await api(`${base}/files/${file.id}`, { method: "PATCH", body: {
          name, folder, description: description || null, tags: tags.split(",").map((t) => t.trim()).filter(Boolean) } });
        toast.success("Saved");
        qc.invalidateQueries({ queryKey: ["biz", bizId, "files"] });
      } catch (err) { toast.error(err instanceof ApiError ? err.message : "Could not save"); } finally { setBusy(false); }
    }}>
      <div className="grid items-start gap-3 sm:grid-cols-2">
        <Field label="Name" htmlFor="f-name"><Input id="f-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} /></Field>
        <Field label="Folder" htmlFor="f-folder" hint="e.g. /Licences/2026"><Input id="f-folder" value={folder} onChange={(e) => setFolder(e.target.value)} maxLength={300} /></Field>
      </div>
      <Field label="Tags" htmlFor="f-tags" hint="Comma-separated"><Input id="f-tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="gst, renewal" /></Field>
      <Field label="Notes" htmlFor="f-notes"><Textarea id="f-notes" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} maxLength={1000} /></Field>
      {dirty && <div><Button type="submit" size="sm" disabled={busy}>{busy && <Loader2Icon className="animate-spin" />}Save changes</Button></div>}
    </form>
  );
}

function NewFolderDialog({ open, onOpenChange, parent, onCreate }: {
  open: boolean; onOpenChange: (v: boolean) => void; parent: string; onCreate: (folder: string) => void;
}) {
  const [name, setName] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);
  const clean = name.trim().replace(/[\\/]+/g, " ");
  return (
    <Dialog open={open} onOpenChange={(v) => { onOpenChange(v); if (!v) setName(""); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>New folder</DialogTitle></DialogHeader>
        <form onSubmit={(e) => { e.preventDefault(); if (!clean) return; onCreate(`${parent}/${clean}`); onOpenChange(false); setName(""); }}>
          <Field label="Name" hint={`Created inside ${parent || "the library"}. It is saved when you upload the first file into it.`}>
            <Input ref={inputRef} value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder="Licences" />
          </Field>
          <DialogFooter className="mt-4"><Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!clean}>Create</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function DocumentsPage() {
  return <Suspense><LibraryPage /></Suspense>;
}
