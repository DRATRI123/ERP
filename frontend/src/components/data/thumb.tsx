import { PackageIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/** Small product photo (signed link from the API) with a neutral placeholder when there is none. */
export function Thumb({ src, alt = "", className }: { src?: string | null; alt?: string; className?: string }) {
  return (
    <span className={cn("flex size-10 shrink-0 items-center justify-center overflow-hidden rounded-md border bg-muted text-muted-foreground", className)}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {src ? <img src={src} alt={alt} loading="lazy" className="size-full object-cover" /> : <PackageIcon className="size-1/2" />}
    </span>
  );
}

export const thumbOf = (p: object) => (((p as { images?: unknown }).images as { thumb: string }[] | undefined) ?? [])[0]?.thumb ?? null;
