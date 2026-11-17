"""Forecasting: statistical monthly models + daily cash-flow projection with scenario hooks.
Method choice depends on history length; insufficient data is reported, never papered over."""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, FinancialProfile, Transaction
from app.services.categorization import ESSENTIAL
from app.services.frames import complete_months, load_df, monthly_table
from app.services.recurring import active_recurring, occurrence_dates
from app.utils.money import r2

Z80 = 1.2816  # 80% two-sided interval
MAX_DAYS = 400


# ---------------------------------------------------------------- balance
def get_current_balance(db: Session, user_id) -> tuple[float, str]:
    accounts = list(db.scalars(select(Account).where(Account.user_id == user_id, Account.type != "credit_card")))
    known = [a for a in accounts if a.current_balance is not None]
    if known:
        total = 0.0
        for a in known:
            bal = float(a.current_balance)
            if a.balance_as_of:
                # txns after the balance date, plus manual entries dated on it (imported rows on that date are already in the balance)
                for t in db.scalars(select(Transaction).where(Transaction.user_id == user_id, Transaction.account_id == a.id, Transaction.date >= a.balance_as_of)):
                    if t.date == a.balance_as_of and t.source != "manual":
                        continue
                    bal += float(t.amount) if t.direction == "credit" else -float(t.amount)
            total += bal
        return total, "account_balances" if len(known) == len(accounts) else "account_balances_partial"
    prof = db.get(FinancialProfile, user_id)
    opening = float(prof.opening_balance) if prof else 0.0
    net = 0.0
    cc_ids = {a.id for a in db.scalars(select(Account).where(Account.user_id == user_id, Account.type == "credit_card"))}
    for t in db.scalars(select(Transaction).where(Transaction.user_id == user_id)):
        if t.account_id in cc_ids:
            continue
        net += float(t.amount) if t.direction == "credit" else -float(t.amount)
    return opening + net, "derived: opening_balance + net of recorded transactions (set account balances for accuracy)"


# ---------------------------------------------------------------- monthly series forecasting
def forecast_series(series: pd.Series, horizon: int = 1) -> dict:
    s = series.astype(float)
    n = len(s)
    hist = [{"month": str(i), "value": r2(v)} for i, v in s.items()]
    if n == 0:
        return {"status": "insufficient_data", "method": None, "confidence": "none", "history_months": 0, "history": hist,
                "message": "No complete months of history yet; cannot forecast."}
    mean, std = float(s.mean()), float(s.std(ddof=1)) if n > 1 else 0.0
    cv = std / mean if mean else 0.0
    if n <= 2:
        method, pts = "historical_average", [mean] * horizon
        spread = max(std, 0.2 * mean)
        conf, status = "low", "limited_data"
    elif n < 6:
        w = np.array([0.2, 0.3, 0.5])[-min(3, n):]
        w = w / w.sum()
        base = float(np.dot(s.values[-len(w):], w))
        method, pts = "weighted_moving_average_3m", [base] * horizon
        spread = max(std, 0.1 * mean)
        conf, status = "low", "limited_data"
    else:
        method, pts, spread = "holt_damped_exponential_smoothing", None, std
        try:
            from statsmodels.tsa.holtwinters import ExponentialSmoothing
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fit = ExponentialSmoothing(s.values, trend="add", damped_trend=True, initialization_method="estimated").fit()
                pts = [float(x) for x in fit.forecast(horizon)]
                resid = np.asarray(fit.resid)
                spread = float(np.std(resid, ddof=1)) if len(resid) > 2 else std
            if not all(math.isfinite(p) for p in pts):
                raise ValueError
        except Exception:
            method, pts = "weighted_moving_average_3m", [float(np.dot(s.values[-3:], [0.2, 0.3, 0.5]))] * horizon
        # guard against runaway trends: stay within a sane band around recent history
        lo_b, hi_b = float(s.min()) * 0.5, float(s.max()) * 1.5
        pts = [min(max(p, lo_b), hi_b) for p in pts]
        conf = "high" if (n >= 12 and cv < 0.25) else "medium"
        status = "ok"
    out = [{"step": i + 1, "point": r2(max(p, 0)), "lower": r2(max(p - Z80 * spread * math.sqrt(i + 1), 0)),
            "upper": r2(p + Z80 * spread * math.sqrt(i + 1))} for i, p in enumerate(pts)]
    return {"status": status, "method": method, "confidence": conf, "interval": "80% (approx.)", "history_months": n,
            "history": hist, "forecast": out, "data_note": "Based on complete calendar months only; current month excluded."}


