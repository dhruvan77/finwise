from __future__ import annotations

import logging

import httpx
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (Account, AIConversation, AIMessage, Anomaly, Budget, CategoryRule, FinancialProfile, FinancialSnapshot, Goal,
                        ImportJob, Notification, Prediction, RecurringPayment, Simulation, StressTest, Transaction, User)

log = logging.getLogger("finwise.data")
# deletion order respects FKs (children first)
ORDER = [AIMessage, AIConversation, Anomaly, Notification, Simulation, StressTest, Prediction, FinancialSnapshot, FinancialProfile, Goal,
         Budget, RecurringPayment, CategoryRule, Transaction, ImportJob, Account]


def _row(o) -> dict:
    return {c.key: getattr(o, c.key) for c in inspect(o).mapper.column_attrs}


def export_user_data(db: Session, user: User) -> dict:
    data = {"user": _row(user)}
    for m in reversed(ORDER):
        rows = db.scalars(select(m).where(m.user_id == user.id)).all()
        data[m.__tablename__] = [_row(r) for r in rows]
    return jsonable_encoder({"format_version": 1, "exported_for": str(user.id), "data": data})


def delete_user_data(db: Session, user: User) -> dict:
    counts = {}
    for m in ORDER:
        counts[m.__tablename__] = db.execute(delete(m).where(m.user_id == user.id)).rowcount
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
    s = get_settings()
    auth_deleted = False
    if s.supabase_url and s.supabase_service_role_key:
        try:
            r = httpx.delete(f"{s.supabase_url}/auth/v1/admin/users/{user.id}", timeout=15,
                             headers={"apikey": s.supabase_service_role_key, "Authorization": f"Bearer {s.supabase_service_role_key}"})
            auth_deleted = r.status_code < 300
        except httpx.HTTPError:
            log.warning("Supabase auth user deletion failed")
    return {"deleted_rows": counts, "auth_user_deleted": auth_deleted,
            "note": None if auth_deleted else "Auth identity not removed (SUPABASE_SERVICE_ROLE_KEY not configured or call failed)."}
