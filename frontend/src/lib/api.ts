/**
 * Central API client for the FastAPI backend (same origin: /api/v1 is proxied by Next.js in
 * development and routed by Caddy in production).
 *
 * * Access tokens live only in memory. The refresh token is an httpOnly cookie
 *   (ERP_AUTH_COOKIE=true) protected by a double-submit CSRF token.
 * * A 401 triggers one refresh (shared by concurrent requests) and a retry; if that fails the
 *   user is signed out.
 * * Errors are thrown as ApiError with the backend's code, message, details and request id.
 */

import { useAuth } from "@/stores/auth";

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
    public requestId?: string,
  ) {
    super(message);
  }
  get fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    if (Array.isArray(this.details)) {
      for (const d of this.details as { loc?: (string | number)[]; msg?: string }[]) {
        const loc = (d.loc ?? []).filter((x) => x !== "body");
        if (loc.length) out[loc.join(".")] = d.msg ?? "Invalid value";
      }
    }
    return out;
  }
}

function csrfToken(): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/(?:^|;\s*)erp_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1] ?? "") : "";
}

let refreshing: Promise<boolean> | null = null;

/** Exchange the refresh cookie (or in-memory refresh token) for a new access token. */
export function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      const { refreshToken, setTokens } = useAuth.getState();
      try {
        const res = await fetch(`${API_BASE}/auth/refresh`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
          body: JSON.stringify(refreshToken ? { refresh_token: refreshToken } : {}),
        });
        if (!res.ok) return false;
        const data = await res.json();
        setTokens(data.access_token, data.refresh_token ?? null);
        return true;
      } catch {
        return false;
      } finally {
        setTimeout(() => (refreshing = null), 0);
      }
    })();
  }
  return refreshing;
}

type Query = Record<string, string | number | boolean | null | undefined | (string | number)[]>;

export function buildQuery(params?: Query): string {
  if (!params) return "";
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => q.append(k, String(x)));
    else q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Query;
  headers?: Record<string, string>;
  raw?: boolean; // return the Response (files, HTML)
  signal?: AbortSignal;
}

async function parseError(res: Response): Promise<ApiError> {
  let body: { error?: { code?: string; message?: string; details?: unknown; request_id?: string } } = {};
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  const e = body.error ?? {};
  const fallback = res.status === 503 ? "The server is busy. Please try again." : `Request failed (${res.status})`;
  return new ApiError(res.status, e.code ?? "http_error", e.message ?? fallback, e.details, e.request_id);
}

export async function api<T = unknown>(path: string, opts: RequestOptions = {}, retried = false): Promise<T> {
  const token = useAuth.getState().accessToken;
  const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.body instanceof FormData) body = opts.body;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(`${API_BASE}${path}${buildQuery(opts.query)}`, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    headers,
    body,
    credentials: "same-origin",
    signal: opts.signal,
  });
  if (res.status === 401 && !retried && !path.startsWith("/auth/login")) {
    if (await refreshSession()) return api<T>(path, opts, true);
    useAuth.getState().signOut();
  }
  if (!res.ok) throw await parseError(res);
  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Fetch with auth and open the result (PDF/HTML) in a new tab, or download it. */
export async function openFile(path: string, query?: Query, filename?: string) {
  const res = await api<Response>(path, { query, raw: true });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  if (filename) {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
  } else {
    window.open(url, "_blank", "noopener");
  }
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
