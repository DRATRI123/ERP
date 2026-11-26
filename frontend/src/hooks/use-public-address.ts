"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

export interface PublicAddress { url: string | null; kind: "server" | "tunnel" | "local" }

const isLocalHost = (h: string) => h === "localhost" || h === "127.0.0.1" || h === "[::1]" || /^(10|192\.168|172\.(1[6-9]|2\d|3[01]))\./.test(h) || /\.(local|lan|home|internal)$/.test(h);

/** The base to put in links for phones elsewhere: the server domain or a running online.bat
 *  tunnel; else the address this page is open at (and ``reachable`` tells if phones can use it). */
export function usePublicAddress() {
  const q = useQuery({ queryKey: ["public-address"], queryFn: () => api<PublicAddress>("/system/public-address"), refetchInterval: 30_000, staleTime: 20_000 });
  const here = typeof window === "undefined" ? "" : window.location.origin;
  const hereLocal = typeof window === "undefined" ? true : isLocalHost(window.location.hostname);
  const base = q.data?.url ?? here;
  return { base, kind: q.data?.kind ?? "local", reachable: !!q.data?.url || !hereLocal, loading: q.isLoading };
}
