"""Deterministic financial analytics. All numbers computed here, never by the LLM."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RecurringPayment
from app.services.categorization import CATEGORY_META, ESSENTIAL, FIXED
from app.services.frames import complete_months, load_df, monthly_table
from app.utils.money import r2


def _recurring_keys(db: Session, user_id) -> set[str]:
    return {r.merchant_key for r in db.scalars(select(RecurringPayment).where(
        RecurringPayment.user_id == user_id, RecurringPayment.status != "dismissed", RecurringPayment.direction == "debit"))}


def _pct(a: float, b: float):
    return round((a - b) / b * 100, 1) if b else None


def _cat_table(d: pd.DataFrame) -> list[dict]:
    e = d[d.flow.isin(["expense", "refund"])]
    if e.empty:
        return []
    g = e.groupby("category").agg(amount=("signed_expense", "sum"), count=("id", "count")).reset_index()
    g = g[g.amount > 0].sort_values("amount", ascending=False)
    total = g.amount.sum() or 1
    return [{"category": r.category, "amount": r2(r.amount), "pct_of_expenses": round(r.amount / total * 100, 1), "transactions": int(r["count"]),
             "essential": r.category in ESSENTIAL, "fixed": r.category in FIXED} for _, r in g.iterrows()]


def summary(db: Session, user_id, start: date | None = None, end: date | None = None, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    end = end or as_of
    start = start or (end - timedelta(days=29))
    all_df = load_df(db, user_id)
    df = all_df[(all_df.date >= pd.Timestamp(start)) & (all_df.date <= pd.Timestamp(end))] if not all_df.empty else all_df
    out = {"period": {"start": start.isoformat(), "end": end.isoformat()}, "currency": "INR", "data_available": not df.empty}
    if df.empty:
        out.update(total_income=0.0, total_expenses=0.0, net_savings=0.0, savings_rate=None, invested=0.0,
                   average_daily_spending=0.0, average_monthly_spending=None, category_breakdown=[], largest_expenses=[],
                   fixed_vs_variable={"fixed": 0.0, "variable": 0.0}, essential_vs_discretionary={"essential": 0.0, "discretionary": 0.0})
        return out
    eff_start = max(pd.Timestamp(start), all_df.date.min())
    days = max(1, (pd.Timestamp(end) - eff_start).days + 1)
    income = float(df[df.flow == "income"].amount.sum())
    expenses = float(df.signed_expense.sum())
    invested = float(df[(df.flow == "investment") & (df.direction == "debit")].amount.sum())
    net = income - expenses
    rkeys = _recurring_keys(db, user_id)
    e = df[df.flow.isin(["expense", "refund"])]      # refunds net against their category
    fixed_mask = e.category.isin(FIXED) | e.merchant.isin(rkeys)
    ess_mask = e.category.isin(ESSENTIAL)
    months = complete_months(all_df, as_of)
    mt = monthly_table(all_df, months)
    avg_monthly = float(mt.expenses.mean()) if len(mt) else None
    cur_m = pd.Period(end, "M")
    prev_m = cur_m - 1
    mm = monthly_table(all_df, [prev_m, cur_m])
    largest = e[e.flow == "expense"].sort_values("amount", ascending=False).head(5)
    out.update(
        total_income=r2(income), total_expenses=r2(expenses), net_savings=r2(net),
        savings_rate=round(net / income * 100, 1) if income > 0 else None, invested=r2(invested),
        average_daily_spending=r2(expenses / days), average_monthly_spending=r2(avg_monthly),
        average_monthly_spending_basis=f"{len(mt)} complete month(s)" if len(mt) else "insufficient complete months",
        category_breakdown=_cat_table(df),
        largest_expenses=[{"transaction_id": r.id, "date": r.date.date().isoformat(), "description": r.description[:80], "category": r.category, "amount": r2(r.amount)}
                          for _, r in largest.iterrows()],
        fixed_vs_variable={"fixed": r2(e[fixed_mask].signed_expense.sum()), "variable": r2(e[~fixed_mask].signed_expense.sum())},
        essential_vs_discretionary={"essential": r2(e[ess_mask].signed_expense.sum()), "discretionary": r2(e[~ess_mask].signed_expense.sum())},
        cash_flow={"inflow": r2(float(df[(df.direction == "credit") & (~df.is_internal)].amount.sum())),
                   "outflow": r2(float(df[(df.direction == "debit") & (~df.is_internal)].amount.sum())),
                   "net": r2(float(df[(df.direction == "credit") & (~df.is_internal)].amount.sum() - df[(df.direction == "debit") & (~df.is_internal)].amount.sum()))},
        month_over_month={"current_month": str(cur_m), "previous_month": str(prev_m),
                          "expenses": {"current": r2(mm.loc[cur_m, "expenses"]), "previous": r2(mm.loc[prev_m, "expenses"]),
                                       "change_pct": _pct(mm.loc[cur_m, "expenses"], mm.loc[prev_m, "expenses"])},
                          "income": {"current": r2(mm.loc[cur_m, "income"]), "previous": r2(mm.loc[prev_m, "income"]),
                                     "change_pct": _pct(mm.loc[cur_m, "income"], mm.loc[prev_m, "income"])},
                          "note": "Current month may be partial."},
    )
    return out


def monthly(db: Session, user_id, months: int = 12, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    df = load_df(db, user_id)
    if df.empty:
        return {"months": [], "data_available": False}
    first = df.date.min().to_period("M")
    cur = pd.Period(as_of, "M")
    plist = list(pd.period_range(max(first, cur - (months - 1)), cur, freq="M"))
    mt = monthly_table(df, plist)
    out = []
    prev = None
    for p in plist:
        row = mt.loc[p]
        dm = df[df.date.dt.to_period("M") == p]
        cats = {c: r2(v) for c, v in dm[dm.flow.isin(["expense", "refund"])].groupby("category").signed_expense.sum().items() if v > 0}
        net = row.income - row.expenses
        rec = {"month": str(p), "income": r2(row.income), "expenses": r2(row.expenses), "net_savings": r2(net),
               "savings_rate": round(net / row.income * 100, 1) if row.income > 0 else None, "invested": r2(row.invested),
               "by_category": cats, "is_partial": p == cur,
               "expense_change_pct_vs_prev": _pct(row.expenses, prev.expenses) if prev is not None else None,
               "income_change_pct_vs_prev": _pct(row.income, prev.income) if prev is not None else None}
        out.append(rec)
        prev = row
    return {"months": out, "currency": "INR", "data_available": True}


def categories(db: Session, user_id, start: date | None = None, end: date | None = None, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    end = end or as_of
    start = start or (end - timedelta(days=29))
    df = load_df(db, user_id, start, end)
    cats = _cat_table(df)
    ess = sum(c["amount"] for c in cats if c["essential"])
    tot = sum(c["amount"] for c in cats)
    return {"period": {"start": start.isoformat(), "end": end.isoformat()}, "categories": cats, "total_expenses": r2(tot),
            "essential": r2(ess), "discretionary": r2(tot - ess)}


def trends(db: Session, user_id, months: int = 6, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    df = load_df(db, user_id)
    plist = complete_months(df, as_of)[-months:]
    if len(plist) < 2:
        return {"data_available": False, "message": "At least 2 complete months of data are needed for trends.", "months_available": len(plist)}
    mt = monthly_table(df, plist)
    mt["net"] = mt.income - mt.expenses

    def slope(s):
        return float(np.polyfit(range(len(s)), s.values, 1)[0]) if len(s) >= 3 else float(s.iloc[-1] - s.iloc[0]) / max(1, len(s) - 1)

    def direction(sl, base):
        if base == 0:
            return "flat"
        rel = sl / abs(base)
        return "rising" if rel > 0.03 else "falling" if rel < -0.03 else "flat"

    labels = [str(p) for p in plist]
    cat_df = df[df.flow.isin(["expense", "refund"])].copy()
    cat_df["m"] = cat_df.date.dt.to_period("M")
    pivot = cat_df[cat_df.m.isin(plist)].pivot_table(index="m", columns="category", values="signed_expense", aggfunc="sum").reindex(plist).fillna(0.0)
    top = pivot.sum().sort_values(ascending=False).head(5).index
    cat_tr = [{"category": c, "values": [r2(v) for v in pivot[c]], "direction": direction(slope(pivot[c]), pivot[c].mean())} for c in top]
    return {"data_available": True, "months": labels, "currency": "INR",
            "income": {"values": [r2(v) for v in mt.income], "moving_avg_3": [r2(v) for v in mt.income.rolling(3, min_periods=1).mean()],
                       "direction": direction(slope(mt.income), mt.income.mean())},
            "expenses": {"values": [r2(v) for v in mt.expenses], "moving_avg_3": [r2(v) for v in mt.expenses.rolling(3, min_periods=1).mean()],
                         "direction": direction(slope(mt.expenses), mt.expenses.mean())},
            "net_savings": {"values": [r2(v) for v in mt.net], "direction": direction(slope(mt.net), mt.net.mean())},
            "category_trends": cat_tr}
