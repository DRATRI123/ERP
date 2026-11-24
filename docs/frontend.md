# Web app (frontend)

`frontend/` is a Next.js 16 application (React 19, TypeScript, Tailwind CSS 4, shadcn-style
components on Radix UI, TanStack Query, Zustand, React Hook Form + Zod, Lucide icons, Recharts,
dnd-kit). It holds **no business logic and no data of its own**: every figure comes from the
FastAPI backend, so the database (SQLite or PostgreSQL) can change without touching it.

## How it talks to the backend

* Same origin only: the browser calls `/api/v1/*`. In development and with `start.bat`/`start.sh`
  Next.js proxies it to `127.0.0.1:8000`; in Docker, Caddy routes `/api` to the API and `/ws` to
  the real-time service. No CORS, no API URL in the browser bundle.
* `src/lib/api.ts` is the single API client: attaches the access token, refreshes it once on 401
  (shared by concurrent requests), maps the backend's `{error: {code, message, details,
  request_id}}` to `ApiError` (field errors feed the forms; the request id is shown in toasts).
* Sessions: the access token lives only in memory; the refresh token is an **httpOnly,
  SameSite=strict cookie** with a double-submit CSRF token (`ERP_AUTH_COOKIE=true`). A page
  reload restores the session silently; signing out clears both.
* Every screen is permission-aware (`useBusiness().can(...)`): menu items, buttons and sections
  appear only for members who hold the permission. The **backend enforces** every rule
  regardless — the UI only avoids offering what would be refused.
* Money arrives as decimal strings and is never stored as floats; invoice totals and GST shown
  while billing come from the server's `POST /documents/preview`, so the screen always matches
  what will be saved and printed.
* Live updates: `useRealtime` connects to the Node.js service (token sent in the first message,
  not the URL), subscribes to the selected business and refreshes affected data on events.

## Structure

```
src/app/(auth)/            sign-in (with 2FA step), accept invitation, reset password
src/app/(app)/             every signed-in screen; layout = AppShell (route protection)
src/components/ui/         design-system primitives (button, input, dialog, tabs, table, …)
src/components/layout/     sidebar, top bar, business switcher, command palette, nav config
src/components/data/       data table (server pagination/search/sort), states, stat cards
src/components/pickers/    party picker, product search (barcode), period picker
src/features/<module>/     module components (billing editor, inventory, parties, payments…)
src/hooks/                 business-scoped queries/mutations, shortcuts, realtime
src/lib/                   API client, formatting (₹, dates, decimals), types, CSV, Code 128
src/stores/                Zustand: auth (memory only), workspace (selected business, sidebar)
```

## Keyboard

| Keys | Action |
|---|---|
| Ctrl K or / | Search products, customers, documents; jump to any screen; run actions |
| Alt N | New invoice |
| G then D / S / P / I / C / A / T / R / U | Dashboard, Sales, Purchases, Inventory, Customers, Accounting, GST, Reports, Users |
| [ | Collapse or expand the sidebar |
| F2 / F4 / F9 / Ctrl S | Billing: product search, customer, save & finalize, save draft |
| ? | Shortcut list |

## Design

Forest green (primary) and blue (accent) on white, Inter variable font (bundled, no external
requests), tabular figures for money, light and dark themes from the same tokens
(`src/app/globals.css`), skeletons while loading, empty and error states with retry, toasts for
outcomes. Layouts are responsive: a collapsible sidebar on desktop, a slide-over menu on phones.

## Checks

```bash
npm run typecheck && npm run lint && npm run build
ERP_E2E_EMAIL=… ERP_E2E_PASSWORD=… node e2e/smoke.mjs   # against a running stack
```

CI runs these plus `npm audit`, and an end-to-end job that installs with `setup.sh`, starts with
`start.sh` and drives a real sale in Chromium (stock, payment and books verified through the API).

## Not built yet

* **Documents → file library** and **Automation → custom rules** need backend modules that do
  not exist yet; those pages say so and link to what already works instead of showing fake data.