def category_forecast(db: Session, user_id, as_of: date) -> list[dict]:
    df = load_df(db, user_id)
    months = complete_months(df, as_of)
    if len(months) < 1:
        return []
    e = df[df.flow.isin(["expense", "refund"])].copy()
    e["m"] = e.date.dt.to_period("M")
    pv = e.pivot_table(index="m", columns="category", values="signed_expense", aggfunc="sum").reindex(months).fillna(0.0)
    out = []
    for c in pv.columns:
        col = pv[c]
        if col.sum() <= 0:
            continue
        w = np.array([0.2, 0.3, 0.5])[-min(3, len(col)):]
        pt = float(np.dot(col.values[-len(w):], w / w.sum()))
        sd = float(col.std(ddof=1)) if len(col) > 1 else 0.0
        out.append({"category": c, "predicted_next_month": r2(pt), "lower": r2(max(pt - Z80 * sd, 0)), "upper": r2(pt + Z80 * sd),
                    "months_used": len(col), "confidence": "low" if len(col) < 3 else "medium"})
    return sorted(out, key=lambda x: -x["predicted_next_month"])


# ---------------------------------------------------------------- daily projection
@dataclass
class Scenario:
    one_offs: list = field(default_factory=list)            # (day_offset>=1, signed_amount, label)
    monthly_cash_delta: float = 0.0                        # +income / -expense per month
    income_delay_days: int = 0
    income_suspend_days: int = 0
    income_scale: float = 1.0
    income_scale_days: int | None = None                   # None = whole horizon
    recurring_expense_scale: float = 1.0
    variable_spend_scale: float = 1.0
    extra_recurring: list = field(default_factory=list)     # (start_offset, interval_days, signed_amount, count|None, label)
    savings_transfer_monthly: float = 0.0


@dataclass
class ForecastContext:
    as_of: date
    balance: float
    balance_source: str
    events: list            # (offset, signed_amount, direction, name, kind)
    has_recurring_income: bool
    var_exp_daily: float
    var_exp_std: float
    var_inc_daily: float
    var_inc_std: float
    avg_monthly_income: float | None
    avg_monthly_expenses: float | None
    essential_monthly: float | None
    months_of_data: int
    history_days: int
    n_transactions: int
    warnings: list
    excluded_one_offs: dict

    @property
    def data_status(self) -> str:
        if self.n_transactions == 0:
            return "none"
        if self.months_of_data >= 6:
            return "good"
        if self.months_of_data >= 2:
            return "adequate"
        return "limited"


