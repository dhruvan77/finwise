"""What-if simulator, stress tester and opportunity-cost calculator (deterministic)."""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Simulation, StressTest
from app.services.forecasting import ForecastContext, Scenario, build_context, project, risk_level, run_rate_monthly
from app.services.goals import MONTH, SAFE_SHARE, analyze_goal
from app.utils.money import r2

DISCLAIMER = ("Hypothetical simulation based on your recorded history. It is informational decision support, not guaranteed "
              "outcomes or regulated financial advice.")
HORIZONS = (30, 60, 90, 180)


def emi_amount(principal: float, annual_rate_pct: float, months: int) -> float:
    if months <= 0:
        raise AppError(422, "invalid_params", "months must be > 0")
    r = annual_rate_pct / 1200.0
    if r == 0:
        return principal / months
    f = (1 + r) ** months
    return principal * r * f / (f - 1)


def _slim(p: dict) -> dict:
    return {k: v for k, v in p.items() if k != "series"}


def _series(p: dict) -> list[dict]:
    return [{"day": x["day"], "date": x["date"], "balance": x["balance"], "lower": x["lower"], "upper": x["upper"]} for x in p["series"]]


def _goal_effects(db_goals: list, ctx: ForecastContext, sc: Scenario, one_off: float) -> list[dict]:
    cap_b, cap_a = run_rate_monthly(ctx), run_rate_monthly(ctx, sc)
    out = []
    n = len(db_goals) or 1
    for g in db_goals:
        b = analyze_goal(g, cap_b, ctx.as_of, n)
        a = analyze_goal(g, cap_a, ctx.as_of, n)
        realistic = max(cap_a, 0) * SAFE_SHARE
        delay = round(one_off / realistic, 1) if one_off > 0 and realistic > 0 else None
        out.append({"goal_id": b["id"], "name": b["name"], "progress_pct": b["progress_pct"],
                    "feasibility_before": b["feasibility"].get("verdict"), "feasibility_after": a["feasibility"].get("verdict"),
                    "recommended_monthly_before": b.get("recommended_monthly_contribution"), "recommended_monthly_after": a.get("recommended_monthly_contribution"),
                    "estimated_delay_months_from_one_off_cost": delay})
    return out


def compare(ctx: ForecastContext, sc: Scenario, horizon: int, kind: str, goals: list, assumptions: list[str], extra: dict | None = None) -> dict:
    if ctx.data_status == "none":
        raise AppError(422, "insufficient_data", "No transactions recorded yet; simulations need financial history to be meaningful.")
    before, after = project(ctx, horizon), project(ctx, horizon, sc)
    ess = ctx.essential_monthly or 0.0
    min_a = after["min_balance"]
    one_off = sum(-a for _, a, _ in sc.one_offs if a < 0)
    if min_a < 0:
        verdict, feasible = "not_recommended", False
    elif min_a < ess:
        verdict, feasible = "affordable_with_caution", True
    else:
        verdict, feasible = "affordable", True
    risks = []
    if after["days_below_zero"]:
        risks.append(f"Projected balance falls below zero on {after['days_below_zero']} day(s); lowest ₹{min_a:,.0f} around {after['min_balance_date']}.")
    elif after["lowest_balance_in_uncertainty_band"] < 0:
        risks.append("Within the uncertainty band the balance could dip below zero.")
    if 0 <= min_a < ess:
        risks.append(f"Balance drops below one month of essential expenses (₹{ess:,.0f}); thin safety buffer.")
    if after["monthly_run_rate_net"] < 0:
        risks.append("Monthly net cash flow is negative under this scenario.")
    if before["min_balance"] < 0:
        risks.append("Your baseline projection already dips below zero even without this scenario.")
    runway = (after["end_balance"] / ess) if ess > 0 else None
    res = {
        "type": "simulation", "kind": kind, "hypothetical": True, "horizon_days": horizon, "as_of": ctx.as_of.isoformat(),
        "before": _slim(before), "after": _slim(after),
        "series": {"before": _series(before), "after": _series(after), "kind": "predicted"},
        "delta": {"end_balance": r2(after["end_balance"] - before["end_balance"]), "min_balance": r2(min_a - before["min_balance"]),
                  "monthly_savings": r2(after["monthly_run_rate_net"] - before["monthly_run_rate_net"])},
        "monthly_savings_before": before["monthly_run_rate_net"], "monthly_savings_after": after["monthly_run_rate_net"],
        "expected_future_balance": after["end_balance"],
        "emergency_runway_months_after": round(runway, 1) if runway is not None else None,
        "effect_on_goals": _goal_effects(goals, ctx, sc, one_off),
        "cash_flow_risks": risks, "feasible": feasible, "verdict": verdict, "risk_level": after["risk_level"],
        "safety_buffer_used": r2(ess), "assumptions": assumptions + ["Large non-recurring purchases in your history are excluded from the spending run-rate.",
                                                                       f"Safety buffer = one month of essential expenses (₹{ess:,.0f})."],
        "data_used": {"status": ctx.data_status, "transactions": ctx.n_transactions, "complete_months": ctx.months_of_data, "balance_source": ctx.balance_source},
        "warnings": ctx.warnings, "disclaimer": DISCLAIMER,
    }
    if extra:
        res.update(extra)
    return res


