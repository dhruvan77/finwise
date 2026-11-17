"""Analytics, budgets, recurring, predictions, anomalies, twin."""
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.core.errors import AppError, NotFound
from app.models import Anomaly, Budget, FinancialProfile, FinancialSnapshot, Prediction, RecurringPayment
from app.schemas.schemas import AnomalyStatusIn, BudgetIn, BudgetUpdate, RecurringIn, RecurringUpdate
from app.services import analytics, anomalies as anom, forecasting
from app.services.budgets import budget_status
from app.services.recurring import detect_recurring, serialize as ser_rec, upcoming_commitments
from app.services.twin import build_state, refresh_twin
from app.utils.money import q2, r2

router = APIRouter()


# ---- analytics ---------------------------------------------------------------
@router.get("/analytics/summary", tags=["Analytics"])
def a_summary(user: CurrentUser, db: DB, start: date | None = None, end: date | None = None):
    return analytics.summary(db, user.id, start, end)


@router.get("/analytics/monthly", tags=["Analytics"])
def a_monthly(user: CurrentUser, db: DB, months: int = Query(12, ge=1, le=36)):
    return analytics.monthly(db, user.id, months)


@router.get("/analytics/categories", tags=["Analytics"])
def a_categories(user: CurrentUser, db: DB, start: date | None = None, end: date | None = None):
    return analytics.categories(db, user.id, start, end)


@router.get("/analytics/trends", tags=["Analytics"])
def a_trends(user: CurrentUser, db: DB, months: int = Query(6, ge=2, le=24)):
    return analytics.trends(db, user.id, months)


# ---- budgets -----------------------------------------------------------------
def _budget(b: Budget) -> dict:
    return {"id": str(b.id), "category": b.category, "limit_amount": r2(b.limit_amount), "year_month": b.year_month, "alert_threshold": b.alert_threshold}


def _own_budget(db, user, budget_id) -> Budget:
    b = db.scalar(select(Budget).where(Budget.id == budget_id, Budget.user_id == user.id))
    if not b:
        raise NotFound("Budget")
    return b


@router.post("/budgets", status_code=201, tags=["Budgets"])
def create_budget(body: BudgetIn, user: CurrentUser, db: DB):
    dup = db.scalar(select(Budget).where(Budget.user_id == user.id, Budget.category == body.category, Budget.year_month == body.year_month))
    if dup:
        raise AppError(409, "budget_exists", "A budget for this category and month already exists; update it instead.")
    b = Budget(user_id=user.id, **body.model_dump())
    db.add(b)
    db.commit()
    return _budget(b)


