"""Recurring payment / subscription / salary detection with interval + amount regularity checks."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RecurringPayment
from app.services.frames import load_df
from app.utils.money import q2, r2

FREQ_RANGES = [("weekly", 5, 9, 7), ("monthly", 25, 36, 30), ("quarterly", 80, 100, 91), ("yearly", 350, 380, 365)]
KIND_BY_CATEGORY = {"Subscriptions": "subscription", "Housing and rent": "rent", "EMI and loan payments": "emi",
                    "Insurance": "insurance", "Utilities": "utility", "Salary and income": "salary",
                    "Transfers": "transfer", "Investments": "investment"}
AMOUNT_TOLERANT = {"Utilities", "Salary and income"}
SKIP_MERCHANTS = {"UNKNOWN", "ATM WDL", "ATM"}


def add_months(d: date, n: int) -> date:
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def _freq_for(median_interval: float):
    for name, lo, hi, nominal in FREQ_RANGES:
        if lo <= median_interval <= hi:
            return name, nominal
    return None, None


def _step_date(anchor: date, freq: str, k: int) -> date:
    if freq == "weekly":
        return anchor + timedelta(days=7 * k)
    return add_months(anchor, {"monthly": 1, "quarterly": 3, "yearly": 12}[freq] * k)


def occurrence_dates(rp: RecurringPayment, start: date, end: date) -> list[date]:
    """Expected occurrences of a recurring payment within [start, end]. An overdue next_date within half an
    interval is treated as 'pending' and expected on `start`."""
    if not rp.next_date or rp.status == "dismissed":
        return []
    nominal = {"weekly": 7, "monthly": 30, "quarterly": 91, "yearly": 365}[rp.frequency]
    out, k = [], 0
    anchor = rp.next_date
    d = anchor
    while d < start:
        overdue = (start - d).days
        if k == 0 and overdue <= nominal * 0.5 and rp.last_date is not None and rp.last_date < d:
            out.append(start)      # pending / slightly late
        k += 1
        d = _step_date(anchor, rp.frequency, k)
        if k > 2000:
            break
    while d <= end:
        out.append(d)
        k += 1
        d = _step_date(anchor, rp.frequency, k)
    return out


def detect_recurring(db: Session, user_id, as_of: date | None = None) -> list[RecurringPayment]:
    as_of = as_of or date.today()
    df = load_df(db, user_id)
    existing = {(r.merchant_key, r.direction): r for r in db.scalars(select(RecurringPayment).where(RecurringPayment.user_id == user_id))}
    seen_keys = set()
    if not df.empty:
        d = df[(~df.is_internal) & (df.flow != "refund") & (~df.merchant.isin(SKIP_MERCHANTS)) & (df.merchant != "")]
        for (merchant, direction), g in d.groupby(["merchant", "direction"]):
            day = g.groupby("date").agg(amount=("amount", "sum"), id=("id", "last"), category=("category", lambda s: s.mode().iat[0]),
                                        desc=("description", "last")).reset_index().sort_values("date")
            n = len(day)
            if n < 3 and not (n == 2 and (day["date"].iat[1] - day["date"].iat[0]).days in range(350, 381)):
                continue
            intervals = np.diff(day["date"].values).astype("timedelta64[D]").astype(int)
            med = float(np.median(intervals))
            freq, nominal = _freq_for(med)
            if not freq:
                continue
            icv = float(np.std(intervals) / np.mean(intervals)) if len(intervals) > 1 else 0.0
            if icv > 0.25:
                continue
            amounts = day["amount"].values.astype(float)
            acv = float(np.std(amounts) / np.mean(amounts)) if np.mean(amounts) else 1.0
            category = day["category"].iat[-1]
            if acv > (0.6 if category in AMOUNT_TOLERANT else 0.25):
                continue
            last_date = day["date"].iat[-1].date()
            if (as_of - last_date).days > nominal * 2.2:
                continue   # stopped
            conf = 0.5 * (1 - min(1, icv * 2)) + 0.3 * (1 - min(1, acv)) + 0.2 * min(1, n / 6)
            last_amt = float(amounts[-1])
            prev = amounts[:-1]
            change = round((last_amt - float(prev.mean())) / float(prev.mean()) * 100, 1) if len(prev) >= 2 and prev.mean() else None
            kind = KIND_BY_CATEGORY.get(category, "other")
            key = (merchant, direction)
            seen_keys.add(key)
            rp = existing.get(key)
            next_date = _step_date(last_date, freq, 1)
            if freq == "monthly":
                dom = int(np.median([x.day for x in day["date"]]))
                nm = add_months(date(last_date.year, last_date.month, 1), 1)
                next_date = date(nm.year, nm.month, min(dom, monthrange(nm.year, nm.month)[1]))
            vals = dict(name=merchant.title(), category=category, kind=kind, frequency=freq, avg_amount=q2(amounts.mean()),
                        last_amount=q2(last_amt), last_date=last_date, next_date=next_date, occurrences=n,
                        amount_change_pct=change, confidence=round(conf, 2), last_transaction_id=_uuid(day["id"].iat[-1]))
            if rp is None:
                db.add(RecurringPayment(user_id=user_id, merchant_key=merchant, direction=direction, status="detected", **vals))
            elif not rp.is_user_defined:
                for k, v in vals.items():
                    setattr(rp, k, v)
    # drop stale auto-detected (never touch confirmed/dismissed/user-defined)
    for key, rp in existing.items():
        if key not in seen_keys and rp.status == "detected" and not rp.is_user_defined:
            db.delete(rp)
    db.flush()
    return list(db.scalars(select(RecurringPayment).where(RecurringPayment.user_id == user_id).order_by(RecurringPayment.next_date)))


def _uuid(s):
    import uuid
    return uuid.UUID(str(s))


def active_recurring(db: Session, user_id) -> list[RecurringPayment]:
    return list(db.scalars(select(RecurringPayment).where(RecurringPayment.user_id == user_id, RecurringPayment.status != "dismissed")))


def upcoming_commitments(db: Session, user_id, days: int = 30, as_of: date | None = None) -> list[dict]:
    as_of = as_of or date.today()
    out = []
    for rp in active_recurring(db, user_id):
        for d in occurrence_dates(rp, as_of, as_of + timedelta(days=days)):
            out.append({"recurring_id": str(rp.id), "name": rp.name, "kind": rp.kind, "direction": rp.direction,
                        "date": d.isoformat(), "amount": r2(rp.avg_amount), "category": rp.category})
    return sorted(out, key=lambda x: x["date"])


def serialize(rp: RecurringPayment) -> dict:
    return {"id": str(rp.id), "name": rp.name, "merchant_key": rp.merchant_key, "category": rp.category, "kind": rp.kind,
            "direction": rp.direction, "frequency": rp.frequency, "avg_amount": r2(rp.avg_amount), "last_amount": r2(rp.last_amount),
            "last_date": rp.last_date.isoformat() if rp.last_date else None, "next_date": rp.next_date.isoformat() if rp.next_date else None,
            "occurrences": rp.occurrences, "amount_change_pct": rp.amount_change_pct, "confidence": rp.confidence,
            "status": rp.status, "is_user_defined": rp.is_user_defined}
