import { ShieldCheckIcon } from "lucide-react";

import { Logo } from "@/components/layout/logo";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.1fr_1fr]">
      <aside className="relative hidden overflow-hidden bg-[radial-gradient(120%_80%_at_0%_0%,oklch(0.42_0.1_155),oklch(0.28_0.07_165)_55%,oklch(0.25_0.08_245))] p-10 text-white lg:flex lg:flex-col">
        <Logo className="text-white" invert />
        <div className="mt-auto max-w-md space-y-6">
          <h2 className="text-3xl leading-tight font-semibold tracking-tight">
            Billing, GST, stock and books for every business you run — in one place.
          </h2>
          <ul className="space-y-3 text-sm text-white/80">
            {["GST-ready invoices in seconds, A4 or thermal", "Live stock with batches, expiry and transfers",
              "Double-entry books posted automatically", "Your data, your server — encrypted backups"].map((t) => (
              <li key={t} className="flex items-center gap-2"><ShieldCheckIcon className="size-4 text-emerald-300" />{t}</li>
            ))}
          </ul>
        </div>
        <div className="pointer-events-none absolute -right-24 -bottom-24 size-96 rounded-full bg-white/5" />
        <div className="pointer-events-none absolute top-24 -right-10 size-48 rounded-full bg-white/5" />
      </aside>
      <main className="flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden"><Logo /></div>
          {children}
        </div>
      </main>
    </div>
  );
}
