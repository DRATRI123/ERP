import { cn } from "@/lib/utils";

export function Logo({ className, collapsed, invert }: { className?: string; collapsed?: boolean; invert?: boolean }) {
  return (
    <div className={cn("flex items-center gap-2.5 font-semibold tracking-tight", className)}>
      <span className={cn("flex size-8 shrink-0 items-center justify-center rounded-lg shadow-sm",
        invert ? "bg-white/15 text-white ring-1 ring-white/25" : "bg-gradient-to-br from-primary to-[oklch(0.45_0.12_210)] text-white")}>
        <svg viewBox="0 0 24 24" className="size-4.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M4 19V9l8-5 8 5v10" /><path d="M9 19v-6h6v6" />
        </svg>
      </span>
      {!collapsed && <span className="text-[15px]">Private&nbsp;ERP</span>}
    </div>
  );
}
