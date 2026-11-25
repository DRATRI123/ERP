# Roadmap

A phase is complete only when its features are covered by tests that pass on SQLite **and**
PostgreSQL.

| # | Phase | Status |
|---|---|---|
| 1 | Foundation: architecture, repo, SQLite + SQLAlchemy + Alembic, auth, owner, multi-business, users, roles & permissions, audit, outbox | **Backend done**. Frontend shell still to do |
| 2 | Products, customers, suppliers, warehouses, inventory ledger | **Backend done**. Frontend screens still to do |
| 3 | Invoice engine, numbering series, GST, returns, printing | **Backend done**. Visual designer UI still to do |
| 4 | Double-entry accounting, ledgers, banking, financial reports | **Backend done** |
| 5 | Integrations: WhatsApp, Gmail/SMTP, SMS, payment gateways, Google, Shopify | **Backend done** (185 tests in total); live verification with your accounts pending — see [integrations.md](integrations.md) |
| 6 | Developer platform: API keys & scopes, webhooks, idempotency, rate limits | **Backend done** (197 tests in total) — see [api.md](api.md) |
| 7 | Node.js realtime + webhook delivery, Redis, Rust workers (where benchmarks justify) | **Done** — real-time service, Redis stream + shared rate limits, batch PDF; Rust measured and not needed ([ADR 0001](adr/0001-rust-services.md)) |
| 8 | Custom domains, SSL, encryption at rest, monitoring, backups, deployment | **Done** (220 tests in total) — first real VPS deployment still to be done with [deployment.md](deployment.md) |
| 9 | SQLite → PostgreSQL migration utility and cutover | **Done** (227 tests in total) — see [postgres-migration.md](postgres-migration.md) |
| 10 | Load/security testing, restore drills, production docs | **Done** — see [performance.md](performance.md), [security.md](security.md), [operations.md](operations.md) |

## Web app — what exists

* Next.js frontend covering every module, with permission-aware navigation, business switcher,
  dashboard (server-computed figures; `GET /businesses/{id}/dashboard`, `GET /dashboard/overview`),
  keyboard-first billing counter, invoice designer with live server-rendered preview, dark mode,
  mobile layout, command palette and live updates. See [frontend.md](frontend.md).
* One-click local run: `setup.bat` / `start.bat` / `stop.bat` / `backup.bat` (Windows) and
  `setup.sh` / `start.sh` (macOS, Linux); tested from a fresh clone in CI.
* Docker image for the web app; Caddy routes everything else to it.

## Health-sector business features — what exists

* **Batch and expiry control:**
  * Sales lines without a batch get the batch that expires first, and are split across batches
    when one is not enough (as pharmacy bills show it).
  * Expired batches cannot be sold or delivered.
  * The MRP typed on a purchase line is kept per batch and printed on sales of that batch.
  * Daily `batch.expiring` (90/60/30 days) and `batch.expired` events feed automation rules.
  * Inventory → Expiry shows expired and expiring stock, and drafts the debit note to return it
    against the purchase bill it came from.
* **Licence register:**
  * Drug, FSSAI, AYUSH, trade and other licences, with certificate files.
  * Licences marked for printing appear on every invoice while valid.
  * `licence.expiring` / `licence.expired` events feed renewal reminders.
  * Permission: `licences.manage`.
* **Price lists and schemes:**
  * Customer price lists: a base (selling, wholesale, MRP or purchase) ± a percentage, plus fixed
    prices with quantity breaks.
  * Trade schemes: buy X get Y free (repeating), and quantity discounts, for a product or a
    category, with dates.
  * Schemes apply automatically on sales documents; free goods leave stock at zero value.
* **Credit control:**
  * A per-customer credit hold (only full-payment sales; `credit_limit.override` can bypass it).
  * A credit panel in billing shows dues, overdue amount, limit and price list.
* **Smart reordering** (Inventory → Reorder):
  * The suggested quantity is daily sales × (lead days + cover days) − on hand − open purchase
    orders, and never less than the product's own reorder level asks for.
  * The supplier and price come from the last purchase bill.
  * One click creates one draft purchase order per supplier.
* **Scheduled reports** (Reports → Scheduled e-mails):
  * A daily, weekly or monthly e-mail with sales (vs the previous period), cash by mode, dues,
    profit, best sellers, low stock, expiry and licences due.
  * It goes to up to 10 addresses through the connected Gmail or SMTP.
  * Preview it, or send it now. Failures are shown on the schedule.
