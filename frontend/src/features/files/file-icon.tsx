import { FileIcon, FileImageIcon, FileSpreadsheetIcon, FileTextIcon } from "lucide-react";

import { cn } from "@/lib/utils";

import { kind } from "./files";

const STYLE = {
  image: ["bg-blue-soft text-blue", FileImageIcon],
  pdf: ["bg-destructive/10 text-destructive", FileTextIcon],
  sheet: ["bg-primary-soft text-primary", FileSpreadsheetIcon],
  text: ["bg-secondary text-secondary-foreground", FileTextIcon],
  other: ["bg-secondary text-muted-foreground", FileIcon],
} as const;

export function FileTypeIcon({ contentType, className }: { contentType: string; className?: string }) {
  const [tone, Icon] = STYLE[kind(contentType)];
  return <span className={cn("flex size-9 shrink-0 items-center justify-center rounded-lg", tone, className)}><Icon className="size-4.5" /></span>;
}
