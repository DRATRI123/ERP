# Private ERP

A private billing, accounting, inventory and GST ERP for running several of my own businesses
from one application. One owner account; up to 30 internal users per business; each business
has its own books, branches, GST registration, financial years and numbering.

It is **not** a SaaS product: there is no public sign-up, subscriptions or tenant admin.

* [Architecture](docs/architecture.md) – service boundaries, security model, database strategy
* [Database schema](docs/database-schema.md) – tables, conventions, planned tables per phase
* [Roadmap](docs/roadmap.md) – phases and current status

## Status

* **Phase 1 backend — complete:** authentication, owner, multi-business management, branches,
  financial years, users, roles, permissions, audit trail, outbox.
* **Phase 2 backend — complete:** products, units, categories, brands, customers & suppliers,
  warehouses, and a stock engine with weighted-average/FIFO valuation, batches & expiry, serial
  numbers, opening stock, adjustments, transfers, physical counts and reversals.
* **Phase 3 backend — complete:** 11 sales & purchase document types, GST engine
  (CGST/SGST/UTGST/IGST, cess, inclusive pricing, RCM, composition, exports), versioned HSN
  rates, conversions and returns, payments and credit limits, numbering series, printable
  templates (A4–thermal, PDF, UPI QR), GSTR-1/3B preparation and e-invoice JSON.
* **Phase 4 backend — complete:** double-entry books posted automatically from every
  invoice, purchase, payment, stock movement and expense; manual journals, opening balances,
  fixed assets & depreciation, bank reconciliation, year closing; trial balance, ledgers,
  day/cash/bank book, aging, P&L, balance sheet and cash flow.
* Next: Phase 5 (integrations). The Next.js frontend is still to be built.

After upgrading a database that already has invoices or stock from earlier phases, run
`python -m app.cli accounting-backfill` once to post their journals.

## Running the backend

Requires Python 3.12+.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # add ,postgres for PostgreSQL support

cp .env.example .env
# put a long random value in ERP_SECRET_KEY:
python -c "import secrets; print(secrets.token_urlsafe(64))"

python -m app.cli migrate          # create / upgrade the database (SQLite: backend/data/erp.db)
python -m app.cli create-owner --email you@example.com --name "Your Name"   # prompts for password
uvicorn app.asgi:app --reload      # API docs: http://localhost:8000/api/v1/docs
```

Operations:

```bash
python -m app.cli db-check         # SQLite integrity + foreign-key check
python -m app.cli db-backup        # online, verified backup into backend/backups/
```

## Tests

```bash
cd backend
pytest                                                    # SQLite
ERP_TEST_DATABASE_URL=postgresql+psycopg://user:pw@localhost/erp_test pytest   # PostgreSQL
```

The schema in tests is built from the Alembic migrations, and a test fails if the models and
migrations drift apart. CI runs the suite on both databases.

## Typical first steps through the API

1. `POST /api/v1/auth/login` → access + refresh token.
2. `POST /api/v1/businesses` → creates the business with a head office, current financial year
   and the built-in roles.
3. `GET /api/v1/businesses/{id}/roles`, then `POST /api/v1/businesses/{id}/members` with an
   e-mail and role → returns a one-time `invite_token` to give to that person.
4. They call `POST /api/v1/auth/accept-invite` with the token and their chosen password.
