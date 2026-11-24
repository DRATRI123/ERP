"use client";

import { BookOpenIcon, InfoIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/dialog";
import { Tooltip } from "@/components/ui/switch";

import { HELP_BY_ID, topicForPath } from "./content";
import { HelpView } from "./help-view";

/** The ⓘ button: opens the guide for the current page (or ``topic``) in a side panel. */
export function HelpButton({ topic }: { topic?: string }) {
  const path = usePathname();
  const [open, setOpen] = useState<string | null>(null);
  const start = topic ? HELP_BY_ID.get(topic) : topicForPath(path);
  if (!start) return null;
  const current = HELP_BY_ID.get(open ?? start.id) ?? start;
  return (
    <>
      <Tooltip content="How to use this page" side="bottom">
        <Button type="button" variant="ghost" size="icon" className="size-8 rounded-full text-blue hover:bg-blue-soft hover:text-blue"
          aria-label={`Guide: ${start.title}`} onClick={() => setOpen(start.id)}><InfoIcon className="size-5" /></Button>
      </Tooltip>
      <Sheet open={open !== null} onOpenChange={(v) => !v && setOpen(null)} side="right" className="w-[min(480px,100vw)] max-w-none">
        <div className="flex h-full flex-col">
          <div className="flex items-start gap-3 border-b p-4">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-blue-soft text-blue"><InfoIcon className="size-5" /></span>
            <div className="min-w-0 flex-1"><p className="text-xs font-medium text-muted-foreground uppercase">{current.group}</p><h2 className="font-semibold">{current.title}</h2></div>
            <Button size="icon" variant="ghost" aria-label="Close guide" onClick={() => setOpen(null)}><XIcon /></Button>
          </div>
          <div className="flex-1 overflow-y-auto p-4"><HelpView topic={current} onOpen={setOpen} /></div>
          <div className="flex items-center justify-between border-t p-3 text-sm">
            {current.id !== start.id ? <button type="button" className="text-blue hover:underline" onClick={() => setOpen(start.id)}>← Back to this page’s guide</button> : <span />}
            <Link href={`/help?topic=${current.id}`} onClick={() => setOpen(null)} className="inline-flex items-center gap-1.5 text-blue hover:underline"><BookOpenIcon className="size-4" /> All guides</Link>
          </div>
        </div>
      </Sheet>
    </>
  );
}
