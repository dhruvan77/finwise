# FinWise AI Backend - Implementation Report

## Implemented (all backed by real code + tests)
Auth/JWT validation + profile + accounts · transaction CRUD with filters/pagination/sorting · CSV, Excel, PDF (PyMuPDF) import with pluggable parser registry,
dedupe, validation, import jobs · rule + ML (TF-IDF/LogReg) + learned-from-correction categorization with confidence · internal-transfer detection ·
analytics (summary, monthly, categories, trends) · recurring/subscription detection with user review · budgets · forecasting (weighted average → Holt damped
smoothing via statsmodels, 80% bands, daily balance projection 7/30/90d, end-of-month, shortage detection, category forecast) ·
anomaly detection (robust z-score, withdrawals, duplicates, surges, new categories, recurring changes, cash-flow change, Isolation Forest) ·
Financial Digital Twin (state + daily snapshots, refreshed on every data change) · what-if simulator (purchase cash/EMI, loan, savings, category cut,
salary delay/loss, unexpected expense, income increase, subscription; 30/60/90/180d) · stress tests · goals with feasibility · opportunity-cost calculator ·
notifications (DB-backed, channel interface for email/push) · AI coach (Groq GPT-OSS 120B, 13 server-bound tools, limits, history) · data export/deletion ·
rate limiting, CORS, redacted logging, upload validation · synthetic Indian dataset + seed script · Alembic migration · Dockerfile/compose · docs.

## API endpoint groups (all 60+ listed endpoints from the brief + extras)
System/Auth · Users/Accounts/Categories · Transactions · Import (+demo) · Analytics · Budgets · Recurring · Predictions · Anomalies (+status) · Financial twin ·
Simulations (+opportunity-cost) · Stress tests · Goals · AI coach · Notifications · Data management. See `docs/API.md`, `docs/openapi.json`.

## Database (18 tables, `docs/DATABASE_SCHEMA.md`, migration `alembic/versions/0001_initial_schema.py`)
users, accounts, categories, category_rules, import_jobs, transactions, budgets, recurring_payments, financial_profiles, financial_snapshots, predictions,
anomalies, goals, simulations, stress_tests, notifications, ai_conversations, ai_messages. Money = `NUMERIC(14,2)`.

## Tests actually executed
| Run | Result |
|---|---|
| `pytest -q` from the **unzipped ZIP in a fresh virtualenv** (SQLite) | **88 passed** in ~31 s |
| Same suite against **PostgreSQL 16** (`TEST_DATABASE_URL`, local server) | **88 passed** |
| `alembic upgrade head` on PostgreSQL 16 | applied cleanly, 18 tables + alembic_version |
| `uvicorn` on PostgreSQL with seeded demo user; curl of `/health`, `/docs`, `/simulations/purchase`, `/anomalies` | worked |

Coverage: health, auth rejection (missing/garbage/expired token), transaction CRUD & validation, pagination/filter/sort, user isolation, categorization (rules, learning, transfers),
CSV/Excel/PDF import incl. dedupe, scanned-PDF handling and upload validation, summary/budget math (hand-computed expectations), recurring detection (and non-detection of irregular repeats),
forecasting sufficiency behaviour, anomalies, purchase/EMI/loan/other simulations, stress tests, goals, twin updates, notifications, AI tool execution with mocked LLM (tool safety, limits, persistence),
export/delete, rate limiting, CORS.

## Needs credentials / NOT verified against the real service
- **Groq / GPT-OSS 120B**: client code is written to Groq's OpenAI-compatible `/chat/completions` (tools, `reasoning_effort`, `max_completion_tokens`) but **has never been called live** (no key here). Tests use a mocked LLM. First live run may need small tweaks (e.g. if the model wraps JSON differently - the parser falls back to plain text).
- **Supabase Auth** (`/auth/register|login|logout`, service-role user deletion, JWKS): implemented as thin proxies; only unconfigured (503) and mocked paths are tested. JWT validation (HS256) is tested with locally-minted tokens.
- **Hosted Supabase Postgres**: tested on a local PostgreSQL 16, not on a Supabase instance.
- **OCR** for scanned PDFs: interface + generic HTTP provider included, untested; without config scanned PDFs return a clear error.
- **Docker image build / compose**: files provided, not built here (no Docker in the sandbox).

## Known limitations
- PDF parsing is a generic line-based parser; unusual bank layouts may need a bank-specific parser (register via `register_parser`). `.xls` (legacy) not supported.
- Rate limiter is in-memory (single process). Logout revokes the Supabase session but already-issued JWTs live until expiry.
- Imports and twin refresh run synchronously inside the request (fine for personal-scale data; move to a task queue for very large histories). Endpoints are sync `def` (run in FastAPI's threadpool) rather than `async`, because SQLAlchemy sessions are synchronous.
- Forecasts are statistical approximations with ~12 months of history at best; bands are approximate and not guarantees. ML categorizer only activates after ≥30 labelled transactions across ≥3 categories; Isolation Forest after ≥60 expenses.
- `transactions.category` stores the category name (the `categories` table is a catalog of flags, not an FK). Custom user categories are not implemented.
- No Supabase RLS policies are shipped; isolation is enforced in the API layer (see README).
- Currency is INR-centric; multi-currency conversion is not implemented.
