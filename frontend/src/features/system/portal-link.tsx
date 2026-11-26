"use client";

import { CopyIcon, ExternalLinkIcon, MessageCircleIcon, SmartphoneIcon } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { OnlineNotice } from "@/features/system/online-notice";
import { usePublicAddress } from "@/hooks/use-public-address";

/** The salesperson portal link to send to MRs / salespeople, with how to give them access. */
export function PortalLinkCard() {
  const { base } = usePublicAddress();
  const url = `${base}/sp`;
  const msg = `Our sales app: ${url}\nSign in with the e-mail and password from your invitation. Add it to your home screen for one-tap use.`;
  return (
    <Card className="mb-4"><CardContent className="space-y-3">
      <div className="flex items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary"><SmartphoneIcon className="size-5" /></span>
        <div className="min-w-0 flex-1"><p className="font-medium">Salesperson portal for MRs and field staff</p>
          <p className="text-sm text-muted-foreground">A simple phone page: today&apos;s beat, orders (with free quantity), collections, visit notes and new shops — nothing else of the ERP.
            Invite each person in <Link className="text-primary hover:underline" href="/users">Users</Link> with the role <b>Field salesperson / MR</b>, then send them this link.</p></div>
      </div>
      <OnlineNotice what="portal" />
      <div className="flex flex-wrap gap-2">
        <code className="min-w-0 flex-1 truncate rounded-md border bg-muted px-2 py-1.5 text-xs">{url}</code>
        <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(url).then(() => toast.success("Link copied"))}><CopyIcon /> Copy</Button>
        <Button asChild size="sm" className="bg-[#25D366] text-white hover:bg-[#1ebe57]"><a target="_blank" rel="noreferrer" href={`https://wa.me/?text=${encodeURIComponent(msg)}`}><MessageCircleIcon /> Send</a></Button>
        <Button asChild size="sm" variant="outline"><Link href="/sp"><ExternalLinkIcon /> Open</Link></Button>
      </div>
    </CardContent></Card>
  );
}
