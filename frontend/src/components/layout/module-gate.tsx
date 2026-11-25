"use client";

import { BlocksIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { EmptyState } from "@/components/data/states";
import { NAV } from "@/components/layout/nav";
import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useBusiness } from "@/hooks/use-business";
import { useModules } from "@/hooks/use-modules";

/** A page of a module this business has switched off says so, instead of half-working. */
export function ModuleGate({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { on, loading } = useModules();
  const { can, isOwner } = useBusiness();
  const item = NAV.find((n) => n.module && (path === n.href || path.startsWith(`${n.href}/`)));
  if (!item?.module || loading || on(item.module)) return <>{children}</>;
  return (
    <Card className="mx-auto mt-6 max-w-xl">
      <EmptyState icon={BlocksIcon} title={`${item.label} is switched off for this business`}
        description="Each business shows only the modules it uses. Your data is kept; switch the module on to use it again."
        action={(isOwner || can("business.settings")) && <Link href="/settings?tab=modules" className={buttonVariants()}>Open Settings → Modules</Link>} />
    </Card>
  );
}