def build_context(db: Session, user_id, as_of: date | None = None) -> ForecastContext:
    as_of = as_of or date.today()
    df = load_df(db, user_id, end=as_of)
    balance, bsrc = get_current_balance(db, user_id)
    warns: list[str] = []
    recs = active_recurring(db, user_id)
    events = []
    end_h = as_of + timedelta(days=MAX_DAYS)
    for rp in recs:
        for d in occurrence_dates(rp, as_of + timedelta(days=1), end_h):
            events.append(((d - as_of).days, float(rp.avg_amount) * (1 if rp.direction == "credit" else -1), rp.direction, rp.name, rp.kind))
    events.sort()
    has_inc = any(e[2] == "credit" and e[4] in ("salary", "other", "transfer") for e in events)
    if df.empty:
        return ForecastContext(as_of, balance, bsrc, events, has_inc, 0, 0, 0, 0, None, None, None, 0, 0, 0,
                               ["No transactions available."], {})
    first, last = df.date.min().date(), df.date.max().date()
    if (as_of - last).days > 10:
        warns.append(f"Latest transaction is {(as_of - last).days} days old; projections may miss recent activity.")
    win_end = last
    win_days = max(1, min(90, (win_end - first).days + 1))
    win_start = pd.Timestamp(win_end - timedelta(days=win_days - 1))
    w = df[(df.date >= win_start) & (df.date <= pd.Timestamp(win_end))]
    rec_keys = {(r.merchant_key, r.direction) for r in recs}
    nonrec = w[~w.apply(lambda r: (r.merchant, r.direction) in rec_keys, axis=1)] if not w.empty else w
    exp = nonrec[nonrec.flow.isin(["expense", "refund"])]
    excluded = {"count": 0, "total": 0.0}
    if len(exp[exp.flow == "expense"]) >= 8:
        a = exp[exp.flow == "expense"].amount
        q1, q3, med = a.quantile(0.25), a.quantile(0.75), a.median()
        thr = max(q3 + 3 * (q3 - q1), 10000.0, 3 * med)
        big = exp[(exp.flow == "expense") & (exp.amount > thr)]
        excluded = {"count": int(len(big)), "total": r2(big.amount.sum()), "threshold": r2(thr)}
        exp = exp.drop(big.index)
    days_idx = pd.date_range(win_start, pd.Timestamp(win_end), freq="D")
    de = exp.groupby("date").signed_expense.sum().reindex(days_idx, fill_value=0.0)
    inc = nonrec[nonrec.flow == "income"].groupby("date").amount.sum().reindex(days_idx, fill_value=0.0)
    months = complete_months(df, as_of)
    mt = monthly_table(df, months) if months else None
    if mt is not None and len(mt):
        avg_inc, avg_exp = float(mt.income.mean()), float(mt.expenses.mean())
        last3 = months[-3:]
        e = df[df.flow.isin(["expense", "refund"]) & df.category.isin(ESSENTIAL)].copy()
        e["m"] = e.date.dt.to_period("M")
        ess = float(e[e.m.isin(last3)].groupby("m").signed_expense.sum().reindex(last3, fill_value=0.0).mean())
    else:
        span = max(1, (last - first).days + 1)
        avg_inc = float(df[df.flow == "income"].amount.sum()) / span * 30.4375
        avg_exp = float(df.signed_expense.sum()) / span * 30.4375
        ess = float(df[df.category.isin(ESSENTIAL)].signed_expense.sum()) / span * 30.4375
        warns.append("Fewer than one complete month of data; monthly figures are prorated estimates.")
    if len(months) < 3:
        warns.append(f"Only {len(months)} complete month(s) of history; forecasts have low confidence.")
    return ForecastContext(as_of, balance, bsrc, events, has_inc, float(de.mean()), float(de.std(ddof=0)),
                           float(inc.mean()), float(inc.std(ddof=0)), avg_inc, avg_exp, ess, len(months),
                           (last - first).days + 1, len(df), warns, excluded)


def run_rate_monthly(ctx: ForecastContext, sc: Scenario | None = None) -> float:
    """Steady-state expected monthly net cash flow (excludes one-offs and temporary delay/suspension)."""
    sc = sc or Scenario()
    tot = 0.0
    for off, amt, direction, *_ in ctx.events:
        if off > 365:
            break
        tot += amt * (sc.income_scale if amt > 0 else sc.recurring_expense_scale)
    tot += ctx.var_inc_daily * sc.income_scale * 365 - ctx.var_exp_daily * sc.variable_spend_scale * 365
    m = tot / 12 + sc.monthly_cash_delta - sc.savings_transfer_monthly
    for start, interval, amt, count, _ in sc.extra_recurring:
        m += amt * (1.0 if interval == 30 else 30.4375 / interval)   # 30-day cadence == one month
    return m


