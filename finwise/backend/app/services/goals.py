from __future__ import annotations

import math
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Goal
from app.services.forecasting import ForecastContext, build_context, run_rate_monthly
from app.utils.money import r2

SAFE_SHARE = 0.7   # share of free cash flow considered realistic to commit to goals
MONTH = 30.4375


def _add_months(d: date, m: float) -> date | None:
    if not math.isfinite(m) or m > 1200:        # > 100 years: not a meaningful estimate
        return None
    return d + timedelta(days=int(round(m * MONTH)))


def analyze_goal(g: Goal, capacity_monthly: float, as_of: date, n_active: int = 1) -> dict:
    target, cur, contrib = float(g.target_amount), float(g.current_amount), float(g.monthly_contribution or 0)
    remaining = max(0.0, target - cur)
    pct = round(min(100.0, cur / target * 100), 1) if target else 0.0
    realistic = max(0.0, capacity_monthly) * SAFE_SHARE
    out = {"id": str(g.id), "name": g.name, "target_amount": r2(target), "current_amount": r2(cur), "remaining_amount": r2(remaining),
           "target_date": g.target_date.isoformat() if g.target_date else None, "monthly_contribution": r2(contrib), "progress_pct": pct,
           "status": "achieved" if remaining <= 0 else g.status}
    if remaining <= 0:
        out.update(estimated_completion_date=as_of.isoformat(), feasibility={"verdict": "achieved"}, recommended_monthly_contribution=0.0)
        return out
    est = _add_months(as_of, remaining / contrib) if contrib > 0 else None
    out["estimated_completion_date"] = est.isoformat() if est else None
    months_left = None
    required = None
    if g.target_date:
        months_left = max(0.0, (g.target_date - as_of).days / MONTH)
        required = remaining / months_left if months_left >= 0.25 else remaining
        out["months_remaining"] = round(months_left, 1)
        out["required_monthly_to_hit_target_date"] = r2(required)
        out["on_track_with_current_contribution"] = bool(est and est <= g.target_date) if contrib > 0 else False
    share_cap = realistic / max(1, n_active)
    if capacity_monthly <= 0:
        verdict, rec = "not_feasible_with_current_cash_flow", 0.0
        msg = "Your current monthly cash flow is not positive, so a goal contribution isn't sustainable until spending falls or income rises."
    elif required is not None:
        if required <= share_cap:
            verdict, rec, msg = "feasible", required, "Required monthly amount fits within a prudent share of your free cash flow."
        elif required <= realistic:
            verdict, rec, msg = "feasible_if_prioritised", required, "Feasible only if this goal gets priority over other goals."
        elif required <= capacity_monthly:
            verdict, rec, msg = "stretch", realistic, "Requires almost all free cash flow; consider extending the date."
        else:
            verdict, rec, msg = "unlikely_by_target_date", realistic, "Required amount exceeds your free cash flow; extend the date or reduce the target."
    else:
        verdict, rec, msg = "no_target_date", share_cap, "No target date set; showing a sustainable contribution."
    out["recommended_monthly_contribution"] = r2(rec)
    if rec > 0:
        e2 = _add_months(as_of, remaining / rec)
        out["earliest_completion_at_recommended"] = e2.isoformat() if e2 else None
    out["feasibility"] = {"verdict": verdict, "message": msg, "free_cash_flow_monthly": r2(capacity_monthly),
                          "assumed_share_of_free_cash_flow": SAFE_SHARE}
    return out


def goals_with_analysis(db: Session, user_id, as_of: date | None = None, ctx: ForecastContext | None = None) -> dict:
    as_of = as_of or date.today()
    ctx = ctx or build_context(db, user_id, as_of)
    cap = run_rate_monthly(ctx) if ctx.n_transactions else 0.0
    gs = list(db.scalars(select(Goal).where(Goal.user_id == user_id).order_by(Goal.created_at)))
    active = [g for g in gs if g.status == "active" and float(g.current_amount) < float(g.target_amount)]
    items = [analyze_goal(g, cap, as_of, len(active) or 1) for g in gs]
    total_required = sum(i.get("required_monthly_to_hit_target_date") or 0 for i, g in zip(items, gs) if g in active)
    return {"goals": items, "free_cash_flow_monthly": r2(cap), "combined_required_monthly": r2(total_required),
            "combined_feasible": total_required <= cap * SAFE_SHARE if cap > 0 else False,
            "warnings": ctx.warnings, "data_status": ctx.data_status}


def savings_feasibility(db: Session, user_id, target_amount: float, months: int, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    ctx = build_context(db, user_id, as_of)
    cap = run_rate_monthly(ctx) if ctx.n_transactions else 0.0
    required = target_amount / months if months > 0 else target_amount
    realistic = max(0.0, cap) * SAFE_SHARE
    return {"target_amount": r2(target_amount), "months": months, "required_monthly": r2(required), "free_cash_flow_monthly": r2(cap),
            "realistic_monthly_capacity": r2(realistic), "feasible": required <= realistic,
            "feasible_using_all_free_cash_flow": required <= cap,
            "projected_savings_over_period_at_current_rate": r2(max(0.0, cap) * months),
            "shortfall_monthly": r2(max(0.0, required - realistic)), "data_status": ctx.data_status, "warnings": ctx.warnings,
            "assumption": f"Capacity = {int(SAFE_SHARE * 100)}% of projected monthly free cash flow after recurring commitments."}
