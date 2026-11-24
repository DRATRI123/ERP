import type { NextConfig } from "next";

// The browser only ever talks to this origin: /api/v1/* is proxied to the FastAPI backend, so
// the refresh-token cookie is first-party and no CORS is needed. In production Caddy routes
// /api and /ws itself and these rewrites are not used.
const API = process.env.ERP_API_INTERNAL_URL ?? "http://127.0.0.1:8000";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Frame-Options", value: "SAMEORIGIN" },
  { key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=()" },
];

const config: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // Uploads pass through the /api/v1 rewrite in local runs; match Caddy's 30 MB (file limit is 25 MB).
  experimental: { proxyClientMaxBodySize: "30mb" },
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${API}/api/v1/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default config;
