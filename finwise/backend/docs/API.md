# FinWise AI - API guide (v1)

Base path `/api/v1`. Auth: `Authorization: Bearer <Supabase JWT>`. Full machine-readable spec: `docs/openapi.json` / `/docs`.
Errors: `{"error": {"code": "validation_error", "message": "...", "details": [{"field": "amount", "message": "..."}]}}`.

| Group | Endpoints |
|---|---|
| System/Auth | `GET /health`, `POST /auth/register`, `/auth/login`, `/auth/logout` (Supabase proxies), `POST /auth/dev-token` (dev only) |
| Profile | `GET/PUT /users/me`, `GET/POST /accounts`, `PUT /accounts/{id}`, `GET /categories` |
| Transactions | `POST/GET /transactions`, `GET/PUT/DELETE /transactions/{id}` |
| Import | `POST /import/pdf|csv|excel|demo`, `GET /import/{job_id}` |
| Analytics | `GET /analytics/summary|monthly|categories|trends` |
| Budgets | `POST/GET /budgets`, `PUT/DELETE /budgets/{id}` |
| Recurring | `GET/POST /recurring`, `PUT/DELETE /recurring/{id}` |
| Predictions | `GET /predictions/expenses|income|balance` |
| Anomalies | `GET /anomalies`, `GET /anomalies/{id}`, `PUT /anomalies/{id}/status` |
| Digital twin | `GET /financial-twin`, `GET /financial-twin/history` |
| Simulations | `POST /simulations/purchase|scenario|opportunity-cost`, `GET /simulations`, `GET /simulations/{id}` |
| Stress tests | `POST/GET /stress-tests` |
| Goals | `POST/GET /goals`, `PUT/DELETE /goals/{id}` |
| AI coach | `POST /ai/chat`, `GET /ai/conversations`, `GET/DELETE /ai/conversations/{id}` |
| Notifications | `GET /notifications`, `PUT /notifications/{id}/read`, `DELETE /notifications/{id}` |
| Data | `GET /data/export`, `DELETE /data/account?confirm=DELETE` |

## Examples

### Add a transaction
`POST /transactions` (`?force=true` to bypass duplicate check, `?refresh=false` to skip digital-twin refresh)
```json
{"date": "2026-11-10", "description": "UPI/DR/123456789012/SWIGGY/okaxis/Payment", "amount": 350.50, "direction": "debit"}
```
```json
{"id": "…", "date": "2026-11-10", "merchant": "SWIGGY", "amount": "350.50", "direction": "debit", "category": "Food and dining",
 "category_confidence": 0.9, "category_source": "rule", "is_internal_transfer": false, "currency": "INR"}
```
Correct a category (learns the merchant for future imports): `PUT /transactions/{id}` `{"category": "Groceries"}`.
List: `GET /transactions?page=1&page_size=50&category=Groceries&min_amount=100&date_from=2026-10-01&sort_by=amount&order=desc&q=swiggy`.

### Import a statement
`POST /import/csv` (multipart, field `file`, optional `account_id`). Also `/import/excel` (.xlsx), `/import/pdf` (text PDFs; scanned PDFs need OCR config).
```json
{"job_id": "…", "status": "completed", "total_rows": 5, "imported": 4, "duplicates_skipped": 0, "invalid_rows": 1,
 "errors": [{"row": 6, "error": "unrecognised date 'badrow'"}], "warnings": []}
```

### "Can I afford a ₹35,000 laptop next month?"
`POST /simulations/purchase`
```json
{"item_name": "laptop", "amount": 35000, "days_from_now": 30, "horizon_days": 90, "payment": {"type": "emi", "months": 6, "annual_rate_pct": 12}}
```
Response (abridged): `verdict` (`affordable | affordable_with_caution | not_recommended`), `feasible`, `before`/`after` (`end_balance`, `min_balance`,
`monthly_run_rate_net`, `risk_level`…), `delta`, `emergency_runway_months_after`, `effect_on_goals[]`, `cash_flow_risks[]`,
`series.before/after[]` (`day,date,balance,lower,upper`), `assumptions[]`, `data_used`, `warnings[]`, `disclaimer`.
Other scenarios: `POST /simulations/scenario` `{"type": "loan|reduce_category_spending|salary_delay|salary_loss|unexpected_expense|increase_income|new_subscription|increase_monthly_savings", "params": {...}, "horizon_days": 90}`.

### Stress test
`POST /stress-tests` `{"type": "salary_delay", "params": {"days": 30}}` - types: `salary_delay, medical_expense, large_purchase, income_reduction, expense_increase, income_loss`.
Returns `emergency_cash_runway`, `can_cover_essential_expenses`, `potential_shortfall`, `risk_level`, `suggested_actions[]`; always marked `hypothetical`.

### Predictions
`GET /predictions/balance` -> `horizons {7d,30d,90d: {predicted_balance, lower, upper}}`, `end_of_month`, `potential_shortages`, `daily_series`, `confidence`, `warnings`.
With no transactions: `{"status": "insufficient_data", ...}` (no numbers invented). `GET /predictions/expenses` returns `method`, `confidence`, `history`, `forecast[{point,lower,upper}]`, `category_forecast`.

### Anomaly item
```json
{"id": "…", "transaction_id": "…", "type": "unusually_large_withdrawal", "amount": 25000.0, "date": "2026-11-09",
 "reason": "Cash withdrawal of ₹25,000 is 10.0x your typical withdrawal (₹2,500).", "severity": "high", "confidence": 0.8,
 "recommended_action": "Verify this withdrawal was made by you.", "status": "open", "is_confirmed_fraud": false}
```

### AI coach
`POST /ai/chat` `{"message": "Why did my expenses increase this month?", "conversation_id": null}`
```json
{"conversation_id": "…", "answer": "…", "structured": {"answer": "…", "key_figures": [{"label": "…", "value": "…"}], "assumptions": [], "uncertainty": "…", "suggested_follow_ups": []},
 "tools_used": ["get_financial_summary", "get_spending_by_category"], "disclaimer": "…"}
```
Tools exposed to the model (read-only, per-user, size-limited, max 8 calls/turn, 5 rounds): get_financial_summary, get_spending_by_category, get_transaction_history,
get_recurring_payments, get_upcoming_commitments, predict_future_balance, predict_monthly_expenses, get_financial_anomalies, simulate_purchase,
run_financial_stress_test, get_financial_goals, calculate_savings_feasibility, get_budget_status.
