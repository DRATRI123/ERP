import "./globals.css";

import type { Metadata, Viewport } from "next";

import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: { default: "Private ERP", template: "%s · Private ERP" },
  description: "Billing, GST, inventory and accounting for your businesses",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: [{ media: "(prefers-color-scheme: light)", color: "#ffffff" }, { media: "(prefers-color-scheme: dark)", color: "#121a17" }],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-IN" suppressHydrationWarning>
      <body className="min-h-dvh">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