@router.get("/budgets", tags=["Budgets"])
def list_budgets(user: CurrentUser, db: DB, month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    """Budgets with utilisation, remaining amount, warnings and 3-month history for the given month (default current)."""
    return budget_status(db, user.id, month)


@router.put("/budgets/{budget_id}", tags=["Budgets"])
def update_budget(budget_id: uuid.UUID, body: BudgetUpdate, user: CurrentUser, db: DB):
    b = _own_budget(db, user, budget_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(b, k, v)
    db.commit()
    return _budget(b)


@router.delete("/budgets/{budget_id}", status_code=204, tags=["Budgets"])
def delete_budget(budget_id: uuid.UUID, user: CurrentUser, db: DB):
    db.delete(_own_budget(db, user, budget_id))
    db.commit()


# ---- recurring ---------------------------------------------------------------
def _own_rec(db, user, pid) -> RecurringPayment:
    r = db.scalar(select(RecurringPayment).where(RecurringPayment.id == pid, RecurringPayment.user_id == user.id))
    if not r:
        raise NotFound("Recurring payment")
    return r


@router.get("/recurring", tags=["Recurring"])
def list_recurring(user: CurrentUser, db: DB, redetect: bool = False, include_dismissed: bool = False, upcoming_days: int = Query(30, ge=1, le=90)):
    if redetect:
        detect_recurring(db, user.id)
        db.commit()
    rows = db.scalars(select(RecurringPayment).where(RecurringPayment.user_id == user.id).order_by(RecurringPayment.next_date)).all()
    items = [ser_rec(r) for r in rows if include_dismissed or r.status != "dismissed"]
    return {"items": items, "upcoming_commitments": upcoming_commitments(db, user.id, upcoming_days)}


@router.post("/recurring", status_code=201, tags=["Recurring"])
def create_recurring(body: RecurringIn, user: CurrentUser, db: DB):
    key = body.name.upper()[:120]
    if db.scalar(select(RecurringPayment).where(RecurringPayment.user_id == user.id, RecurringPayment.merchant_key == key, RecurringPayment.direction == body.direction)):
        raise AppError(409, "recurring_exists", "A recurring payment with this name already exists.")
    r = RecurringPayment(user_id=user.id, merchant_key=key, status="confirmed", is_user_defined=True, confidence=1.0, occurrences=0, **body.model_dump())
    db.add(r)
    db.commit()
    return ser_rec(r)


@router.put("/recurring/{payment_id}", tags=["Recurring"])
def update_recurring(payment_id: uuid.UUID, body: RecurringUpdate, user: CurrentUser, db: DB):
    r = _own_rec(db, user, payment_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(r, k, q2(v) if k == "avg_amount" else v)
    db.commit()
    return ser_rec(r)


@router.delete("/recurring/{payment_id}", status_code=204, tags=["Recurring"])
def delete_recurring(payment_id: uuid.UUID, user: CurrentUser, db: DB):
    r = _own_rec(db, user, payment_id)
    if r.is_user_defined:
        db.delete(r)
    else:
        r.status = "dismissed"      # keep the row so re-detection doesn't resurrect a rejected item
    db.commit()


# ---- predictions -------------------------------------------------------------
def _store(db, user, kind, horizon, result):
    db.add(Prediction(user_id=user.id, kind=kind, horizon_days=horizon, method=str(result.get("method") or "cashflow_projection"),
                      result={k: v for k, v in result.items() if k != "daily_series"}))
    db.commit()


@router.get("/predictions/expenses", tags=["Predictions"])
def p_expenses(user: CurrentUser, db: DB, horizon_months: int = Query(1, ge=1, le=6), by_category: bool = True):
    r = forecasting.monthly_forecast(db, user.id, "expenses", horizon_months, by_category=by_category)
    _store(db, user, "expenses", horizon_months * 30, r)
    return r


@router.get("/predictions/income", tags=["Predictions"])
def p_income(user: CurrentUser, db: DB, horizon_months: int = Query(1, ge=1, le=6)):
    r = forecasting.monthly_forecast(db, user.id, "income", horizon_months)
    _store(db, user, "income", horizon_months * 30, r)
    return r


@router.get("/predictions/balance", tags=["Predictions"])
def p_balance(user: CurrentUser, db: DB, include_series: bool = True):
    r = forecasting.balance_forecast(db, user.id)
    _store(db, user, "balance", 90, r)
    if not include_series:
        r.pop("daily_series", None)
    return r


# ---- anomalies ---------------------------------------------------------------
@router.get("/anomalies", tags=["Anomalies"])
def list_anomalies(user: CurrentUser, db: DB, status: Literal["open", "reviewed_ok", "dismissed", "all"] = "open",
                   severity: Literal["low", "medium", "high"] | None = None):
    rows = anom.detect_anomalies(db, user.id)
    db.commit()
    items = [anom.serialize(a) for a in rows if (status == "all" or a.status == status) and (not severity or a.severity == severity)]
    return {"items": items, "total": len(items), "note": anom.DISCLAIMER}


@router.get("/anomalies/{anomaly_id}", tags=["Anomalies"])
def get_anomaly(anomaly_id: uuid.UUID, user: CurrentUser, db: DB):
    a = db.scalar(select(Anomaly).where(Anomaly.id == anomaly_id, Anomaly.user_id == user.id))
    if not a:
        raise NotFound("Anomaly")
    return anom.serialize(a)


@router.put("/anomalies/{anomaly_id}/status", tags=["Anomalies"])
def set_anomaly_status(anomaly_id: uuid.UUID, body: AnomalyStatusIn, user: CurrentUser, db: DB):
    a = db.scalar(select(Anomaly).where(Anomaly.id == anomaly_id, Anomaly.user_id == user.id))
    if not a:
        raise NotFound("Anomaly")
    a.status = body.status
    db.commit()
    return anom.serialize(a)


# ---- digital twin ------------------------------------------------------------
@router.get("/financial-twin", tags=["Financial Digital Twin"])
def twin(user: CurrentUser, db: DB, refresh: bool = False):
    """Current financial state derived from stored data. `refresh=true` recomputes detections and saves a snapshot."""
    if refresh:
        return refresh_twin(db, user.id)
    return build_state(db, user.id)


@router.get("/financial-twin/history", tags=["Financial Digital Twin"])
def twin_history(user: CurrentUser, db: DB, limit: int = Query(30, ge=1, le=365), full: bool = False):
    snaps = db.scalars(select(FinancialSnapshot).where(FinancialSnapshot.user_id == user.id).order_by(FinancialSnapshot.snapshot_date.desc()).limit(limit)).all()
    out = []
    for s in snaps:
        st = s.state or {}
        item = {"date": s.snapshot_date.isoformat(), "balance": (st.get("balance") or {}).get("current"),
                "average_monthly_expenses": (st.get("spending") or {}).get("average_monthly"), "savings_rate_pct": (st.get("savings") or {}).get("savings_rate_pct"),
                "forecast_balance_30d": (st.get("forecast") or {}).get("balance_30d")}
        if full:
            item["state"] = st
        out.append(item)
    return {"snapshots": out, "count": len(out)}
