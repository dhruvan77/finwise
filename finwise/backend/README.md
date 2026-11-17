# FinWise AI - Backend

> **Don't just track your money. Understand it. Predict it. Make smarter decisions with it.**

AI-powered personal financial decision intelligence. FastAPI modular monolith with a **Financial Digital Twin**,
forecasting, anomaly detection, what-if simulation, stress tests, goals, budgets, notifications and an AI coach
(GPT-OSS 120B via Groq with tool calling). **Backend only** - connect any Next.js/React frontend.

> ⚠️ FinWise AI provides informational decision support, not guaranteed financial outcomes or regulated professional
> financial advice. Predictions, simulations and stress tests are hypothetical and based on the user's recorded history.

**Golden rule:** the LLM never invents or calculates money figures. All numbers come from deterministic Python
(`app/services/*`); the LLM only chooses tools and explains their results.

## 1. Prerequisites
Python 3.11+ (developed on 3.12), PostgreSQL (Supabase project recommended) - or SQLite for a zero-setup local trial.
Optional: a Groq API key (AI coach), Supabase project (auth), Docker.

## 2. Install
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # then edit
```

## 3. Environment variables (`.env.example` lists all)
| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres URI (`postgresql://...` is auto-rewritten to the psycopg2 driver) or `sqlite:///./finwise_dev.db` |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | Needed for `/auth/register`, `/auth/login`, `/auth/logout` proxies |
| `SUPABASE_JWT_SECRET` | Validates Supabase access tokens (HS256). For asymmetric-key projects set `SUPABASE_JWKS_URL` instead |
| `SUPABASE_SERVICE_ROLE_KEY` | Optional: lets `DELETE /data/account` also delete the Supabase auth identity |
| `GROQ_API_KEY`, `GROQ_MODEL` (default `openai/gpt-oss-120b`), `GROQ_REASONING_EFFORT` | AI coach. Model is configurable - never hard-coded |
| `ALLOW_DEV_AUTH` | `true` enables `/auth/dev-token` for local dev **only** (disabled/404 in production) |
| `CORS_ORIGINS` | Comma-separated frontend origins |
| `MAX_UPLOAD_MB`, `OCR_PROVIDER`, `OCR_API_URL`, `OCR_API_KEY` | Upload limit; optional OCR for scanned PDFs |

## 4. Connect Supabase PostgreSQL
1. Supabase dashboard -> *Project Settings -> Database -> Connection string (URI)*; put it in `DATABASE_URL`.
2. *Settings -> API*: copy URL, anon key and **JWT secret** into `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET`.
3. `alembic upgrade head` creates all tables. The `users.id` equals the Supabase `auth.users.id` (JWT `sub`); a profile row is created
   automatically on a user's first authenticated request.
4. Authorization is enforced in the API layer (every query is filtered by the authenticated `user_id`). If you also expose these tables via
   Supabase's PostgREST, enable Row Level Security on them - the backend connects as a privileged DB role.

## 5. Configure Groq / GPT-OSS 120B
Set `GROQ_API_KEY`. Default model `openai/gpt-oss-120b`; change via `GROQ_MODEL`. `GROQ_REASONING_EFFORT` (low/medium/high) is passed to the API.
The key stays server-side. Without a key `/ai/chat` returns `503 ai_not_configured`; everything else works.

## 6-8. Migrate, seed, run
```bash
alembic upgrade head
python -m scripts.seed          # synthetic Indian demo user (fictional data), prints a dev token when ALLOW_DEV_AUTH=true
uvicorn app.main:app --reload   # http://localhost:8000
```
Or `docker compose up --build` (API + local Postgres). Any logged-in user can also call `POST /api/v1/import/demo` to load the synthetic dataset.

## 9. API docs
Swagger UI `http://localhost:8000/docs`, ReDoc `/redoc`, schema `/openapi.json` (snapshot in `docs/openapi.json`).
Examples: `docs/API.md`. Database: `docs/DATABASE_SCHEMA.md`.

## 10. Tests
```bash
pytest -q                                              # SQLite, no network, no real Groq key needed (LLM mocked)
TEST_DATABASE_URL=postgresql://user:pw@host/db pytest  # same suite on PostgreSQL
```

## 11. Connecting a frontend
Send `Authorization: Bearer <Supabase access token>`; add the origin to `CORS_ORIGINS`. Success responses are plain JSON resources (lists:
`{items,total,page,page_size,pages}`); errors are always `{"error":{"code","message","details"}}`. Chart-ready arrays are returned by
`/analytics/*`, `/predictions/*` (`daily_series` with `lower/upper` bands, flagged `series_kind: "predicted"`) and simulations (`series.before/after`).
Everything is versioned under `/api/v1`.

## 12. What is implemented vs. needs credentials
See `IMPLEMENTATION_REPORT.md` for the verified status of each feature, test results and known limitations.

## Architecture
```
app/api/v1/        thin routers (validation, auth, status codes)
app/core/          config, db, JWT security, errors, rate limit, redacting logs
app/models/        SQLAlchemy ORM (Numeric(14,2) for money)
app/schemas/       Pydantic request models
app/services/      all business logic: parsers, categorization, frames, analytics, recurring, forecasting,
                   anomalies, twin, simulation(+stress, opportunity cost), goals, budgets, notifications, ai_coach, demo_data
alembic/           migrations     scripts/ seed + docs generators     tests/ pytest suite
```
Key modelling choices (all documented in responses' `assumptions`): transfers between own accounts and investments are not expenses;
refunds net against their category; large non-recurring one-offs are excluded from the spending run-rate; forecasts use only complete months,
state their method/confidence and refuse to forecast with no history; "free cash flow" = recurring income - recurring commitments - typical variable spend.

## Security notes
JWT validation (Supabase), per-user data isolation on every query, input/file validation (size, extension, magic bytes, in-memory parsing - uploads are never written to disk),
in-memory rate limiting (single instance; swap for Redis when scaling), CORS allow-list, redacted logs (no bodies/amounts), no secrets in code,
data export + full account deletion. Never send UPI PINs, OTPs, passwords or card CVVs - the API has no field for them.
