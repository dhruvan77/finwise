"""Notification engine. Rules generate Notification rows (in-app channel); other channels plug in via NotificationChannel."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Anomaly, Goal, Notification
from app.services.anomalies import serialize as ser_anomaly
from app.services.budgets import budget_status
from app.services.forecasting import ForecastContext, build_context, project
from app.services.recurring import upcoming_commitments


class NotificationChannel(ABC):
    """Implement `deliver` for email/push/SMS and register it in CHANNELS. Core generation logic never changes."""
    name = "base"

    @abstractmethod
    def deliver(self, user_id, notification: Notification) -> bool: ...


class InAppChannel(NotificationChannel):
    name = "in_app"

    def deliver(self, user_id, notification: Notification) -> bool:
        return True      # row in DB *is* the in-app delivery


CHANNELS: list[NotificationChannel] = [InAppChannel()]


def _emit(db: Session, user_id, existing: set, type_: str, title: str, message: str, severity: str, key: str, data: dict | None = None) -> bool:
    if key in existing:
        return False
    n = Notification(user_id=user_id, type=type_, title=title, message=message, severity=severity, dedupe_key=key, data=data or {})
    n.delivered_channels = [c.name for c in CHANNELS if c.deliver(user_id, n)]
    db.add(n)
    existing.add(key)
    return True


def generate_notifications(db: Session, user_id, as_of: date | None = None, ctx: ForecastContext | None = None) -> int:
    as_of = as_of or date.today()
    existing = set(db.scalars(select(Notification.dedupe_key).where(Notification.user_id == user_id)))
    n = 0
    # unusual spending / pattern changes
    for a in db.scalars(select(Anomaly).where(Anomaly.user_id == user_id, Anomaly.status == "open")):
        if a.severity in ("high", "medium") and a.date and (as_of - a.date).days <= 30:
            t = "spending_pattern_change" if a.type in ("sudden_spending_increase", "unexpected_category_spending", "unexpected_cashflow_change") else "unusual_spending"
            n += _emit(db, user_id, existing, t, "Unusual activity flagged", a.reason, "warning" if a.severity == "high" else "info",
                       f"anomaly:{a.id}", {"anomaly_id": str(a.id)})
    # upcoming recurring payments (next 3 days)
    for u in upcoming_commitments(db, user_id, 3, as_of):
        if u["direction"] == "debit":
            n += _emit(db, user_id, existing, "upcoming_payment", f"Upcoming: {u['name']}", f"₹{u['amount']:,.0f} expected on {u['date']}.", "info",
                       f"upcoming:{u['recurring_id']}:{u['date']}", u)
    # budgets
    for b in budget_status(db, user_id, as_of=as_of)["budgets"]:
        if b["status"] != "ok":
            level = "critical" if b["status"] == "exceeded" else "warning"
            n += _emit(db, user_id, existing, "budget_limit", f"{b['category']} budget {b['status']}", b["warning"] or "", level,
                       f"budget:{b['budget_id']}:{as_of:%Y-%m}:{b['status']}", {"budget_id": b["budget_id"], "utilization_pct": b["utilization_pct"]})
    # cash-flow risk / predicted low balance
    ctx = ctx or build_context(db, user_id, as_of)
    if ctx.data_status in ("adequate", "good"):
        p = project(ctx, 30)
        if p["days_below_zero"] > 0:
            n += _emit(db, user_id, existing, "cash_flow_risk", "Possible cash shortage ahead",
                       f"Projected balance may fall below zero around {p['min_balance_date']} (lowest ₹{p['min_balance']:,.0f}). Projection only, not certain.",
                       "critical", f"cashflow:{as_of:%Y-%m-%d}", {"min_balance": p["min_balance"]})
        elif ctx.essential_monthly and p["min_balance"] < 0.5 * ctx.essential_monthly:
            n += _emit(db, user_id, existing, "predicted_low_balance", "Balance projected to run low",
                       f"Projected balance could drop to ₹{p['min_balance']:,.0f} by {p['min_balance_date']}.", "warning",
                       f"lowbal:{as_of:%Y-%m}", {"min_balance": p["min_balance"]})
    # goal progress milestones
    for g in db.scalars(select(Goal).where(Goal.user_id == user_id, Goal.status.in_(["active", "achieved"]))):
        pct = float(g.current_amount) / float(g.target_amount) * 100 if float(g.target_amount) else 0
        for m in (25, 50, 75, 100):
            if pct >= m > g.last_notified_pct:
                n += _emit(db, user_id, existing, "goal_progress", f"Goal '{g.name}' reached {m}%", f"You're {pct:.0f}% of the way to ₹{float(g.target_amount):,.0f}.",
                           "info", f"goal:{g.id}:{m}", {"goal_id": str(g.id)})
                g.last_notified_pct = m
    db.flush()
    return n


def serialize(n: Notification) -> dict:
    return {"id": str(n.id), "type": n.type, "title": n.title, "message": n.message, "severity": n.severity, "is_read": n.is_read,
            "data": n.data, "created_at": n.created_at.isoformat() if n.created_at else None, "channels": n.delivered_channels}
