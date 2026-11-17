"""Load transactions into pandas frames with flow classification used by every analytic module."""
from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transaction
from app.services.categorization import INCOME_CATS

COLUMNS = ["id", "date", "amount", "direction", "category", "merchant", "description", "is_internal", "account_id", "flow", "signed_expense", "confidence"]


def flow_of(direction: str, category: str, internal: bool) -> str:
    if internal or category == "Transfers":
        return "transfer"
    if category == "Investments":
        return "investment"
    if direction == "credit":
        return "income" if category in INCOME_CATS else "refund"
    return "expense"


def load_df(db: Session, user_id, start: date | None = None, end: date | None = None) -> pd.DataFrame:
    q = select(Transaction).where(Transaction.user_id == user_id)
    if start:
        q = q.where(Transaction.date >= start)
    if end:
        q = q.where(Transaction.date <= end)
    rows = []
    for t in db.scalars(q.order_by(Transaction.date)):
        flow = flow_of(t.direction, t.category, t.is_internal_transfer)
        amt = float(t.amount)
        rows.append((str(t.id), pd.Timestamp(t.date), amt, t.direction, t.category, t.merchant or "", t.description,
                     t.is_internal_transfer, str(t.account_id) if t.account_id else None, flow,
                     amt if flow == "expense" else (-amt if flow == "refund" else 0.0), t.category_confidence))
    return pd.DataFrame(rows, columns=COLUMNS)


def complete_months(df: pd.DataFrame, as_of: date) -> list[pd.Period]:
    """Months fully elapsed (strictly before as_of's month) and having data coverage from the first txn month."""
    if df.empty:
        return []
    first = df["date"].min().to_period("M")
    last_complete = pd.Period(as_of, "M") - 1
    if last_complete < first:
        return []
    # first month counts only if data starts on day <= 5 (otherwise it's partial)
    months = list(pd.period_range(first, last_complete, freq="M"))
    if months and df["date"].min().day > 5:
        months = months[1:]
    return months


def monthly_table(df: pd.DataFrame, months: list[pd.Period]) -> pd.DataFrame:
    idx = pd.PeriodIndex(months, freq="M")
    if df.empty:
        return pd.DataFrame(0.0, index=idx, columns=["income", "expenses", "invested"])
    d = df.copy()
    d["m"] = d["date"].dt.to_period("M")
    inc = d[d.flow == "income"].groupby("m")["amount"].sum()
    exp = d.groupby("m")["signed_expense"].sum()
    inv = d[(d.flow == "investment") & (d.direction == "debit")].groupby("m")["amount"].sum()
    out = pd.DataFrame({"income": inc, "expenses": exp, "invested": inv}).reindex(idx).fillna(0.0)
    return out