def risk_level(min_balance: float, essential_monthly: float | None, shortage_days: int = 0) -> str:
    ess = essential_monthly or 0.0
    if min_balance < 0:
        return "critical" if (ess and min_balance < -ess) or shortage_days > 14 else "high"
    if ess and min_balance < 0.5 * ess:
        return "medium"
    return "low"


def project(ctx: ForecastContext, horizon_days: int, sc: Scenario | None = None) -> dict:
    sc = sc or Scenario()
    H = min(max(1, horizon_days), MAX_DAYS)
    by_day: dict[int, float] = {}
    seen_income = set()
    inflow = outflow = 0.0
    for off, amt, direction, name, kind in ctx.events:
        o = off
        if amt > 0:
            if o <= sc.income_suspend_days:
                continue
            if name not in seen_income:
                seen_income.add(name)
                o = off + sc.income_delay_days
            amt = amt * (sc.income_scale if (sc.income_scale_days is None or o <= sc.income_scale_days) else 1.0)
        else:
            amt = amt * sc.recurring_expense_scale
        if o <= H:
            by_day[o] = by_day.get(o, 0.0) + amt
    one_off_by_day: dict[int, float] = {}
    for off, amt, _ in sc.one_offs:
        if 1 <= off <= H:
            one_off_by_day[off] = one_off_by_day.get(off, 0.0) + amt
    sigma_d = math.sqrt(ctx.var_exp_std ** 2 + ctx.var_inc_std ** 2)
    bal, series = ctx.balance, []
    min_bal, min_day, neg_days, low_band_min = bal, 0, 0, bal
    savings_accum = 0.0
    var_catchup = ctx.var_inc_daily * sc.income_scale * sc.income_delay_days if not ctx.has_recurring_income else 0.0
    one_off_total = 0.0
    for day in range(1, H + 1):
        f = by_day.get(day, 0.0)
        suspended = day <= sc.income_suspend_days
        delayed = (not ctx.has_recurring_income) and day <= sc.income_delay_days
        if not suspended and not delayed:
            f += ctx.var_inc_daily * (sc.income_scale if (sc.income_scale_days is None or day <= sc.income_scale_days) else 1.0)
        if (not ctx.has_recurring_income) and sc.income_delay_days and day == sc.income_delay_days + 1 and not suspended:
            f += var_catchup
        f -= ctx.var_exp_daily * sc.variable_spend_scale
        f += sc.monthly_cash_delta / 30.4375
        st = sc.savings_transfer_monthly / 30.4375
        f -= st
        savings_accum += st
        for start, interval, amt, count, _ in sc.extra_recurring:
            if day >= start and (day - start) % interval == 0 and (count is None or (day - start) // interval < count):
                f += amt
        oo = one_off_by_day.get(day, 0.0)
        one_off_total += oo
        f += oo
        bal += f
        band = Z80 * sigma_d * math.sqrt(day)
        series.append({"day": day, "date": (ctx.as_of + timedelta(days=day)).isoformat(), "balance": r2(bal), "lower": r2(bal - band), "upper": r2(bal + band)})
        if bal < min_bal:
            min_bal, min_day = bal, day
        low_band_min = min(low_band_min, bal - band)
        if bal < 0:
            neg_days += 1
        if f > 0:
            inflow += f
        else:
            outflow += -f
    return {"horizon_days": H, "start_balance": r2(ctx.balance), "end_balance": r2(bal), "min_balance": r2(min_bal),
            "min_balance_date": (ctx.as_of + timedelta(days=min_day)).isoformat() if min_day else ctx.as_of.isoformat(),
            "days_below_zero": neg_days, "lowest_balance_in_uncertainty_band": r2(low_band_min),
            "projected_inflows": r2(inflow), "projected_outflows": r2(outflow), "one_off_total": r2(one_off_total),
            "monthly_run_rate_net": r2(run_rate_monthly(ctx, sc)), "savings_transferred": r2(savings_accum),
            "risk_level": risk_level(min_bal, ctx.essential_monthly, neg_days), "series": series}


def balance_forecast(db: Session, user_id, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    ctx = build_context(db, user_id, as_of)
    if ctx.data_status == "none":
        return {"status": "insufficient_data", "message": "No transactions recorded; cannot project a balance.", "type": "prediction"}
    p = project(ctx, 90)
    s = p["series"]
    pick = lambda d: s[d - 1]
    eom_day = (date(as_of.year + (as_of.month == 12), as_of.month % 12 + 1, 1) - timedelta(days=1) - as_of).days
    shortage = [x for x in s if x["balance"] < 0]
    return {
        "type": "prediction", "status": "limited_data" if ctx.data_status == "limited" else "ok",
        "confidence": {"none": "none", "limited": "low", "adequate": "medium", "good": "medium"}[ctx.data_status],
        "as_of": as_of.isoformat(), "current_balance": r2(ctx.balance), "balance_source": ctx.balance_source,
        "horizons": {f"{d}d": {"predicted_balance": pick(d)["balance"], "lower": pick(d)["lower"], "upper": pick(d)["upper"], "date": pick(d)["date"]} for d in (7, 30, 90)},
        "end_of_month": ({"date": pick(eom_day)["date"], "predicted_balance": pick(eom_day)["balance"], "lower": pick(eom_day)["lower"], "upper": pick(eom_day)["upper"]} if eom_day >= 1 else None),
        "potential_shortages": {"first_negative_date": shortage[0]["date"] if shortage else None, "days_below_zero_next_90d": p["days_below_zero"],
                                "lowest_projected_balance": p["min_balance"], "lowest_balance_date": p["min_balance_date"],
                                "possible_in_uncertainty_band": p["lowest_balance_in_uncertainty_band"] < 0},
        "daily_series": s, "series_kind": "predicted",
        "assumptions": ["Known recurring payments/income continue on their detected schedule.",
                        "Non-recurring spending continues at its recent daily average (large one-off purchases excluded).",
                        "Bands are approximate 80% ranges that widen with time; they are not guarantees."],
        "excluded_one_offs": ctx.excluded_one_offs, "data_used": {"transactions": ctx.n_transactions, "history_days": ctx.history_days, "complete_months": ctx.months_of_data},
        "warnings": ctx.warnings,
    }


def monthly_forecast(db: Session, user_id, kind: str, horizon_months: int = 1, as_of: date | None = None, by_category: bool = False) -> dict:
    as_of = as_of or date.today()
    df = load_df(db, user_id)
    months = complete_months(df, as_of)
    mt = monthly_table(df, months)
    series = mt["expenses" if kind == "expenses" else "income"] if len(mt) else pd.Series(dtype=float)
    out = forecast_series(series, horizon_months)
    out.update({"type": "prediction", "kind": kind, "as_of": as_of.isoformat(), "series_kind": "history_vs_forecast",
                "next_months": [str(pd.Period(as_of, "M") + i) for i in range(1, horizon_months + 1)]})
    cur = pd.Period(as_of, "M")
    if not df.empty:
        cm = monthly_table(df, [cur])
        out["current_month_actual_so_far"] = r2(cm.loc[cur, "expenses" if kind == "expenses" else "income"])
    if kind == "expenses" and by_category:
        out["category_forecast"] = category_forecast(db, user_id, as_of)
    ctx = build_context(db, user_id, as_of)
    if ctx.data_status != "none":
        out["expected_over_next_days"] = {f"{d}d": expected_flows(ctx, d)[kind] for d in (7, 30, 90)}
        out["expected_over_next_days_method"] = "known recurring items on schedule + recent non-recurring daily average"
    return out


def expected_flows(ctx: ForecastContext, days: int) -> dict:
    exp = sum(-a for o, a, *_ in ctx.events if a < 0 and o <= days) + ctx.var_exp_daily * days
    inc = sum(a for o, a, *_ in ctx.events if a > 0 and o <= days) + ctx.var_inc_daily * days
    return {"expenses": r2(max(exp, 0)), "income": r2(max(inc, 0))}
