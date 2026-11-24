"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/stores/auth";
import { useWorkspace } from "@/stores/business";

export type LiveState = "connecting" | "live" | "offline";

function wsUrl(): string {
  if (process.env.NEXT_PUBLIC_WS_URL) return process.env.NEXT_PUBLIC_WS_URL;
  const { protocol, host, hostname, port } = window.location;
  // Local development: Next.js runs on :3000, the realtime service on :8090.
  if (port === "3000") return `ws://${hostname}:8090/ws`;
  return `${protocol === "https:" ? "wss" : "ws"}://${host}/ws`;
}

const LABELS: Record<string, string> = {
  "invoice.created": "New invoice", "payment.created": "Payment received", "document.cancelled": "Document cancelled",
};

/**
 * Live updates from the Node.js real-time service: events for the selected business refresh the
 * affected screens. Optional — without Redis/realtime the app works the same, just without push.
 */
export function useRealtime(): LiveState {
  const token = useAuth((s) => s.accessToken);
  const myId = useAuth((s) => s.me?.user.id);
  const businessId = useWorkspace((s) => s.businessId);
  const qc = useQueryClient();
  const [state, setState] = useState<LiveState>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const tokenRef = useRef(token);
  useEffect(() => { tokenRef.current = token; }, [token]);

  useEffect(() => {
    if (!token || !businessId) return;
    let stopped = false;
    let retry = 1000;
    let timer: number | undefined;

    function connect() {
      if (stopped) return;
      setState("connecting");
      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        setState("offline");
        return;
      }
      wsRef.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token: tokenRef.current }));
      ws.onmessage = (m) => {
        const msg = JSON.parse(m.data as string);
        if (msg.type === "ready") {
          retry = 1000;
          setState("live");
          ws.send(JSON.stringify({ type: "subscribe", business_id: businessId }));
        } else if (msg.type === "event" || msg.type === "resync_required") {
          qc.invalidateQueries({ queryKey: ["biz", businessId] });
          const ev = msg.event as { type?: string; data?: { actor_id?: string; number?: string } } | undefined;
          const label = ev?.type ? LABELS[ev.type] : undefined;
          if (label && ev?.data?.actor_id !== myId) toast.info(label, { description: ev?.data?.number });
        }
      };
      ws.onclose = () => {
        if (stopped) return;
        setState("offline");
        timer = window.setTimeout(connect, retry);
        retry = Math.min(retry * 2, 60_000);
      };
    }
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      wsRef.current?.close();
    };
  }, [token, businessId, qc, myId]);

  // A refreshed access token is re-sent so the socket stays authorised.
  useEffect(() => {
    const ws = wsRef.current;
    if (token && ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "auth", token }));
  }, [token]);

  return state;
}
