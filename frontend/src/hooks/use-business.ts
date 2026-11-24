"use client";

import { useMutation, useQuery, useQueryClient, type QueryKey, type UseQueryOptions } from "@tanstack/react-query";
import { toast } from "sonner";

import { api, ApiError, type RequestOptions } from "@/lib/api";
import { useAuth, type Membership } from "@/stores/auth";
import { useWorkspace } from "@/stores/business";

/** The selected business, its base API path and the member's permissions in it. */
export function useBusiness() {
  const me = useAuth((s) => s.me);
  const businessId = useWorkspace((s) => s.businessId);
  const list = me?.businesses ?? [];
  const current: Membership | undefined = list.find((b) => b.business_id === businessId) ?? list[0];
  const perms = new Set(current?.permissions ?? []);
  return {
    businesses: list,
    business: current,
    id: current?.business_id ?? "",
    base: current ? `/businesses/${current.business_id}` : "",
    isOwner: !!me?.user.is_owner,
    can: (...codes: string[]) => codes.every((c) => perms.has(c)),
    canAny: (...codes: string[]) => codes.some((c) => perms.has(c)),
  };
}

/** GET a business-scoped endpoint. ``path`` is relative to /businesses/{id}. */
export function useBizQuery<T>(
  key: QueryKey, path: string, query?: RequestOptions["query"],
  options?: Omit<UseQueryOptions<T, ApiError>, "queryKey" | "queryFn">,
) {
  const { base, id } = useBusiness();
  return useQuery<T, ApiError>({
    queryKey: ["biz", id, ...key, query ?? {}],
    queryFn: ({ signal }) => api<T>(`${base}${path}`, { query, signal }),
    enabled: !!id && (options?.enabled ?? true),
    ...options,
  });
}

/** POST/PATCH/DELETE with toast feedback and cache invalidation of the listed keys. */
export function useBizMutation<TBody = unknown, TOut = unknown>(
  method: "POST" | "PATCH" | "PUT" | "DELETE",
  path: string | ((body: TBody) => string),
  { invalidate = [], success, onSuccess, global = false }: {
    invalidate?: QueryKey[]; success?: string | ((out: TOut) => string);
    onSuccess?: (out: TOut, body: TBody) => void; global?: boolean;
  } = {},
) {
  const qc = useQueryClient();
  const { base, id } = useBusiness();
  return useMutation<TOut, ApiError, TBody>({
    mutationFn: (body) => {
      const p = typeof path === "function" ? path(body) : path;
      return api<TOut>(`${global ? "" : base}${p}`, { method, body: method === "DELETE" ? undefined : (body ?? {}) });
    },
    onSuccess: (out, body) => {
      for (const k of invalidate) qc.invalidateQueries({ queryKey: global ? k : ["biz", id, ...k] });
      if (success) toast.success(typeof success === "function" ? success(out) : success);
      onSuccess?.(out, body);
    },
    onError: (e) => toast.error(e.message, { description: e.requestId ? `Ref ${e.requestId.slice(0, 8)}` : undefined }),
  });
}

/** Non-business endpoints (developer apps, system, users). */
export function useApiQuery<T>(key: QueryKey, path: string, query?: RequestOptions["query"],
  options?: Omit<UseQueryOptions<T, ApiError>, "queryKey" | "queryFn">) {
  return useQuery<T, ApiError>({
    queryKey: [...key, query ?? {}],
    queryFn: ({ signal }) => api<T>(path, { query, signal }),
    ...options,
  });
}
