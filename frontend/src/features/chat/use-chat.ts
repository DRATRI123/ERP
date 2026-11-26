"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { create } from "zustand";

import { useBusiness } from "@/hooks/use-business";
import { api } from "@/lib/api";

export interface ChatMsg {
  id: string; from: string; to: string; mine: boolean; body: string | null; photo: string | null; thumb: string | null;
  document_id: string | null; document_label: string | null; at: string; read_at: string | null;
}
export interface Contact {
  id: string; name: string; phone: string | null; role: string; role_name: string; unread: number;
  last: { body: string | null; at: string; mine: boolean } | null;
}

interface ChatState { unread: number; openPeer: string | null; setUnread: (n: number) => void; setOpenPeer: (id: string | null) => void }
export const useChatState = create<ChatState>((set) => ({
  unread: 0, openPeer: null, setUnread: (unread) => set({ unread }), setOpenPeer: (openPeer) => set({ openPeer }),
}));

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

/** Keeps one "wait for new messages" request open: new messages show within about a second,
 *  update the unread badge and, when the page is in the background, raise a notification. */
export function useChatStream(onOpen?: (peer: string) => void) {
  const { base, id } = useBusiness();
  const allowed = useCanChat();
  const qc = useQueryClient();
  const openRef = useRef(onOpen);
  useEffect(() => { openRef.current = onOpen; }, [onOpen]);

  useEffect(() => {
    if (!base || !allowed) return;
    let stopped = false;
    let after = new Date().toISOString();
    (async () => {
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
          // Messages in the conversation already on screen are read as they arrive (the thread
          // reloads and marks them read), so they do not count as unread.
          const open = useChatState.getState().openPeer;
          const seen = r.messages.filter((m) => !m.mine && m.from === open && !m.read_at).length;
          useChatState.getState().setUnread(Math.max(0, r.unread - seen));
          if (r.messages.length) {
            void qc.invalidateQueries({ queryKey: ["biz", id, "chat"] });
            const incoming = r.messages.filter((m) => !m.mine && m.from !== useChatState.getState().openPeer);
            const last = incoming[incoming.length - 1];
            if (last) {
              beep();
              const text = last.body ?? (last.photo ? "📷 Photo" : last.document_label ?? "New message");
              if (document.hidden && "Notification" in window && Notification.permission === "granted") {
                const n = new Notification("New message", { body: text, tag: `chat-${last.from}` });
                n.onclick = () => { window.focus(); openRef.current?.(last.from); };
              } else {
                toast(text, { description: "New message", action: { label: "Open", onClick: () => openRef.current?.(last.from) } });
              }
            }
          }
        } catch {
          await new Promise((res) => setTimeout(res, 5000));
        }
      }
    })();
    return () => { stopped = true; };
  }, [base, id, allowed, qc]);
}
