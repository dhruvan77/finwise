# FinWise AI - Database Schema

Generated from the SQLAlchemy models (`python -m scripts.generate_docs`). Source of truth for DDL: `alembic/versions/`.

Conventions: UUID primary keys; every user-owned table has `user_id` -> `users.id` (ON DELETE CASCADE) and is always filtered by it;
money columns are `NUMERIC(14,2)` (never float); timestamps are timezone-aware.

## `accounts`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| name | VARCHAR(120) | no |  |
| type | VARCHAR(30) | no |  |
| institution | VARCHAR(120) | yes |  |
| currency | VARCHAR(3) | no |  |
| current_balance | NUMERIC(14, 2) | yes |  |
| balance_as_of | DATE | yes |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- Index `ix_accounts_user_id`: (user_id)

## `ai_conversations`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| title | VARCHAR(200) | no |  |
| updated_at | DATETIME | no |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- Index `ix_ai_conversations_user_id`: (user_id)

## `ai_messages`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| conversation_id | CHAR(32) | no | FK -> ai_conversations.id, indexed |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| role | VARCHAR(12) | no |  |
| content | TEXT | no |  |
| structured | JSON | yes |  |
| tool_calls | JSON | yes |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- Index `ix_ai_messages_user_id`: (user_id)
- Index `ix_ai_messages_conversation_id`: (conversation_id)

## `anomalies`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| transaction_id | CHAR(32) | yes | FK -> transactions.id, indexed |
| dedupe_key | VARCHAR(160) | no |  |
| type | VARCHAR(40) | no |  |
| amount | NUMERIC(14, 2) | yes |  |
| date | DATE | yes |  |
| reason | TEXT | no |  |
| severity | VARCHAR(10) | no |  |
| confidence | FLOAT | yes |  |
| recommended_action | TEXT | no |  |
| details | JSON | no |  |
| status | VARCHAR(12) | no |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- UniqueConstraint: (user_id, dedupe_key)
- Index `ix_anomalies_user_id`: (user_id)
- Index `ix_anomalies_transaction_id`: (transaction_id)

## `budgets`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| category | VARCHAR(60) | yes |  |
| limit_amount | NUMERIC(14, 2) | no |  |
| year_month | VARCHAR(7) | yes |  |
| alert_threshold | FLOAT | no |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- Index `ix_budgets_user_id`: (user_id)

## `categories`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | INTEGER | no | PK |
| name | VARCHAR(60) | no | unique |
| kind | VARCHAR(20) | no |  |
| is_essential | BOOLEAN | no |  |
| is_fixed | BOOLEAN | no |  |

Constraints / indexes:
- UniqueConstraint: (name)

## `category_rules`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| merchant_key | VARCHAR(120) | no |  |
| category | VARCHAR(60) | no |  |
| hits | INTEGER | no |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- UniqueConstraint: (user_id, merchant_key)
- Index `ix_category_rules_user_id`: (user_id)

## `financial_profiles`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| user_id | CHAR(32) | no | PK, FK -> users.id |
| opening_balance | NUMERIC(14, 2) | no |  |
| state | JSON | no |  |
| updated_at | DATETIME | yes |  |

## `financial_snapshots`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| snapshot_date | DATE | no |  |
| state | JSON | no |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- UniqueConstraint: (user_id, snapshot_date)
- Index `ix_financial_snapshots_user_id`: (user_id)

## `goals`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| name | VARCHAR(160) | no |  |
| target_amount | NUMERIC(14, 2) | no |  |
| current_amount | NUMERIC(14, 2) | no |  |
| target_date | DATE | yes |  |
| monthly_contribution | NUMERIC(14, 2) | no |  |
| status | VARCHAR(12) | no |  |
| last_notified_pct | INTEGER | no |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- Index `ix_goals_user_id`: (user_id)

## `import_jobs`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| source_type | VARCHAR(20) | no |  |
| filename | VARCHAR(255) | yes |  |
| status | VARCHAR(20) | no |  |
| total_rows | INTEGER | no |  |
| imported | INTEGER | no |  |
| duplicates | INTEGER | no |  |
| invalid | INTEGER | no |  |
| errors | JSON | no |  |
| warnings | JSON | no |  |
| completed_at | DATETIME | yes |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- Index `ix_import_jobs_user_id`: (user_id)

