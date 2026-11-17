from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Budget
from app.services.frames import load_df
from app.utils.money import r2


def budget_status(db: Session, user_id, year_month: str | None = None, as_of: date | None = None, history: int = 3) -> dict:
    as_of = as_of or date.today()
    ym = year_month or as_of.strftime("%Y-%m")
    period = pd.Period(ym, "M")
    df = load_df(db, user_id)
    exp = df[df.flow.isin(["expense", "refund"])].copy() if not df.empty else df
    if not exp.empty:
        exp["m"] = exp.date.dt.to_period("M")

    def spent(cat, p):
        if exp.empty:
            return 0.0
        d = exp[exp.m == p]
        if cat:
            d = d[d.category == cat]
        return float(d.signed_expense.sum())

    budgets = db.scalars(select(Budget).where(Budget.user_id == user_id, or_(Budget.year_month == ym, Budget.year_month.is_(None)))).all()
    # a month-specific budget overrides a generic one for the same category
    chosen: dict = {}
    for b in sorted(budgets, key=lambda x: x.year_month is None, reverse=True):
        chosen.setdefault(b.category, b)
    items = []
    for b in chosen.values():
        limit = float(b.limit_amount)
        s = spent(b.category, period)
        util = s / limit * 100 if limit else 0.0
        status = "exceeded" if util >= 100 else "warning" if util >= b.alert_threshold * 100 else "ok"
        hist = [{"month": str(period - i), "spent": r2(spent(b.category, period - i)), "limit": r2(limit),
                 "utilization_pct": round(spent(b.category, period - i) / limit * 100, 1) if limit else None} for i in range(1, history + 1)]
        items.append({"budget_id": str(b.id), "category": b.category or "Overall", "limit": r2(limit), "spent": r2(s), "remaining": r2(limit - s),
                      "utilization_pct": round(util, 1), "status": status, "alert_threshold_pct": round(b.alert_threshold * 100),
                      "history": hist, "warning": (f"Budget {'exceeded' if status == 'exceeded' else 'nearly used'}: {util:.0f}% of the limit used." if status != "ok" else None)})
    return {"month": ym, "budgets": sorted(items, key=lambda x: -x["utilization_pct"])}
