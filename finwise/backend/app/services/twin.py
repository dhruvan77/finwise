"""Financial Digital Twin: a continuously refreshed, data-derived representation of the user's finances."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Anomaly, FinancialProfile, FinancialSnapshot, RecurringPayment
from app.services import analytics
from app.services.anomalies import detect_anomalies
from app.services.forecasting import build_context, expected_flows, project, run_rate_monthly
from app.services.goals import goals_with_analysis
from app.services.notifications import generate_notifications
from app.services.recurring import active_recurring, detect_recurring, upcoming_commitments
from app.utils.money import r2

_FREQ_MONTHLY = {"weekly": 52 / 12, "monthly": 1, "quarterly": 1 / 3, "yearly": 1 / 12}


def build_state(db: Session, user_id, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    ctx = build_context(db, user_id, as_of)
    state = {"as_of": as_of.isoformat(), "currency": "INR", "data_quality": {"status": ctx.data_status, "transactions": ctx.n_transactions,
             "history_days": ctx.history_days, "complete_months": ctx.months_of_data, "warnings": ctx.warnings}}
    if ctx.data_status == "none":
        state["message"] = "No financial data yet. Import a statement or add transactions to build your twin."
        return state
    recs = active_recurring(db, user_id)
    debit_recs = [r for r in recs if r.direction == "debit"]
    income_recs = [r for r in recs if r.direction == "credit" and r.kind in ("salary", "other")]
    rec_monthly = sum(float(r.avg_amount) * _FREQ_MONTHLY[r.frequency] for r in debit_recs)
    salary = max(income_recs, key=lambda r: float(r.avg_amount), default=None)
    trends = analytics.trends(db, user_id, as_of=as_of)
    cats = analytics.categories(db, user_id, start=date(as_of.year, as_of.month, 1) - __import__("datetime").timedelta(days=90), end=as_of, as_of=as_of)
    rr = run_rate_monthly(ctx)
    p30, p90 = project(ctx, 30), project(ctx, 90)
    avg_inc, avg_exp = ctx.avg_monthly_income or 0.0, ctx.avg_monthly_expenses or 0.0
    open_anoms = db.scalars(select(Anomaly).where(Anomaly.user_id == user_id, Anomaly.status == "open")).all()
    goals = goals_with_analysis(db, user_id, as_of, ctx)
    state.update(
        balance={"current": r2(ctx.balance), "source": ctx.balance_source},
        income={"average_monthly": r2(avg_inc), "regular_income_detected": salary is not None,
                "primary_income": ({"name": salary.name, "amount": r2(salary.avg_amount), "frequency": salary.frequency, "next_date": salary.next_date.isoformat() if salary.next_date else None} if salary else None),
                "trend": (trends.get("income", {}) or {}).get("direction")},
        spending={"average_monthly": r2(avg_exp), "essential_monthly": r2(ctx.essential_monthly), "discretionary_monthly": r2(max(0.0, avg_exp - (ctx.essential_monthly or 0.0))),
                  "recurring_commitments_monthly": r2(rec_monthly), "variable_daily_average": r2(ctx.var_exp_daily),
                  "top_categories_last_90d": cats["categories"][:5], "trend": (trends.get("expenses", {}) or {}).get("direction")},
        savings={"average_monthly_net": r2(avg_inc - avg_exp), "savings_rate_pct": round((avg_inc - avg_exp) / avg_inc * 100, 1) if avg_inc > 0 else None,
                 "projected_monthly_free_cash_flow": r2(rr),
                 "emergency_runway_months": round(ctx.balance / ctx.essential_monthly, 1) if ctx.essential_monthly else None},
        recurring={"count": len(recs), "monthly_total_outflow": r2(rec_monthly),
                   "items": [{"name": r.name, "kind": r.kind, "amount": r2(r.avg_amount), "frequency": r.frequency, "next_date": r.next_date.isoformat() if r.next_date else None} for r in debit_recs[:15]]},
        upcoming_payments=[u for u in upcoming_commitments(db, user_id, 30, as_of) if u["direction"] == "debit"][:10],
        goals=[{"name": g["name"], "progress_pct": g["progress_pct"], "feasibility": g["feasibility"].get("verdict")} for g in goals["goals"]],
        forecast={"balance_30d": p30["end_balance"], "balance_90d": p90["end_balance"], "lowest_balance_90d": p90["min_balance"], "risk_level_90d": p90["risk_level"],
                  "expected_expenses_30d": expected_flows(ctx, 30)["expenses"], "series_kind": "predicted"},
        anomalies={"open": len(open_anoms), "high_severity_open": sum(1 for a in open_anoms if a.severity == "high")},
        trends={"income": (trends.get("income", {}) or {}).get("direction"), "expenses": (trends.get("expenses", {}) or {}).get("direction")},
    )
    return state


def refresh_twin(db: Session, user_id, as_of: date | None = None, snapshot: bool = True) -> dict:
    """Recompute everything derived from raw transactions and persist current state (+ daily snapshot)."""
    as_of = as_of or date.today()
    detect_recurring(db, user_id, as_of)
    detect_anomalies(db, user_id, as_of)
    state = build_state(db, user_id, as_of)
    prof = db.get(FinancialProfile, user_id)
    if prof is None:
        prof = FinancialProfile(user_id=user_id, opening_balance=0)
        db.add(prof)
    prof.state, prof.updated_at = state, datetime.now(timezone.utc)
    if snapshot and state.get("balance"):
        snap = db.scalar(select(FinancialSnapshot).where(FinancialSnapshot.user_id == user_id, FinancialSnapshot.snapshot_date == as_of))
        if snap:
            snap.state = state
        else:
            db.add(FinancialSnapshot(user_id=user_id, snapshot_date=as_of, state=state))
    generate_notifications(db, user_id, as_of)
    db.commit()
    return state