## `notifications`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| type | VARCHAR(40) | no |  |
| title | VARCHAR(200) | no |  |
| message | TEXT | no |  |
| severity | VARCHAR(10) | no |  |
| dedupe_key | VARCHAR(160) | no |  |
| data | JSON | no |  |
| is_read | BOOLEAN | no |  |
| is_dismissed | BOOLEAN | no |  |
| delivered_channels | JSON | no |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- UniqueConstraint: (user_id, dedupe_key)
- Index `ix_notifications_user_id`: (user_id)

## `predictions`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| kind | VARCHAR(20) | no |  |
| horizon_days | INTEGER | no |  |
| method | VARCHAR(60) | no |  |
| result | JSON | no |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- Index `ix_predictions_user_id`: (user_id)

## `recurring_payments`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| name | VARCHAR(120) | no |  |
| merchant_key | VARCHAR(120) | no |  |
| category | VARCHAR(60) | no |  |
| kind | VARCHAR(20) | no |  |
| direction | VARCHAR(6) | no |  |
| frequency | VARCHAR(12) | no |  |
| avg_amount | NUMERIC(14, 2) | no |  |
| last_amount | NUMERIC(14, 2) | yes |  |
| last_date | DATE | yes |  |
| next_date | DATE | yes |  |
| occurrences | INTEGER | no |  |
| amount_change_pct | FLOAT | yes |  |
| confidence | FLOAT | no |  |
| status | VARCHAR(12) | no |  |
| is_user_defined | BOOLEAN | no |  |
| last_transaction_id | CHAR(32) | yes |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- UniqueConstraint: (user_id, merchant_key, direction)
- Index `ix_recurring_payments_user_id`: (user_id)

## `simulations`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| kind | VARCHAR(40) | no |  |
| horizon_days | INTEGER | no |  |
| params | JSON | no |  |
| result | JSON | no |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- Index `ix_simulations_user_id`: (user_id)

## `stress_tests`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| kind | VARCHAR(40) | no |  |
| horizon_days | INTEGER | no |  |
| params | JSON | no |  |
| result | JSON | no |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- Index `ix_stress_tests_user_id`: (user_id)

## `transactions`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| user_id | CHAR(32) | no | FK -> users.id, indexed |
| account_id | CHAR(32) | yes | FK -> accounts.id, indexed |
| import_job_id | CHAR(32) | yes | FK -> import_jobs.id |
| date | DATE | no |  |
| description | VARCHAR(500) | no |  |
| merchant | VARCHAR(120) | yes |  |
| amount | NUMERIC(14, 2) | no |  |
| direction | VARCHAR(6) | no |  |
| currency | VARCHAR(3) | no |  |
| category | VARCHAR(60) | no |  |
| category_confidence | FLOAT | no |  |
| category_source | VARCHAR(20) | no |  |
| is_internal_transfer | BOOLEAN | no |  |
| balance_after | NUMERIC(14, 2) | yes |  |
| notes | TEXT | yes |  |
| fingerprint | VARCHAR(64) | yes |  |
| source | VARCHAR(20) | no |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- Index `ix_transactions_account_id`: (account_id)
- Index `ix_txn_user_category`: (user_id, category)
- Index `ix_transactions_user_id`: (user_id)
- Index `ix_txn_user_fingerprint`: (user_id, fingerprint)
- Index `ix_txn_user_date`: (user_id, date)

## `users`

| Column | Type | Null | Key / Default |
|---|---|---|---|
| id | CHAR(32) | no | PK |
| email | VARCHAR(320) | no | unique, indexed |
| full_name | VARCHAR(200) | yes |  |
| currency | VARCHAR(3) | no |  |
| preferences | JSON | no |  |
| settings | JSON | no |  |
| updated_at | DATETIME | no |  |
| created_at | DATETIME | no |  |

Constraints / indexes:
- Index `ix_users_email`: (email)
