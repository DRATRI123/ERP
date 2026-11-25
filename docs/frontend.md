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

### Screen sizes

* **Desktop** uses the full width of the screen (no fixed maximum). The sidebar groups (Main, Money,
  Manage) fold away; a folded group still shows the page you are on. `[` collapses the sidebar to icons.
* **Phones** get a bottom tab bar (Home, Sales, a large **+ New bill** button, Stock, More). "More"
  opens the full menu.
* Tables turn into cards on phones. A column chooses its place on the card with `mobile`:
  `"title"`, `"aside"` (top right, e.g. the amount), `"row"` (label and value) or `"hide"`.
* Dialogs open as bottom sheets on phones and as centred cards from the `sm` breakpoint up.
* On a phone, the bill editor shows each line as a card with − / + quantity buttons. The total and
  **Save & finalize** stay pinned above the tab bar.
* Tabs and header buttons scroll sideways instead of wrapping onto several lines.
* No page may be wider than the screen. `globals.css` lets every grid child shrink
  (`:where(.grid) > * { min-width: 0 }`), so a long name is truncated instead of pushing the page wider.

## Checks

```bash
npm run typecheck && npm run lint && npm run build
ERP_E2E_EMAIL=… ERP_E2E_PASSWORD=… node e2e/smoke.mjs   # against a running stack
```

CI runs these plus `npm audit`, and an end-to-end job that installs with `setup.sh`, starts with
`start.sh` and drives a real sale in Chromium (stock, payment and books verified through the API).

## Help and guides

* Every page has an **ⓘ** button next to its title. It opens that page's step-by-step guide in
  a side panel, with related guides one click away.
* **Help & guide** (sidebar, or `g` then `h`) collects all the guides, with search and a
  "How do I…" list: sales order, purchase order vs purchase bill, credit note, opening stock,
  e-way bill, salaries, Tally and more.
* The guides live in `src/features/help/content.ts`. Each topic lists the routes it belongs to,
  and its steps name the real buttons, so update the text when you rename a button.

## Expiry, licences, pricing

* **Billing:**
  * Purchase lines of batch-tracked products ask for the batch number, expiry and MRP.
  * Sales lines offer a batch picker that defaults to *Auto (first expiry)*.
  * Rates come from the customer's price list unless you type one.
  * Scheme free goods appear as green rows.
  * Picking a customer shows their dues, overdue amount, limit, hold and price list.
* **Inventory → Expiry:** stock expired and expiring within 30/60/90 days, the batches, and
  *Draft debit note* to return them to the supplier.
* **Licences:** the register, with status, days left, the certificate, and whether the licence
  prints on invoices.
* **Manufacturing:**
  * Recipes: materials with wastage, overhead, shelf life and QC.
  * *Record production* with a live material plan, showing shortages and cost per unit.
  * Runs list with a QC badge. A side panel shows costs, the materials used, release/reject
    and cancel.
* **Staff & Salary:**
  * An attendance grid: click a day to cycle absent, half day, leave, holiday, present.
  * Salary runs: calculate a month, type bonuses and deductions, post to the books, mark paid.
  * Employees.
* **Expense claims:**
  * A new claim takes a photo of the bill (the phone camera opens directly).
  * Approvers see the queue and approve and pay from petty cash in one step.
  * A petty cash balance card, with a top-up.
* **GST → E-way bills:** pick the invoices, enter the distance and the vehicle, download the
  portal JSON, then save each e-way bill number.
* **Accounting → Tally:** download the masters or this year's vouchers.
* **WhatsApp catalogue:**
  * Settings: title, welcome text, your WhatsApp number, categories, minimum order, stock display.
  * The link, with a QR code, copy, share on WhatsApp, and replace.
  * Incoming orders, each linking to its draft sales order.
  * The buyer's page `/shop/{token}` is public, made for phones and outside the app shell:
    search, add/remove, send the order, then continue on WhatsApp.
* **All businesses** (owner): combined totals, a per-business table and a six-month chart; pick
  a period at the top.
* **Inventory → Reorder:** what to buy, how long the stock lasts and from whom. Pick the
  quantities and create the purchase orders.
* **Reports → Scheduled e-mails:** schedules with a preview and *Send now*.
* **Pricing & Schemes:** price lists (base ± %, fixed prices and slabs, linked customers) and
  schemes (free goods or discount, by product or category, with dates).

## Documents and automation

* **Documents** is the file library. It has a folder tree, drag-and-drop upload (several files at
  once), list and grid views, and search. Clicking a file opens a side panel with:
  * a preview (images, PDF, text), fetched with your session as a private blob, never a public URL;
  * rename, move, tags and notes;
  * version history, with a download of any version;
  * remove to trash, restore, and delete for ever.
* **Attachments** (`<Attachments entityType entityId />`) appears on invoices, bills, customers,
  suppliers and products. You can drop files straight onto it.
* **Automation** offers templates and a When / Only if / Wait / Then rule builder. It shows a live
  plain-English summary, an on/off switch per rule, and the run history with the reason for each
  skipped or failed run.
