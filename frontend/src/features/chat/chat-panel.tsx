"use client";

import { ArrowLeftIcon, BellIcon, CheckCheckIcon, CheckIcon, FileTextIcon, ImageIcon, Loader2Icon, MessageCircleIcon, SearchIcon, SendIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";

import { type ChatMsg, type Contact, useChatState } from "./use-chat";

const time = (iso: string) => new Date(iso).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
const day = (iso: string) => new Date(iso).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
const initials = (n: string) => n.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

/** Contacts on the left (or first on a phone), the conversation on the right. */
export function ChatPanel({ peer, onPeer, linkDocs = true, className }: {
  peer: string | null; onPeer: (id: string | null) => void; linkDocs?: boolean; className?: string;
}) {
  const contacts = useBizQuery<{ contacts: Contact[]; unread: number }>(["chat", "contacts"], "/chat/contacts");
  const current = contacts.data?.contacts.find((c) => c.id === peer) ?? null;
  return (
    <div className={cn("grid min-h-0 overflow-hidden rounded-xl border bg-card md:grid-cols-[18rem_1fr]", className)}>
      <div className={cn("min-h-0 border-r", peer && "max-md:hidden")}><Contacts data={contacts.data?.contacts} loading={contacts.isLoading} active={peer} onPick={onPeer} /></div>
      <div className={cn("flex min-h-0 flex-col", !peer && "max-md:hidden")}>
        {peer && current ? <Thread peer={current} onBack={() => onPeer(null)} linkDocs={linkDocs} />
          : <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground"><MessageCircleIcon className="size-8" />Choose a person to chat with.</div>}
      </div>
    </div>
  );
}

function Contacts({ data, loading, active, onPick }: { data?: Contact[]; loading: boolean; active: string | null; onPick: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [perm, setPerm] = useState(() => (typeof window !== "undefined" && "Notification" in window ? Notification.permission : "denied"));
  const list = (data ?? []).filter((c) => !q || c.name.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b p-2"><div className="relative"><SearchIcon className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
        <Input className="pl-8" placeholder="Search people" value={q} onChange={(e) => setQ(e.target.value)} /></div></div>
      {perm === "default" && <button type="button" className="flex items-center gap-1.5 border-b bg-primary-soft px-3 py-2 text-left text-xs"
        onClick={() => void Notification.requestPermission().then(setPerm)}><BellIcon className="size-3.5" /> Turn on message alerts on this device</button>}
      <ul className="min-h-0 flex-1 divide-y overflow-y-auto">
        {loading ? <li className="p-3"><Skeleton className="h-24" /></li> : !list.length ? <li className="p-4 text-sm text-muted-foreground">Nobody else in this business yet. Invite people in Users.</li>
          : list.map((c) => (
            <li key={c.id}><button type="button" onClick={() => onPick(c.id)} className={cn("flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-accent", active === c.id && "bg-accent")}>
              <span className={cn("flex size-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold", c.role === "owner" ? "bg-primary text-primary-foreground" : "bg-muted")}>{initials(c.name)}</span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center justify-between gap-2"><span className="truncate font-medium">{c.name}</span>
                  {c.last && <span className="shrink-0 text-[11px] text-muted-foreground">{new Date(c.last.at).toDateString() === new Date().toDateString() ? time(c.last.at) : day(c.last.at)}</span>}</span>
                <span className="flex items-center justify-between gap-2"><span className="truncate text-xs text-muted-foreground">{c.last ? `${c.last.mine ? "You: " : ""}${c.last.body ?? ""}` : c.role_name}</span>
                  {c.unread > 0 && <Badge className="h-5 min-w-5 justify-center rounded-full px-1.5">{c.unread}</Badge>}</span>
              </span>
            </button></li>
          ))}
      </ul>
    </div>
  );
}

function Thread({ peer, onBack, linkDocs }: { peer: Contact; onBack: () => void; linkDocs: boolean }) {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const msgs = useBizQuery<ChatMsg[]>(["chat", "with", peer.id], `/chat/with/${peer.id}`);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const setOpen = useChatState((s) => s.setOpenPeer);
  useEffect(() => { setOpen(peer.id); return () => setOpen(null); }, [peer.id, setOpen]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs.data?.length]);
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["biz", id, "chat"] }); };
  const send = async () => {
    const body = text.trim();
    if (!body) return;
    setBusy(true);
    try { await api(`${base}/chat/messages`, { body: { to: peer.id, body } }); setText(""); refresh(); }
    catch (e) { toast.error((e as ApiError).message); } finally { setBusy(false); }
  };
  const photo = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    const fd = new FormData(); fd.append("to", peer.id); fd.append("file", f);
    try { await api(`${base}/chat/photos`, { body: fd }); refresh(); }
    catch (e) { toast.error((e as ApiError).message); } finally { setBusy(false); }
  };
  let lastDay = "";
  return (
    <>
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <Button variant="ghost" size="icon" className="size-8 md:hidden" aria-label="Back" onClick={onBack}><ArrowLeftIcon /></Button>
        <div className="min-w-0 flex-1"><p className="truncate font-semibold">{peer.name}</p><p className="text-xs text-muted-foreground">{peer.role_name}</p></div>
        {peer.phone && <Button asChild size="sm" variant="outline"><a href={`tel:${peer.phone}`}>Call</a></Button>}
      </div>
      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto bg-muted/30 p-3">
        {msgs.isLoading ? <Skeleton className="h-32" /> : !msgs.data?.length ? <p className="py-8 text-center text-sm text-muted-foreground">Say hello 👋</p> : msgs.data.map((m) => {
          const d = day(m.at); const sep = d !== lastDay; lastDay = d;
          return (
            <div key={m.id}>
              {sep && <p className="my-2 text-center text-[11px] text-muted-foreground">{d}</p>}
              <div className={cn("flex", m.mine ? "justify-end" : "justify-start")}>
                <div className={cn("max-w-[80%] rounded-2xl px-3 py-1.5 text-sm shadow-xs", m.mine ? "rounded-br-sm bg-primary text-primary-foreground" : "rounded-bl-sm bg-card")}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  {m.thumb && <a href={m.photo ?? "#"} target="_blank" rel="noreferrer"><img src={m.thumb} alt="Photo" className="mb-1 max-h-60 rounded-lg" /></a>}
                  {m.document_label && (linkDocs && m.document_id
                    ? <Link href={`/documents/${m.document_id}`} className={cn("mb-1 flex items-center gap-1.5 rounded-md px-2 py-1 text-xs underline-offset-2 hover:underline", m.mine ? "bg-white/15" : "bg-muted")}><FileTextIcon className="size-3.5" />{m.document_label}</Link>
                    : <p className={cn("mb-1 flex items-center gap-1.5 rounded-md px-2 py-1 text-xs", m.mine ? "bg-white/15" : "bg-muted")}><FileTextIcon className="size-3.5" />{m.document_label}</p>)}
                  {m.body && <p className="break-words whitespace-pre-wrap">{m.body}</p>}
                  <p className={cn("mt-0.5 flex items-center justify-end gap-1 text-[10px]", m.mine ? "text-primary-foreground/80" : "text-muted-foreground")}>
                    {time(m.at)}{m.mine && (m.read_at ? <CheckCheckIcon className="size-3" aria-label="Read" /> : <CheckIcon className="size-3" aria-label="Sent" />)}</p>
                </div>
              </div>
            </div>
          );
        })}
        <div ref={end} />
      </div>
      <form className="flex items-end gap-1.5 border-t p-2" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <label className="flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground hover:bg-accent" aria-label="Send a photo">
          <ImageIcon className="size-5" /><input type="file" accept="image/*" className="sr-only" onChange={(e) => { void photo(e.target.files?.[0]); e.target.value = ""; }} /></label>
        <Textarea rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder="Message" aria-label="Message" className="max-h-32 min-h-9 resize-none"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} />
        <Button type="submit" size="icon" className="shrink-0" disabled={busy || !text.trim()} aria-label="Send">{busy ? <Loader2Icon className="animate-spin" /> : <SendIcon />}</Button>
      </form>
    </>
  );
}