* **Manufacturing** (permission `production.manage`):
  * Recipes are bills of materials with wastage %, overhead per batch, shelf life and optional
    quality control.
  * A plan shows the materials needed, what is in stock, the shortages and the estimated cost
    per unit.
  * A production run is one stock document. Materials leave stock (first expiry first, rounded
    up to each unit's precision) at their stock value. The finished batch arrives at material
    cost + overhead, with its expiry set from the shelf life.
  * The books get Dr Inventory / Cr Production Overhead Absorbed for the overhead. That account
    is created automatically in existing businesses.
  * A QC hold blocks sales of the batch until it is released. Rejecting a batch writes it off.
    Cancelling a run reverses it exactly.
* **Staff and salary** (`payroll.manage`; `attendance.mark` for whoever marks attendance):
  * Employees on monthly salary or daily wage, with an optional % commission on their own sales
    (linked to their login as salesperson).
  * Attendance is marked by exception: unmarked days are present, absent is unpaid, a half day
    is half paid, and leave and holidays are paid.
  * A monthly salary run prorates pay by paid days (and by joining or leaving date), and adds
    commission and bonus minus deductions.
  * Posting it charges Salaries & Wages, credits Advances for recoveries, and credits Salaries
    Payable. Paying clears Salaries Payable from cash or bank.
  * Bank and PAN details are visible only with `payroll.manage`.
* **Expense claims and petty cash:**
  * Every staff role can submit a claim (`expenses.claim`) with a photo of the bill, and sees
    only their own claims.
  * Approvers (`expenses.manage`) approve and pay in one step from petty cash, cash or bank,
    which records a normal expense; or they reject with a reason. Nobody approves their own claim.
  * Petty cash is its own account, topped up from the till or the bank, with its balance and
    waiting claims shown.
* **E-way bills** (GST → E-way bills):
  * Lists final invoices, challans and purchase returns moving goods worth over ₹50,000 without
    an e-way bill.
  * Builds the portal's bulk-generation JSON, with transport details and validated vehicle
    numbers.
  * Records the 12-digit number the portal returns, with validity of 1 day per 200 km to
    midnight IST, and prints it on the invoice.
  * The ERP does not call the NIC API itself: that needs a GSP account.
* **Tally export** (Accounting → Tally; `accounting.view` + `data.export`):
  * TallyPrime / Tally.ERP 9 *Import Data* XML for masters: ledgers under the right Tally groups,
    plus customers and suppliers with GSTIN and state.
  * Vouchers for any period: one per journal entry, typed Sales, Purchase, Receipt, Payment,
    Contra, Credit/Debit Note or Journal, in Tally's sign convention, balanced and XML-escaped.
* **WhatsApp catalogue:**
  * One secret link per business (a 32-byte token, replaceable at once), with a QR code for the
    counter.
  * Buyers browse on their phone: the chosen categories, with price, MRP, GST and in/out of stock
    only (no stock figures or costs).
  * An order becomes a **draft sales order** (matched to an existing customer by mobile number,
    so their price list applies), and WhatsApp opens with the order summary addressed to the
    business.
  * Limits: 5 orders an hour per phone and 10 per IP, at most 50 lines, whole quantities up to
    1,000, an optional minimum order, and a honeypot field.
  * The `order.received` event feeds automation (e.g. an e-mail alert). Nothing is invoiced until
    staff confirm.
  * The two public routes are on the security audit's allow-list.
* **All businesses** (owner only, `GET /dashboard/combined?period=today|month|year`):
  * Sales with growth, book net profit, cash and bank balances, receivables and payables with
    overdue amounts, and stock value.
  * Shown per business and in total, for today, this month or this financial year.
  * A six-month sales chart. Clicking a business opens its dashboard.
* **Statutory payroll** (migration 0024): employees carry PF (UAN, basic + DA %, ₹15,000
  cap), ESI (IP number), a professional-tax state and TDS on salary. The salary run calculates:
  * PF: 12% employee and 12% employer, of which 8.33% (on at most ₹15,000) goes to EPS.
  * ESI: 0.75% employee and 3.25% employer, rounded up, when monthly wages are up to ₹21,000.
  * Professional tax by state slab: Gujarat, Maharashtra (₹300 in February), Karnataka, West
    Bengal, Telangana and Andhra Pradesh.
  * TDS under the new regime: ₹75,000 standard deduction, the 87A rebate up to ₹12 lakh, 4%
    cess, projected over the year. A manual figure can override it.

  Posting debits Salaries plus Employer PF & ESI (6015) and credits PF, ESI and PT Payable
  (2220–2222), TDS Payable, Advances and Salaries Payable. `GET /payroll/runs/{id}/statutory`
  gives amounts and due dates. `/ecr.txt` is the EPFO ECR 2.0 file, and
  `/payslips/{employee_id}` a printable payslip. Not covered: surcharge, marginal relief near
  ₹12 lakh, the old regime and its deductions, and Form 16.
* **Stock extras** (migration 0025):
  * **Packs for every business:** a product's "bigger unit" (12 in a box, 25 kg in a bag, 10 in
    a strip) bills as packs + loose; stock and prices stay per base unit.
  * **Barcode labels** (`/inventory/labels`): A4 sticker sheets of 65, 40 or 24 and 50 × 25 /
    38 × 25 mm thermal rolls, with shop name, product, price and MRP; a half-used sheet can skip
    labels. Items without a barcode print their SKU, which billing scans the same way.
  * **Stock audit** (`/stock-audit`): the books' quantity per item and batch, counted by phone
    camera (the browser's BarcodeDetector — Chrome on Android), a USB/Bluetooth scanner or typing.
    The count is kept on the device until posted as a physical count; a full audit sets uncounted
    items to zero.
  * **Scheme claims** (Pricing & schemes → Scheme claims; `/scheme-claims` API): schemes name who reimburses them and their share.
    Unclaimed free goods (at purchase price) and scheme discounts (as given) on final sales
    invoices are listed per manufacturer; a claim locks its lines, settling posts
    Dr Accounts payable / Cr 4240 Scheme Claims Received, rejecting frees the lines.
  * **Sale or return** (`/sale-or-return`): per delivery-challan line, sent / invoiced / returned /
    still with the customer, and "goods back" returns stock at the cost it left at.
  * **Moving from other software:** `POST /data/import/{kind}` understands Busy, Marg, Vyapar and
    Tally column names (Item Name, Main Unit, Tax Category "GST 12%", Party Name, GST No, state
    names…) and makes a code from the name when there is none. `POST /data/import-tally` reads a
    Tally masters XML export (parsed with defusedxml, so entity tricks are refused): Sundry
    Debtors / Creditors with GSTIN, state, address and credit period, and stock items with unit,
    HSN, GST rate and opening quantity at rate, optionally posting opening balances and opening
    stock. Existing names are skipped; any error rolls everything back.
* **Accounts extras** (Accounting tabs; migration 0023):
  * **Budgets:** monthly amounts per income or expense account, optionally per cost centre
    (`PUT /budgets`).
    * The month's report compares budget with actual from the books, with the difference,
      % used and whether it is favourable.
  * **Daily cash closing:** for a day, the cash account's opening, receipts, payments and
    expected balance are shown.
    * The drawer is counted by denomination (₹500 … ₹1 and coins).
    * A difference posts to Cash Shortage (6910) or Cash Excess (4230), journal source
      `cash_closing`. One closing per day per cash account.
  * **Fixed assets screen** (the register existed only in the API): add assets, post
    depreciation to the last month-end, see book values.
    * **Income-tax depreciation by block** (section 32): opening WDV (entered once in
      `settings.it_opening_wdv`), additions used 180 days or more at the full rate and the rest
      at half rate, less sale proceeds (now stored on disposal), rolled forward year by year.
    * Blocks: buildings 5/10%, furniture 10%, plant 15%, cars 15%, hire vehicles 30%,
      computers 40%, intangibles 25%.
  * **Balance confirmation:** a ledger statement (opening, entries, closing) sent as a secret
    link, usually on WhatsApp.
    * The party confirms, or disputes with their balance and a note, without logging in.
    * Public `GET/POST /public/confirm/{token}`: valid 90 days, answered once.
    * Replies show on the party page.
  * **Payment links on invoices:** the existing Razorpay / Cashfree link API now has a button
    on unpaid invoices. It copies the link and offers WhatsApp sharing.
* **GST compliance** (GST page tabs; migration 0022):
  * **GSTR-2B reconciliation** (`POST /gst/2b-reconcile` with the portal's JSON): purchase
    bills of the period are matched by supplier GSTIN and bill number. Numbers like "INV/0012"
    and "inv-12" are treated as the same.
    * Results: matched (within ₹1), amount differs, only in 2B (not recorded), and only in
      books. The last group is the ITC at risk, totalled.
    * Exportable as CSV.
  * **GSTR-9** key tables (4, 5, 6, 9, 17 HSN) for a financial year from the books.
  * **CMP-08** quarter by quarter at the composition rate (1 / 5 / 6 %), with reverse-charge
    tax and due dates, plus the **GSTR-4** year totals.
  * **RCM self-invoices** (section 31(3)(f)): lists reverse-charge purchases from unregistered
    suppliers. Each gets a numbered (`RCM/{FY}/…`), printable self-invoice, once.
  * **Job work / ITC-04** (section 143):
    * Challans to send goods to a job worker and receive them back. Stock moves to and from an
      automatic godown per job worker.
    * ITC-04 lists what was sent and received in the period, and what is still with the job
      worker. Inputs are due back within 1 year and capital goods within 3, with an overdue
      flag. Exportable as CSV.
  * **Section 194Q tracker:** purchases per supplier in the year against ₹50 lakh, with TDS at
    0.1% on the excess.
  * **Form 26Q deductee file:** a CSV of payments with TDS for the quarterly return.
  * Not built: direct e-invoice / e-way bill generation through a GSP. It needs a paid GSP
    account and its API credentials. IRN and e-way numbers are still recorded after generating
    them on the portal.
* **Offline billing:**
  * The web app keeps a copy of the product list (up to 5,000 products, refreshed every 30
    minutes) and the signed-in profile in the browser. Tokens are not stored.
  * With no connection, product search and barcode lookup use that copy. The bill shows an
    approximate total, and *Save & finalize* puts the sales invoice in a local queue.
    * Walk-in bills must be paid in full.
    * Only new sales invoices can be saved offline.
  * The queue is sent when the connection returns: automatically every minute, or with
    *Send now* from the cloud badge in the top bar.
    * Each bill is created with its `client_ref`, which makes a retried create return the same
      document, and is then finalized.
    * A "paid in full" bill is paid for the server's exact total.
    * Refused bills stay in the queue with the reason until discarded.
  * A service worker (production builds only) caches the built files and visited pages so the
    app opens without internet. API calls are never cached. It also ships with an install
    manifest and icon.
* **Hindi and Gujarati:**
  * The account menu has a Language choice (English / हिन्दी / ગુજરાતી), remembered per
    device. It covers the menu, phone tab bar, top bar, page titles and the billing screen;
    other text stays English.
  * Print templates have *Printed labels*: `en`, `hi`, `gu`, `en_hi` or `en_gu` (bilingual,
    e.g. "Bill to / बिल प्राप्तकर्ता"), on A4 and thermal. Titles, column heads, totals and copy
    labels are translated. Amount in words stays English.
  * Templates fall back to Noto / Nirmala / Mangal / Shruti fonts for Devanagari and Gujarati.
    The Docker image already installs Noto.
* **Field sales** (module `field`, migration 0021; under `/businesses/{id}/field`):
  * **Beats:** a named route of customers in visiting order, a weekday and a salesman (any
    member of the business).
  * **The day** (`GET /field/day`): the salesman's beat for today's weekday (or one chosen).
    Each customer shows phone, address, dues (with `customer_balances.view`), credit hold, last
    visit and today's outcome. Other salesmen's days are for `reports.view` only.
  * **Visits** (`POST /field/visits`): outcome (order, payment, both, no order, closed, other),
    note, optional GPS position, the order or payment it produced, and the next visit date.
  * **Orders** from the field are sales orders with the salesman as `salesperson_id`; invoices
    converted from them keep it.
  * **Targets** (`PUT /field/targets`, `users.manage`): monthly sales and collection targets.
    `GET /field/targets` shows for each salesman:
    * net sales (invoices less returns where they are the salesperson);
    * orders, collections (receipts they recorded) and visits / productive visits;
    * % achieved and whether they are on pace for the days gone.
  * Without `reports.view`, a salesman sees only their own row.
* **Supplier bill import** (`POST /purchases/import/preview` with the file, then `POST /purchases/import`):
  * Reads the distributor's CSV or Excel bill. Columns are recognised by their usual names
    (item name / product / description, code, barcode, HSN, qty, free / scheme qty, rate / PTR,
    disc %, GST %, batch, expiry, MRP).
  * Expiry dates like `03/27`, `12/2026`, `Feb-28` or full dates are understood. A month-only
    date means the month's end.
  * Total lines are skipped.
  * Each line is matched to a product by: this supplier's remembered name or code, then
    barcode, then our SKU / their code, then exact name. Otherwise up to three suggestions are
    offered by shared words.
  * The draft purchase bill keeps batch, expiry and MRP. Free quantity becomes a zero-rate line.
  * The choices are remembered per supplier in `supplier_item_maps`, listed and forgettable
    under `/suppliers/{id}/item-names`.
* **Damage & breakage claims** (module `damage`, migration 0020):
  * Recording damage posts a stock adjustment, so the cost goes to Stock adjustments.
  * Naming a supplier opens a claim for the cost or a chosen amount. Settling it with their
    credit note posts Dr Accounts payable / Cr Stock adjustments (journal source `claim`).
  * Rejected claims keep the loss. A claim with no supplier is simply written off.
  * The summary shows what is waiting, what was recovered and the net loss.
* **Loyalty points** (module `loyalty`, off by default):
  * Settings: points per ₹100 billed, value per point, and a minimum before points can be used.
  * Finalized sales invoices with a customer earn points on what was not paid with points.
  * Points are spent with payment mode `loyalty` (Dr Loyalty Rewards expense, Cr Receivable),
    capped at the balance.
  * Cancelling the invoice takes its points back; cancelling the points receipt returns them.
  * Manual adjustments are possible, and the overview lists members and the value still owed.
* **Cheques** (module `cheques`, migration 0019):
  * A register of post-dated cheques received and issued (`/cheques`). A cheque is not in the
    books while pending.
  * Clearing it records the receipt or payment (mode cheque) and settles the chosen bills, up to
    what each still owes. The rest is an advance.
  * Bounced: a cleared cheque's payment is cancelled, so the bills are due again.
  * Events `cheque.due` (daily, once its date arrives) and `cheque.bounced` feed automation.
  * **Cheque printing** (`/cheques/{id}/print`, issued cheques only):
    * Laid out for a CTS-2010 leaf (203 × 92 mm): date in eight boxes, payee, amount in Indian
      words over two lines, `**12,500.50/-` in figures, and an optional A/C PAYEE crossing.
    * Positions in mm, plus overall shifts, are stored in `settings.cheque_layout`
      (`GET/PUT /cheque-layout`). `/cheque-layout/sample` gives a test print.
* **TDS** (module `tds`):
  * A payment carries `tds_amount` and `tds_section`. `amount` is the money that moved, and
    `amount + tds_amount` is what settles the bills.
  * Payments out post Cr TDS Payable. Receipts where the customer deducted TDS post Dr TDS
    Receivable.
  * Suppliers carry their PAN and usual section. Suggested rates for 194C/J/H/I/Q/A/T pre-fill
    the form, and the user confirms them.
  * `GET /reports/tds?direction=out|in` gives entries with PAN, gross, rate and TDS, a
    section-wise summary and, for TDS we deducted, the month-by-month position. That covers
    deducted, deposited and due date (7th of next month; 30 April for March), with an overdue
    flag.
  * `POST /tds/deposits` records a challan (serial no., BSR code) and posts Dr TDS Payable,
    Cr Bank (journal source `tds`).
* **Interest on overdue bills** (module `interest`, off by default):
  * Simple interest at the party's rate (or the business default in `settings.interest`, with
    grace days), day by day, on what was actually unpaid after the due date. Part payments stop
    interest on that part from their date.
  * `POST /interest/debit-notes` drafts one debit note per invoice. Each note carries the
    invoice's HSN and GST rate, because interest on late payment is part of the value of
    supply.
  * The next calculation starts after the last note, so interest is never charged twice and
    never charged on interest.
* **Modules per business** (Settings → Modules; `GET/PUT /businesses/{id}/modules`):
  * The ERP serves different kinds of business. The core is always on: sales, purchases, stock,
    parties, payments, accounting, GST, reports, users and settings.
  * Each business switches the optional modules on or off: pharmacy, manufacturing, price lists &
    schemes, WhatsApp catalogue, staff & salary, expense claims, licences, automation and
    developer API.
  * Stored in `businesses.settings["modules"]`. Pharmacy is off by default and everything else
    is on. Quick set-up buttons apply a preset per business type: pharmacy, manufacturer,
    distributor, retail or services.
  * Switching a module off hides its menu, screens and fields. Its pages say the module is off,
    with a link to switch it back on. Data is kept.
  * The pharmacy billing rules (prescriptions, near-expiry discount) apply only while Pharmacy is
    on. Migration 0018 switches Pharmacy on for businesses that already have doctors or medicines
    with a salt or schedule.
  * `PATCH /businesses/{id}` now merges `settings` instead of replacing them, so editing the
    profile never wipes modules or other keys. A key sent as `null` is removed.
* **Pharmacy** (migration 0017; only for businesses with the Pharmacy module on):
  * Products carry a salt / composition, rack, drug schedule (None, G, H, H1, X, narcotic),
    PTR and PTS, and a pack size and name for loose selling.
  * Searching a salt finds every brand. `GET /products/{id}/substitutes` lists other active
    products with the same salt (spacing and case ignored), cheapest first.
  * **Loose selling:** stock and prices stay per base unit (tablet). The bill shows
    "2 strip + 3 loose" and has a +1 strip button.
  * **PTR / PTS** are price tiers on a sales document. A missing rate falls back to wholesale,
    then retail. A customer's price list still comes first.
  * **Doctors** (`/doctors`; manage with `customers.manage`): name, registration number,
    qualification, clinic, address, phone.
  * **Prescriptions:** a sales invoice with a Schedule H, H1, X or narcotic drug cannot be
    finalized without the doctor and the patient's name. H1, X and narcotics also need the Rx
    number. All three print on A4 and thermal bills.
  * **Schedule register** (`GET /reports/schedule-register`, `reports.view`): one row per line
    sold, with date, bill, patient and address, doctor with registration number, Rx, drug,
    batch and quantity. Defaults to H1, X and narcotics; `schedule=` chooses others.
  * **Doctor-wise sales** (`GET /reports/doctor-sales`): bills, taxable value and bill value per
    doctor.
  * **Near-expiry discount** (Settings → Modules, `business.settings`): a batch expiring
    within N days gets X % off automatically on sales invoices.
    * The batch is the chosen one, or the first-expiry batch when it covers the whole line.
    * The line is flagged `near_expiry`.
    * Lines with their own discount, and free scheme lines, are left alone.
* Fixed: the daily overdue scan used the UTC date, so invoices became overdue a day late after
  midnight IST. It now uses the business date.

## Document library and automation — what exists

* **Document library** (`/businesses/{id}/files`): folders, tags, notes, versions, trash with
  restore and permanent delete. Content is identified by its bytes, not its name (HTML, SVG,
  scripts and executables are refused). Identical content is stored once per business, and
  downloads are sandboxed attachments. A file can be attached to an invoice or bill, a customer
  or supplier, a product, an expense or a payment. It is then visible only to members who can
  see that record, including branch scope. Permissions: `files.view` and `files.manage`.
  The limit is `ERP_MAX_UPLOAD_MB` (default 25).
* **Automation rules** (`/businesses/{id}/automation`): *when* an event happens (an invoice is
  finalized, overdue, paid or cancelled; a purchase; a payment; low stock; a new customer; an
  expense), *if* conditions hold, *after* an optional delay of up to 90 days, *then* either send
  the invoice to the customer (e-mail, WhatsApp or SMS) or e-mail you and your team with
  `{placeholders}`. Behaviour:
  * Conditions are checked again when a delayed run is due, so a reminder is skipped once the
    invoice is paid.
  * Events from before a rule was created are never replayed.
  * Every run, and the reason a run was skipped or failed, is kept in the run history.
  * The background worker runs the rules. Permission: `automation.manage`.
* Web app: Documents page (folder tree, drag-and-drop upload, list and grid views, image, PDF
  and text preview, versions, trash); an attachments panel on invoices, customers, suppliers
  and products; and an Automation page with templates, a rule builder and run history.
* 254 tests in total, passing on SQLite and PostgreSQL.

## Phase 10 — what exists

* **Load test** (`scripts/loadtest.py`) against the real server, with integrity checks
  afterwards: books, stock ledger, gapless numbering, and stock sold equal to the stock drop.
  90 users ran on SQLite and PostgreSQL in realistic and stress profiles with zero errors (see
  [performance.md](performance.md)). Six concurrency and capacity problems were found and fixed:
  the SQLite writer queue, login hashing outside the write lock, pool sizing, the PostgreSQL
  connection budget, 503 load shedding, and bounded Argon2 hashing. API processes now scale per
  CPU core.
* **Security audit suite** over all 237 routes: the public-route allow-list, token forgery,
  cross-business 404s, least privilege, owner-only routes, hostile input and secret leakage.
  Six findings were fixed, and the review is recorded in [security.md](security.md).
* **Dependency audits** (`pip-audit`, `npm audit`; `ws` upgraded) and the **ruff** rule set,
  including Bandit-style security rules, run in CI. The whole codebase is lint-clean.
* **Automated monthly restore drills** in the worker, with the result on the health page;
  `restore-drill` CLI.
* **Business-timezone calendar** (`ERP_TIMEZONE`) for every "today".
* Production documentation: [operations.md](operations.md) runbook, security, performance.

## Phase 10 — still to do outside this repository

* First deployment on the real VPS (checklist in [deployment.md](deployment.md) §8), then a
  restore practice on a spare server.
* An external penetration test, if you want independent assurance beyond the automated audit.

## Phase 9 — what exists

* `migrate-to-postgres` CLI:
  * preflight checks: source at head, SQLite integrity and foreign keys, target migrated and
    empty;
  * copies through the ORM types, parents first, with self-references back-filled (and
    `updated_at` preserved);
  * one target transaction, committed only after verification: row counts, per-table checksums
    over every column, books and stock checks;
  * `--dry-run` rehearsal and a JSON report.
* `compare-databases` for checking at any later time.
* Maintenance mode (CLI and owner API): changes refused with 503, reads and sign-in allowed,
  worker paused.
* Tests:
  * a full copy with the application then run on the copy (same trial balance and stock, new
    invoices work);
  * dry run, refusal cases, and a corrupted value aborting the whole copy;
  * checksum canonicalisation;
  * the CLI flow and maintenance mode;
  * both directions (SQLite → PostgreSQL in the SQLite suite, PostgreSQL → SQLite in the
    PostgreSQL suite).
* Benchmark: about 5,700 rows/s including verification.

## Phase 9 — deliberately deferred

* Online (zero-downtime) migration through replication: unnecessary at this scale; the window is
  minutes.
* PostgreSQL point-in-time recovery (WAL archiving).

## Phase 8 — what exists

* **Two-factor sign-in** (RFC 6238 TOTP, any authenticator app): QR setup, confirm-to-enable,
  10 single-use recovery codes, replay protection, wrong codes count towards account lockout,
  short-lived challenge token bound to the password; owner/CLI reset for lost devices.
* **Security events** (new device, lockout, 2FA changes, password change, recovery-code use)
  visible to the user and e-mailed through optional system SMTP.
* Per-IP limits on sign-in and token endpoints (Redis-shared); strict CSP, frame, COOP/CORP,
  permissions policy and HSTS headers; HTTPS redirect; optional httpOnly + SameSite=strict
  refresh-token cookie with double-submit CSRF token.
* **Custom domains**: DNS TXT ownership proof, daily re-verification with auto-disable,
  Host-header allow-list (421 for unknown hosts), Caddy on-demand TLS `ask` endpoint, optional
  default business per domain (grants nothing).
* **Backups**: SQLite backup API or `pg_dump`, chunked AES-256-GCM with authenticated header and
  truncation detection, verification after writing (decrypt + checksum + integrity check), daily
  schedule, manual runs via the worker, GFS retention, encrypted Google Drive off-site upload
  (resumable), download, CLI `backup` / `verify-backup` / `restore`, restore drills in the test
  suite on SQLite and PostgreSQL.
* **Monitoring**: `/ready`, token-protected Prometheus `/metrics` (HTTP counters and latency
  histograms, queue depth and age, worker heartbeat, backup age), owner `/system/health` with
  plain-language warnings; worker heartbeat.
* **Deployment**: backend and realtime Dockerfiles (non-root, read-only root filesystem),
  Compose stack (Caddy, API, worker, realtime, Redis, optional PostgreSQL, internal network),
  Caddyfile, deployment guide; CI builds the images and validates Compose and Caddy configs.

## Phase 8 — deliberately deferred

* Running on a real VPS: Docker images and the Caddyfile could not be built or run in the
  development sandbox; CI builds and validates them, and [deployment.md](deployment.md) §8 lists
  the checks for the first deployment.
* WebAuthn / passkeys (TOTP covers 2FA now); forcing 2FA for every member (owner policy).
* Point-in-time recovery for PostgreSQL (WAL archiving) — daily dumps for now.
* Database-level encryption at rest: use full-disk encryption on the VPS; secrets inside the
  database (integration credentials, 2FA secrets) are already encrypted by the application.
* Alertmanager / Grafana dashboards (metrics are exposed; wiring them up is environment work).

## Phase 1 — what exists

* Owner account (CLI only), invitations, accept-invite, password reset tokens, change password.
* Login with Argon2id, account lockout, JWT access tokens + rotating refresh tokens with reuse
  detection, session list/revoke, login history.
* Businesses (create/edit/archive/restore), auto-created head office, financial year and roles.
* Branches, financial years, GSTIN/PAN/state-code validation.
* 42 permission codes (two added in Phase 2; 51 now, with files, automation, licences, production, payroll, attendance and expense claims), 7 built-in roles, custom roles, per-member grant/deny overrides,
  branch scoping, 30-user cap, anti-escalation rules, business-isolation via 404.
* Audit log (per business and owner-wide), transactional outbox, structured errors/logs,
  request ids, DB integrity check and verified online backup.

## Phase 4 — what exists

* **Chart of accounts** seeded per business (cash, bank, debtors, inventory, input/output GST
  per head, RCM payable, creditors, capital, retained earnings, sales, COGS, expenses…),
  extendable with custom and sub-accounts; system accounts are locked to their purpose.
* **Automatic postings** in the same transaction as the business event: sales invoices,
  debit/credit notes, delivery challans, purchase invoices (ITC, non-claimable tax, landed cost,
  reverse charge), purchase returns, receipts, payments, advances, refunds, opening stock,
  adjustments, counts, expenses, depreciation, asset purchase and disposal. Cancelling anything
  posts an exact reversal. Round-off and stock price differences have their own accounts.
* **Journal rules** enforced in one place: balanced or refused, one side per line, never into
  a closed year, immutable once posted, reversal instead of edit, one posting per event.
* **Manual journals** (with reversal) and **opening balances** (party-wise debtors/creditors,
  difference to Opening Balance Equity). Manual postings to control accounts are refused so
  sub-ledgers can never drift.
* **Expense vouchers** with GST input credit, paid from cash/bank or owed to a supplier,
  cost centres, cancellation.
* **Fixed assets**: register, capitalisation, straight-line and written-down-value
  depreciation (daily pro-rata, never below salvage, never doubled), disposal with gain/loss.
* **Bank reconciliation**: CSV/JSON statement import (duplicate-safe), auto-matching by amount,
  date window and reference, manual match/unmatch, reconciliation statement.
* **Year end**: close a financial year (results to Retained Earnings, books locked, next year
  created), owner-only reopen.
* **Reports**: trial balance, general ledger, day book, cash book, bank book, customer and
  supplier ledgers, receivable/payable aging (with advances), profit & loss (by branch / cost
  centre), balance sheet, cash flow (direct method), and an integrity check.
* **Cross-checks** after every accounting test: trial balance and balance sheet balance, every
  journal balances, and the Inventory account equals the stock valuation.
* `python -m app.cli accounting-backfill` posts journals for activity recorded before
  accounting existed (idempotent).

## Phase 4 — deliberately deferred

* **Multi-currency** and **TDS/TCS** deductions.
* **Budgets** and **cost-centre allocation rules** (lines are tagged; allocation is manual).
* **Bank feeds** (live statement download) — via integrations in Phase 5.
* **Applying a credit note directly against an invoice** without a payment — today the net is
  correct in the books and outstanding reports; a settlement document can follow.

## Phase 3 — what exists

* **11 document types** in one engine: quotation, estimate, proforma, sales order, delivery
  challan, sales invoice (tax invoice / bill of supply / export), credit note (sales return),
  debit note, purchase order, purchase invoice, purchase return.
* Draft → final → cancelled lifecycle. Drafts are freely editable; finalizing atomically
  assigns the number, freezes buyer/seller details, posts stock and takes counter payments.
  Finalized documents are immutable; cancellation reverses stock exactly.
* **Conversions** with line links: quotation → order → invoice, challan → invoice (stock moves
  only once), invoice → credit/debit note, PO → purchase invoice → purchase return. Returns and
  challan invoicing are capped by what remains on the source lines.
* **GST engine**: CGST+SGST / UTGST / IGST by place of supply, exports & SEZ, cess,
  tax-inclusive and exclusive pricing (inclusive totals stay exact), percent and flat
  discounts, exempt / nil-rated / non-GST / zero-rated lines, reverse charge (tax shown, not
  collected), composition and unregistered sellers (bill of supply), round-off to the rupee.
  **Versioned HSN rates** with effective dates override product rates.
* **Purchases**: supplier invoice numbers (duplicates blocked), ITC eligibility per line
  (non-claimable tax is added to stock cost), landed cost allocated by value.
* **Payments**: receipts and payments with allocation to many documents, partial payments,
  advances, refunds against credit notes / purchase returns, payment status, due dates from
  credit days, overdue filter, party outstanding, credit-limit enforcement (with an override
  permission), payment cancellation.
* **Numbering**: configurable series per document type and branch with `{FY}`, `{FYS}`,
  `{BRANCH}` placeholders, yearly reset, gap-free under concurrency, GST 16-character rule.
* **Printing**: templates per business / branch / document type; A4, A5, A6, 58 mm, 80 mm and
  custom paper; colours, fonts, columns, header/footer, terms, bank details, watermark, logo,
  signature and stamp images, UPI payment QR, e-invoice QR, amount in words (lakh/crore),
  multiple copies. HTML print view and PDF (headless Chromium), both locked down with a CSP.
* **GST outputs** (prepared, never claimed as filed): sales & purchase registers, HSN summary
  (B2B/B2C), GSTR-1 (B2B, B2CL, B2CS, exports, CDNR, CDNUR, nil, HSN, documents issued),
  GSTR-3B (3.1, 4, 5), e-invoice JSON (IRP schema 1.1), and recording of IRNs issued by the IRP.
* `client_ref` makes document creation idempotent (offline devices, external apps); a preview
  endpoint prices a bill without saving it.

## Phase 3 — deliberately deferred

* **Direct GSP/IRP and e-way bill API calls** need an authorised GSP account and credentials —
  they arrive as connectors in Phase 5. Until then the e-invoice JSON is uploaded on the portal
  and the returned IRN recorded here.
* **Drag-and-drop invoice designer** is a frontend feature; its backend (template config,
  rendering, preview) is done.
* **Price lists, salesperson commission reports, GSTR-2B reconciliation, purchase approvals,
  GRN** — next to the accounting work in Phase 4/5.
* Cancelling a GST invoice is allowed until an IRN exists. After filing, corrections should be
  made with credit/debit notes.

## Phase 2 — what exists

* Units (12 GST-UQC defaults per business), nested categories, brands.
* Products: goods/services, SKU + barcode (mutually unique, scanner lookup), HSN/SAC
  validation, purchase/retail/wholesale price and MRP, tax-inclusive flag, GST and cess rates,
  reorder level/quantity, custom attributes, batch / serial tracking, archive/restore.
  Tracking fields lock once stock has moved.
* Customers and suppliers (one party can be both, needing both permissions), GSTIN-derived
  state and PAN, GST registration type, groups, credit limit/days, billing & shipping
  addresses with defaults, search by name/phone/GSTIN/code/e-mail.
* Warehouses linked to branches (a `MAIN` warehouse is created with every business); branch
  scoping applies to warehouses, stock and documents.
* Stock engine: append-only ledger, balances, weighted average **or** FIFO per business
  (locked once stock exists), exact Decimal values with no rounding residue, row locking so
  concurrent issues never oversell, gap-free document numbers per financial year.
* Documents: opening stock, adjustments (with reasons), transfers (FIFO layers travel with the
  stock), physical counts (post only the differences), cancellation by exact reversal.
* Batches with mfg/expiry dates, serial numbers per unit with history.
* Reports: balances, movement history, valuation by warehouse, low stock, expiring batches,
  serial lookup. Costs and values are hidden from users without `stock_value.view`.
* Events: `product.created/updated/deleted`, `customer.created/updated/deleted`,
  `stock.updated`, `stock.transferred`, `stock.low` (only when crossing the reorder level).

## Phase 2 — deliberately deferred

* **Price lists**, customer/supplier **outstanding balances** and **transaction history** —
  they need invoices and payments (Phases 3–4).
* **Attachments and product images** — come with secure file storage (Phase 8).
* Negative stock is never allowed. A per-business "allow negative billing" option can be added
  with the invoice engine if needed.
* Back-dated movements are valued at posting time (perpetual inventory); they do not
  retroactively revalue later issues.

## Phase 1 — deliberately deferred

* **Frontend** (Next.js) — next step before Phase 2 so the foundation is usable in a browser.
* Mobile/e-mail OTP, Google OAuth, TOTP 2FA, passkeys — need the communication integrations
  (Phase 5) and land in Phase 8 security hardening; the session model already supports them.
* IP-level rate limiting — needs Redis (Phase 7).
* Refresh token in an `HttpOnly` cookie with CSRF protection — added together with the frontend.