def _day(n) -> int:
    return max(1, int(n))


def build_scenario(ctx: ForecastContext, db: Session, user_id, kind: str, p: dict, horizon: int) -> tuple[Scenario, list[str], dict]:
    sc, notes, extra = Scenario(), [], {}
    k = kind.lower()
    if k in ("purchase", "laptop", "phone", "large_purchase"):
        amount = float(p["amount"])
        day = _day(p.get("days_from_now", 1))
        pay = p.get("payment") or {"type": "cash"}
        if pay.get("type") == "emi":
            months, rate, down = int(pay["months"]), float(pay.get("annual_rate_pct", 0)), float(pay.get("down_payment", 0))
            emi = emi_amount(amount - down, rate, months)
            if down:
                sc.one_offs.append((day, -down, "down payment"))
            sc.extra_recurring.append((day + 30, 30, -emi, months, "EMI"))
            extra["emi"] = {"monthly_emi": r2(emi), "months": months, "annual_rate_pct": rate, "total_interest": r2(emi * months - (amount - down))}
            notes.append(f"Financed with EMI: ₹{emi:,.0f}/month for {months} months at {rate}% p.a.")
        else:
            sc.one_offs.append((day, -amount, p.get("item_name", "purchase")))
            notes.append(f"Paid in full in cash on day {day} from today.")
    elif k == "loan":
        principal, rate, months = float(p["principal"]), float(p.get("annual_rate_pct", 12)), int(p["months"])
        day = _day(p.get("days_from_now", 1))
        emi = emi_amount(principal, rate, months)
        if p.get("receive_principal", True):
            sc.one_offs.append((day, principal, "loan disbursed"))
        sc.extra_recurring.append((day + 30, 30, -emi, months, "EMI"))
        extra["loan"] = {"monthly_emi": r2(emi), "months": months, "total_repayment": r2(emi * months), "total_interest": r2(emi * months - principal)}
        notes.append(f"Loan ₹{principal:,.0f} at {rate}% for {months} months; EMI ₹{emi:,.0f}.")
    elif k in ("increase_monthly_savings", "increase_savings"):
        sc.savings_transfer_monthly = float(p["monthly_amount"])
        notes.append("Savings are transferred out of the spending balance each month and tracked separately.")
    elif k == "reduce_category_spending":
        cat, pct = p["category"], float(p["percent"])
        from app.services.frames import complete_months, load_df
        df = load_df(db, user_id)
        months = complete_months(df, ctx.as_of)[-3:]
        e = df[(df.category == cat) & df.flow.isin(["expense", "refund"])]
        if months:
            e = e[e.date.dt.to_period("M").isin(months)]
            avg = float(e.signed_expense.sum()) / len(months)
        else:
            avg = float(e.signed_expense.sum())
        sc.monthly_cash_delta = avg * pct / 100
        extra["category_average_monthly"] = r2(avg)
        notes.append(f"Reduces {cat} spending by {pct:.0f}% of its recent monthly average (₹{avg:,.0f}).")
        if avg <= 0:
            notes.append(f"No recent {cat} spending found, so the reduction has no effect.")
    elif k in ("salary_delay", "delay_salary"):
        sc.income_delay_days = int(p["days"])
        notes.append(f"The next income payment is delayed by {sc.income_delay_days} days.")
    elif k in ("salary_loss", "lose_salary", "income_loss"):
        sc.income_suspend_days = int(float(p.get("months", 1)) * 30)
        notes.append(f"All income stops for {p.get('months', 1)} month(s).")
    elif k == "unexpected_expense":
        sc.one_offs.append((_day(p.get("days_from_now", 1)), -float(p["amount"]), "unexpected expense"))
    elif k == "increase_income":
        if p.get("monthly_amount") is not None:
            sc.monthly_cash_delta = float(p["monthly_amount"])
        else:
            sc.monthly_cash_delta = (ctx.avg_monthly_income or 0.0) * float(p["percent"]) / 100
        notes.append(f"Income rises by ₹{sc.monthly_cash_delta:,.0f}/month (before any tax).")
    elif k == "new_subscription":
        sc.extra_recurring.append((_day(p.get("days_from_now", 1)), 30, -float(p["monthly_amount"]), None, "subscription"))
    else:
        raise AppError(422, "unknown_scenario", f"Unknown scenario type '{kind}'.")
    return sc, notes, extra


