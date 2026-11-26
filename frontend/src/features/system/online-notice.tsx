"use client";

import { GlobeIcon, TriangleAlertIcon } from "lucide-react";

import { usePublicAddress } from "@/hooks/use-public-address";

/** Explains when a shared link only works on this computer, and how to put the ERP online. */
export function OnlineNotice({ what }: { what: string }) {
  const { reachable, kind, base, loading } = usePublicAddress();
  if (loading) return null;
  if (reachable) {
    return kind === "tunnel" ? (
      <p className="flex gap-2 rounded-md bg-primary-soft p-2 text-xs"><GlobeIcon className="mt-0.5 size-4 shrink-0 text-primary" />
        <span>Online through online.bat at <b className="break-all">{base}</b>. This address changes each time online.bat starts, so share links again after a restart. Keep that window open.</span></p>
    ) : null;
  }
  return (
    <div className="space-y-1.5 rounded-md border border-warning/50 bg-warning/10 p-3 text-xs">
      <p className="flex items-center gap-1.5 font-semibold text-amber-800 dark:text-warning"><TriangleAlertIcon className="size-4" /> This {what} link only works on this computer</p>
      <p>“localhost” means “this same device”, so a customer’s or salesperson’s phone cannot open it. To use it from phones anywhere:</p>
      <ul className="list-disc space-y-0.5 pl-4">
        <li><b>Quick:</b> double-click <b>online.bat</b> in the ERP folder and keep its window open. It sets everything up by itself (free Cloudflare tunnel) and this link switches to an https address.</li>
        <li><b>Permanent:</b> run the ERP on a server with your own domain (see “Deployment” in the help) — a fixed address that works all the time.</li>
        <li><b>Same Wi-Fi only:</b> phones on the shop network can use this computer’s network address after starting the web app with <code>npm run start:lan</code>.</li>
      </ul>
    </div>
  );
}
