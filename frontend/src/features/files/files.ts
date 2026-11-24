import { api } from "@/lib/api";

export interface StoredFile {
  id: string; name: string; folder: string; description: string | null; tags: string[];
  content_type: string; size: number; current_version: number;
  entity_type: EntityType | null; entity_id: string | null;
  created_by_id: string | null; created_at: string; updated_at: string; deleted_at: string | null;
}
export interface FileVersion {
  version: number; original_name: string; content_type: string; size: number; sha256: string;
  note: string | null; uploaded_by_id: string | null; created_at: string;
}
export interface FileDetail extends StoredFile { versions: FileVersion[] }
export interface FolderInfo {
  folders: { folder: string; files: number }[];
  usage: { stored_bytes: number; blobs: number };
  max_upload_mb: number;
}
export type EntityType = "document" | "party" | "product" | "expense" | "payment";

export const ACCEPT = ".pdf,.png,.jpg,.jpeg,.gif,.webp,.txt,.csv,.json,.md,.docx,.xlsx,.pptx,.doc,.xls,.ppt,.odt,.ods,.zip";

export function fileSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function kind(ct: string): "image" | "pdf" | "sheet" | "text" | "other" {
  if (ct.startsWith("image/")) return "image";
  if (ct === "application/pdf") return "pdf";
  if (ct.includes("spreadsheet") || ct.includes("excel") || ct === "text/csv") return "sheet";
  if (ct.startsWith("text/") || ct.includes("word") || ct.includes("json")) return "text";
  return "other";
}

export function uploadFile(base: string, file: File, fields: Record<string, string | undefined> = {}) {
  const fd = new FormData();
  fd.append("file", file);
  for (const [k, v] of Object.entries(fields)) if (v) fd.append(k, v);
  return api<StoredFile>(`${base}/files`, { method: "POST", body: fd });
}

export function uploadVersion(base: string, id: string, file: File, note?: string) {
  const fd = new FormData();
  fd.append("file", file);
  if (note) fd.append("note", note);
  return api<FileVersion>(`${base}/files/${id}/versions`, { method: "POST", body: fd });
}

/** The file's bytes as a Blob, fetched with the user's session (never a public URL). */
export async function fileBlob(base: string, id: string, version?: number): Promise<Blob> {
  const res = await api<Response>(`${base}/files/${id}/download`, { raw: true, query: { version } });
  return res.blob();
}

export async function download(base: string, f: { id: string; name: string }, version?: number) {
  const url = URL.createObjectURL(await fileBlob(base, f.id, version));
  const a = document.createElement("a");
  a.href = url;
  a.download = version ? `v${version} - ${f.name}` : f.name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