SCENARIO_KINDS = ["purchase", "laptop", "phone", "large_purchase", "loan", "increase_monthly_savings", "reduce_category_spending",
                  "salary_delay", "salary_loss", "unexpected_expense", "increase_income", "new_subscription"]


def run_scenario(db: Session, user_id, kind: str, params: dict, horizon: int = 90, as_of: date | None = None, persist: bool = True) -> dict:
    from sqlalchemy import select
    from app.models import Goal
    ctx = build_context(db, user_id, as_of)
    sc, notes, extra = build_scenario(ctx, db, user_id, kind, params, horizon)
    goals = [g for g in db.scalars(select(Goal).where(Goal.user_id == user_id, Goal.status == "active"))]
    res = compare(ctx, sc, horizon, kind, goals, notes, extra)
    res["params"] = params
    if persist:
        row = Simulation(user_id=user_id, kind=kind, horizon_days=horizon, params=params, result=res)
        db.add(row)
        db.flush()
        res["simulation_id"] = str(row.id)
    return res


def simulate_purchase(db: Session, user_id, amount: float, item_name: str = "purchase", horizon: int = 90, days_from_now: int = 1,
                      payment: dict | None = None, as_of: date | None = None, persist: bool = True) -> dict:
    params = {"amount": amount, "item_name": item_name, "days_from_now": days_from_now, "payment": payment or {"type": "cash"}}
    res = run_scenario(db, user_id, "purchase", params, horizon, as_of, persist)
    ctx_rr = res["monthly_savings_before"]
    res["purchase_summary"] = {"item": item_name, "amount": r2(amount),
                               "months_of_current_free_cash_flow": round(amount / ctx_rr, 1) if ctx_rr and ctx_rr > 0 else None}
    return res


# ------------------------------------------------------------------ stress tests
STRESS_KINDS = ["salary_delay", "medical_expense", "large_purchase", "income_reduction", "expense_increase", "income_loss"]


