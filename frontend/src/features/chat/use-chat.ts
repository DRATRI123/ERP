"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { create } from "zustand";

import { useBusiness } from "@/hooks/use-business";
import { api } from "@/lib/api";
import { useAuth } from "@/stores/auth";

export interface ChatMsg {
  id: string; from: string; to: string; mine: boolean; body: string | null; photo: string | null; thumb: string | null;
  document_id: string | null; document_label: string | null; at: string; read_at: string | null;
}
export interface Contact {
  id: string; name: string; phone: string | null; role: string; role_name: string; unread: number;
  last: { body: string | null; at: string; mine: boolean } | null;
}

interface ChatState {
  unread: number; openPeer: string | null; online: string[]; typing: Record<string, number>; transport: "websocket" | "polling" | "offline";
  setUnread: (n: number) => void; setOpenPeer: (id: string | null) => void;
}
export const useChatState = create<ChatState>((set) => ({
  unread: 0, openPeer: null, online: [], typing: {}, transport: "offline",
  setUnread: (unread) => set({ unread }), setOpenPeer: (openPeer) => set({ openPeer }),
}));

let socket: WebSocket | null = null;
let lastTyping = 0;
/** Tell the other person "typing…" (at most every 2 seconds). */
export function sendTyping(to: string) {
  const now = Date.now();
  if (now - lastTyping < 2000 || socket?.readyState !== WebSocket.OPEN) return;
  lastTyping = now;
  socket.send(JSON.stringify({ type: "typing", to }));
}

/** Whether this person may chat in the current business. */
export function useCanChat() {
  const { isOwner, can } = useBusiness();
  return isOwner || can("chat.use");
}

function beep() {
  try {
    const ctx = new AudioContext();
    const o = ctx.createOscillator(); const g = ctx.createGain();
    o.frequency.value = 880; g.gain.value = 0.05; o.connect(g); g.connect(ctx.destination);
    o.start(); o.stop(ctx.currentTime + 0.12);
  } catch { /* sound blocked until the page is clicked once */ }
}

/** Live chat: a WebSocket (messages, "typing…", online, read receipts) that reconnects by itself.
 *  If WebSockets are blocked on this network it falls back to a 20-second "wait" request. */
export function useChatStream(onOpen?: (peer: string) => void) {
  const { base, id } = useBusiness();
  const allowed = useCanChat();
  const qc = useQueryClient();
  const openRef = useRef(onOpen);
  useEffect(() => { openRef.current = onOpen; }, [onOpen]);

  useEffect(() => {
    if (!base || !allowed || !id) return;
    let stopped = false;
    let failures = 0;
    let ping: number | undefined;
    let retry: number | undefined;
    const invalidate = () => { void qc.invalidateQueries({ queryKey: ["biz", id, "chat"] }); };

    const onMessages = (msgs: ChatMsg[], unread: number | undefined) => {
      const open = useChatState.getState().openPeer;
      const seen = msgs.filter((m) => !m.mine && m.from === open && !m.read_at).length;
      if (unread !== undefined) useChatState.getState().setUnread(Math.max(0, unread - seen));
      if (!msgs.length) return;
      invalidate();
      if (seen && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "read", peer: open }));
      const incoming = msgs.filter((m) => !m.mine && m.from !== open);
      const last = incoming[incoming.length - 1];
      if (!last) return;
      beep();
      const text = last.body ?? (last.photo ? "📷 Photo" : last.document_label ?? "New message");
      if (document.hidden && "Notification" in window && Notification.permission === "granted") {
        const n = new Notification("New message", { body: text, tag: `chat-${last.from}` });
        n.onclick = () => { window.focus(); openRef.current?.(last.from); };
      } else {
        toast(text, { description: "New message", action: { label: "Open", onClick: () => openRef.current?.(last.from) } });
      }
    };

    const poll = async () => {
      useChatState.setState({ transport: "polling" });
      let after = new Date().toISOString();
      try {
        const first = await api<{ unread: number; server_time: string }>(`${base}/chat/contacts`);
        after = first.server_time;
        useChatState.getState().setUnread(first.unread);
      } catch { /* retry below */ }
      while (!stopped) {
        try {
          const r = await api<{ messages: ChatMsg[]; unread: number; server_time: string }>(`${base}/chat/wait`, { query: { after } });
          if (stopped) break;
          after = r.messages.length ? r.messages[r.messages.length - 1]!.at : r.server_time;
          onMessages(r.messages, r.unread);
        } catch { await new Promise((res) => setTimeout(res, 5000)); }
      }
    };

    const connect = () => {
      if (stopped) return;
      const token = useAuth.getState().accessToken;
      if (!token) { retry = window.setTimeout(connect, 2000); return; }
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${window.location.host}/api/v1/chat/ws`);
      socket = ws;
      let ready = false;
      ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token, business_id: id }));
      ws.onmessage = (e) => {
        const f = JSON.parse(e.data as string) as Record<string, unknown>;
        if (f.type === "ready") {
          ready = true; failures = 0;
          useChatState.setState({ transport: "websocket", online: f.online as string[] });
          useChatState.getState().setUnread(f.unread as number);
          invalidate();  // catch up on anything sent while disconnected
          ping = window.setInterval(() => ws.readyState === WebSocket.OPEN && ws.send('{"type":"ping"}'), 25_000);
        } else if (f.type === "message") {
          onMessages([f.message as ChatMsg], f.unread as number | undefined);
        } else if (f.type === "presence") {
          useChatState.setState({ online: f.online as string[] });
        } else if (f.type === "typing") {
          useChatState.setState((st) => ({ typing: { ...st.typing, [f.from as string]: Date.now() } }));
        } else if (f.type === "read") {
          void qc.invalidateQueries({ queryKey: ["biz", id, "chat", "with", f.by as string] });
        }
      };
      ws.onclose = () => {
        window.clearInterval(ping);
        if (socket === ws) socket = null;
        if (stopped) return;
        useChatState.setState({ transport: "offline", online: [] });
        failures = ready ? 0 : failures + 1;
        if (failures >= 3) { void poll(); return; }  // WebSockets blocked here: use the fallback
        retry = window.setTimeout(connect, Math.min(15_000, 1000 * 2 ** failures));
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(retry); window.clearInterval(ping);
      socket?.close(); socket = null;
    };
  }, [base, id, allowed, qc]);
}