def run_stress_test(db: Session, user_id, kind: str, params: dict, horizon: int = 90, as_of: date | None = None, persist: bool = True) -> dict:
    ctx = build_context(db, user_id, as_of)
    if ctx.data_status == "none":
        raise AppError(422, "insufficient_data", "No transactions recorded yet; stress tests need financial history.")
    sc, desc = Scenario(), ""
    if kind == "salary_delay":
        d = int(params.get("days", 15))
        sc.income_delay_days, desc = d, f"Income arrives {d} days late."
    elif kind == "medical_expense":
        a = float(params.get("amount", 50000))
        sc.one_offs.append((_day(params.get("days_from_now", 1)), -a, "medical"))
        desc = f"Unexpected medical expense of ₹{a:,.0f}."
    elif kind == "large_purchase":
        a = float(params["amount"])
        sc.one_offs.append((_day(params.get("days_from_now", 1)), -a, "purchase"))
        desc = f"Sudden purchase of ₹{a:,.0f}."
    elif kind == "income_reduction":
        pct, months = float(params.get("percent", 30)), float(params.get("months", 3))
        sc.income_scale, sc.income_scale_days = 1 - pct / 100, int(months * 30)
        desc = f"Income falls {pct:.0f}% for {months:g} month(s)."
    elif kind == "expense_increase":
        pct = float(params.get("percent", 20))
        sc.recurring_expense_scale = sc.variable_spend_scale = 1 + pct / 100
        desc = f"All spending rises {pct:.0f}%."
    elif kind == "income_loss":
        months = float(params.get("months", 2))
        sc.income_suspend_days, desc = int(months * 30), f"Income stops for {months:g} month(s)."
    else:
        raise AppError(422, "unknown_stress_test", f"Unknown stress test '{kind}'. Valid: {', '.join(STRESS_KINDS)}")
    base, stressed = project(ctx, horizon), project(ctx, horizon, sc)
    long = project(ctx, 365, sc)
    neg = next((x for x in long["series"] if x["balance"] < 0), None)
    ess = ctx.essential_monthly or 0.0
    avg_exp = ctx.avg_monthly_expenses or 0.0
    shortfall = max(0.0, -stressed["min_balance"])
    level = risk_level(stressed["min_balance"], ess, stressed["days_below_zero"])
    actions = []
    if shortfall > 0:
        actions.append(f"Build a cash cushion of at least ₹{shortfall:,.0f} (or arrange credit/family backup) to cover the projected shortfall.")
        actions.append("Pause discretionary spending (shopping, dining out, entertainment, new subscriptions) for the stress period.")
    if level in ("medium", "high", "critical"):
        actions.append("Review which recurring commitments can be postponed or renegotiated.")
    if ess and ctx.balance / ess < 3:
        actions.append(f"Aim for an emergency fund of 3-6 months of essential expenses (₹{3 * ess:,.0f}-₹{6 * ess:,.0f}); current cover is {ctx.balance / ess:.1f} months.")
    if not actions:
        actions.append("Your projected balance stays comfortably positive under this scenario; maintain your emergency fund.")
    res = {
        "type": "stress_test", "kind": kind, "hypothetical": True, "scenario": desc, "params": params, "horizon_days": horizon,
        "as_of": ctx.as_of.isoformat(),
        "emergency_cash_runway": {"months_of_essential_expenses": round(ctx.balance / ess, 1) if ess else None,
                                  "months_of_total_expenses": round(ctx.balance / avg_exp, 1) if avg_exp else None,
                                  "days_until_zero_under_scenario": neg["day"] if neg else None,
                                  "zero_balance_date_under_scenario": neg["date"] if neg else None, "runway_search_window_days": 365},
        "can_cover_essential_expenses": stressed["min_balance"] >= 0,
        "baseline": _slim(base), "stressed": _slim(stressed),
        "series": {"baseline": _series(base), "stressed": _series(stressed), "kind": "predicted"},
        "expected_balance_end": stressed["end_balance"], "lowest_balance": stressed["min_balance"], "lowest_balance_date": stressed["min_balance_date"],
        "potential_shortfall": r2(shortfall), "risk_level": level, "suggested_actions": actions,
        "data_used": {"status": ctx.data_status, "complete_months": ctx.months_of_data, "balance_source": ctx.balance_source},
        "warnings": ctx.warnings, "disclaimer": DISCLAIMER + " This is a hypothetical stress test, not a prediction that the event will occur.",
    }
    if persist:
        row = StressTest(user_id=user_id, kind=kind, horizon_days=horizon, params=params, result=res)
        db.add(row)
        db.flush()
        res["stress_test_id"] = str(row.id)
    return res


# ------------------------------------------------------------------ opportunity cost
def opportunity_cost(amount: float, years: float = 5, annual_returns_pct: list[float] | None = None, monthly_equivalent: bool = False) -> dict:
    rates = annual_returns_pct or [4.0, 8.0, 12.0]
    if amount <= 0 or years <= 0:
        raise AppError(422, "invalid_params", "amount and years must be positive")
    scenarios = []
    for r in rates:
        lump = amount * (1 + r / 100) ** years
        item = {"annual_return_pct": r, "lump_sum_value": r2(lump), "growth": r2(lump - amount)}
        if monthly_equivalent:
            mr, n = r / 1200, int(years * 12)
            per_month = amount / n
            fv = per_month * (((1 + mr) ** n - 1) / mr) if mr else amount
            item["if_spread_monthly_value"] = r2(fv)
        scenarios.append(item)
    return {"type": "opportunity_cost", "amount": r2(amount), "years": years, "scenarios": scenarios,
            "assumptions": ["Constant annual return, compounded annually, before tax, fees and inflation.",
                            "Rates are illustrative inputs you can change, NOT forecasts or promises of returns.",
                            "Actual investment returns can be lower than these figures, including losses."],
            "disclaimer": DISCLAIMER}
