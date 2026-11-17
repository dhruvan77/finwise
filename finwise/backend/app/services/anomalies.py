"""Anomaly detection: robust statistics + duplicates + recurring changes + Isolation Forest.
An anomaly is a statistical outlier worth reviewing, never a fraud verdict."""
from __future__ import annotations

import re
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Anomaly, RecurringPayment
from app.services.frames import complete_months, load_df, monthly_table
from app.utils.money import q2, r2

DISCLAIMER = "This is a statistical flag for review, not a determination of fraud."
_WITHDRAW = re.compile(r"\bATM\b|CASH WITHDRAWAL|\bWDL\b|CASH WDL", re.I)


def _sev(ratio: float, hi: float, med: float) -> str:
    return "high" if ratio >= hi else "medium" if ratio >= med else "low"


def _cand(tid, type_, amount, d, reason, severity, confidence, action, key, **details):
    return {"transaction_id": tid, "type": type_, "amount": amount, "date": d, "reason": reason, "severity": severity,
            "confidence": confidence, "recommended_action": action, "dedupe_key": key, "details": details}


def detect_anomalies(db: Session, user_id, as_of: date | None = None) -> list[Anomaly]:
    as_of = as_of or date.today()
    df = load_df(db, user_id, end=as_of)
    cands: list[dict] = []
    if not df.empty:
        e = df[(df.flow == "expense") & (df.direction == "debit")].copy()
        recent = e[e.date >= pd.Timestamp(as_of - timedelta(days=180))]
        rec_keys = {r.merchant_key for r in db.scalars(select(RecurringPayment).where(RecurringPayment.user_id == user_id, RecurringPayment.status != "dismissed"))}

        # 1. unusually high transaction vs category history (robust z-score)
        for cat, g in e.groupby("category"):
            if len(g) < 6 or cat == "Housing and rent":
                continue
            med = float(g.amount.median())
            mad = float((g.amount - med).abs().median())
            scale = 1.4826 * mad if mad > 0 else float(g.amount.std() or 0)
            if scale <= 0:
                continue
            repeat = g.groupby(["merchant", "amount"]).size()
            for _, r in g[g.date >= pd.Timestamp(as_of - timedelta(days=180))].iterrows():
                z = (r.amount - med) / scale
                if repeat.get((r.merchant, r.amount), 0) >= 3:
                    continue      # a habitual identical purchase (e.g. monthly fuel) is not unusual
                if z > 3.5 and r.amount >= 1000 and r.amount > 2 * med and r.merchant not in rec_keys:
                    ratio = r.amount / med if med else 99
                    cands.append(_cand(r.id, "unusually_high_transaction", r2(r.amount), r.date.date(),
                                       f"₹{r.amount:,.0f} is {ratio:.1f}x the typical {cat} transaction (median ₹{med:,.0f}).",
                                       _sev(ratio, 10, 5), round(min(0.99, 0.5 + z / 40), 2),
                                       "Confirm you recognise this transaction. If you do, mark it reviewed; if not, contact your bank.",
                                       f"high:{r.id}", category=cat, median=r2(med), z_score=round(float(z), 1)))
        # 2. unusually large withdrawals
        w = e[e.description.apply(lambda s: bool(_WITHDRAW.search(s)))]
        if len(w) >= 4:
            med = float(w.amount.median())
            for _, r in w[w.date >= pd.Timestamp(as_of - timedelta(days=180))].iterrows():
                if r.amount > 3 * med and r.amount >= 5000:
                    cands.append(_cand(r.id, "unusually_large_withdrawal", r2(r.amount), r.date.date(),
                                       f"Cash withdrawal of ₹{r.amount:,.0f} is {r.amount / med:.1f}x your typical withdrawal (₹{med:,.0f}).",
                                       "high" if r.amount > 6 * med else "medium", 0.8,
                                       "Verify this withdrawal was made by you.", f"withdraw:{r.id}", median=r2(med)))
        # 3. possible duplicates
        for (d, amt, mer), g in recent.groupby(["date", "amount", "merchant"]):
            if len(g) >= 2 and amt >= 50:
                later = g.iloc[-1]
                cands.append(_cand(later.id, "possible_duplicate", r2(amt), d.date(),
                                   f"{len(g)} identical charges of ₹{amt:,.0f} at {mer or 'same merchant'} on {d.date()}.", "medium", 0.7,
                                   "Check whether you were charged twice and request a refund if so.", f"dup:{later.id}", count=int(len(g))))
        # 4/5. category month analysis
        months = complete_months(df, as_of)
        cur = pd.Period(as_of, "M")
        ex = df[df.flow.isin(["expense", "refund"]) & ~df.merchant.isin(rec_keys)].copy()   # regular bills/fees are expected, not surges
        ex["m"] = ex.date.dt.to_period("M")
        pv = ex.pivot_table(index="m", columns="category", values="signed_expense", aggfunc="sum").fillna(0.0)
        mt = monthly_table(df, months) if months else None
        avg_exp = float(mt.expenses.mean()) if mt is not None and len(mt) else 0.0
        frac = as_of.day / 30.4375
        for target in ([months[-1]] if months else []) + ([cur] if len(months) >= 3 else []):
            prior_months = [m for m in months if m < target][-3:]
            if len(prior_months) < 3:
                continue
            for cat in pv.columns:
                prior = pv.reindex(prior_months)[cat].fillna(0.0)
                actual = float(pv.at[target, cat]) if target in pv.index else 0.0
                base = float(prior.mean())
                comp = actual / (frac if target == cur else 1.0)
                big = ex[(ex.m == target) & (ex.category == cat) & (ex.flow == "expense")].sort_values("amount", ascending=False)
                if big.empty:
                    continue
                tid, tamt, td = big.iloc[0].id, big.iloc[0].amount, big.iloc[0].date.date()
                if base > 0 and actual > 1.5 * base and actual - base > max(2000, 0.05 * avg_exp) and (target != cur or actual > 1.3 * base):
                    cands.append(_cand(tid, "sudden_spending_increase", r2(actual), td,
                                       f"{cat} spending is ₹{actual:,.0f} in {target}{' so far' if target == cur else ''}, {actual / base:.1f}x your 3-month average of ₹{base:,.0f}.",
                                       _sev(actual / base, 3, 2), 0.75, f"Review the {cat} transactions this month and consider adjusting your budget.",
                                       f"surge:{cat}:{target}", category=cat, month=str(target), baseline=r2(base)))
                elif base == 0 and float(prior.sum()) == 0 and actual >= 2000:
                    cands.append(_cand(tid, "unexpected_category_spending", r2(actual), td,
                                       f"First spending in {cat} in at least {len(prior_months)} months: ₹{actual:,.0f} in {target}.", "low", 0.6,
                                       "If this was planned, no action needed; otherwise review the transaction.", f"newcat:{cat}:{target}", category=cat, month=str(target)))
        # 8. cash-flow change
        if mt is not None and len(mt) >= 5:
            net = (mt.income - mt.expenses)
            last, prior = float(net.iloc[-1]), net.iloc[:-1]
            sd = float(prior.std(ddof=1))
            if sd > 0 and (last - float(prior.mean())) / sd < -2 and float(prior.mean()) - last > 5000:
                cands.append(_cand(None, "unexpected_cashflow_change", r2(last), months[-1].end_time.date(),
                                   f"Net savings in {months[-1]} (₹{last:,.0f}) were far below your usual ₹{prior.mean():,.0f}.", "high" if last < 0 else "medium", 0.7,
                                   "Compare income and large expenses for that month to find the cause.", f"cashflow:{months[-1]}", month=str(months[-1])))
        # 7. Isolation Forest
        if len(e) >= 60:
            try:
                from sklearn.ensemble import IsolationForest
                feat = e.copy()
                feat["cat_code"] = feat.category.astype("category").cat.codes
                feat["med"] = feat.groupby("category").amount.transform("median").replace(0, 1)
                X = np.column_stack([np.log1p(feat.amount), feat.date.dt.dayofweek, feat.date.dt.day, feat.cat_code, np.log1p(feat.amount / feat.med)])
                iso = IsolationForest(n_estimators=150, contamination=0.02, random_state=42).fit(X)
                dec = iso.decision_function(X)
                pred = iso.predict(X)
                rep = e.groupby(["merchant", "amount"]).size()
                for i in np.where(pred == -1)[0]:
                    r = feat.iloc[i]
                    if rep.get((r.merchant, r.amount), 0) >= 3:
                        continue
                    if r.date < pd.Timestamp(as_of - timedelta(days=180)) or r.amount < 1000 or r.merchant in rec_keys:
                        continue
                    conf = round(float(min(0.95, 0.5 + abs(dec[i]) * 2)), 2)
                    cands.append(_cand(r.id, "abnormal_spending_pattern", r2(r.amount), r.date.date(),
                                       f"Isolation Forest scored this ₹{r.amount:,.0f} {r.category} transaction as unusual compared with your overall spending pattern.",
                                       "medium" if conf > 0.7 else "low", conf, "Review this transaction; no action is needed if it was expected.",
                                       f"iforest:{r.id}", model="IsolationForest", score=round(float(dec[i]), 3)))
            except Exception:
                pass
    # 6. recurring payment changes
    for rp in db.scalars(select(RecurringPayment).where(RecurringPayment.user_id == user_id, RecurringPayment.status != "dismissed")):
        if rp.amount_change_pct is not None and abs(rp.amount_change_pct) >= 10 and rp.occurrences >= 3 and rp.last_date and (as_of - rp.last_date).days <= 60 \
                and rp.direction == "debit" and rp.last_transaction_id:
            cands.append(_cand(rp.last_transaction_id, "recurring_payment_change", r2(rp.last_amount), rp.last_date,
                               f"{rp.name} changed by {rp.amount_change_pct:+.1f}% versus its usual amount.",
                               "medium" if abs(rp.amount_change_pct) >= 25 else "low", 0.8, "Check for a price increase or a billing error.",
                               f"recchg:{rp.id}:{rp.last_date}", recurring_id=str(rp.id)))
    withdraw_tids = {c["transaction_id"] for c in cands if c["type"] == "unusually_large_withdrawal"}
    cands = [c for c in cands if not (c["type"] == "unusually_high_transaction" and c["transaction_id"] in withdraw_tids)]
    existing = {a.dedupe_key: a for a in db.scalars(select(Anomaly).where(Anomaly.user_id == user_id))}
    seen = set()
    for c in cands:
        if c["dedupe_key"] in seen:
            continue
        seen.add(c["dedupe_key"])
        if c["dedupe_key"] in existing:
            continue
        # skip IF flags for a txn already flagged by a rule
        if c["type"] == "abnormal_spending_pattern" and any(x["transaction_id"] == c["transaction_id"] and x["type"] != c["type"] for x in cands):
            continue
        import uuid as _u
        a = Anomaly(user_id=user_id, transaction_id=_u.UUID(str(c["transaction_id"])) if c["transaction_id"] else None, dedupe_key=c["dedupe_key"],
                    type=c["type"], amount=q2(c["amount"]) if c["amount"] is not None else None, date=c["date"], reason=c["reason"],
                    severity=c["severity"], confidence=c["confidence"], recommended_action=c["recommended_action"], details=c["details"])
        db.add(a)
    db.flush()
    return list(db.scalars(select(Anomaly).where(Anomaly.user_id == user_id).order_by(Anomaly.date.desc())))


def serialize(a: Anomaly) -> dict:
    return {"id": str(a.id), "transaction_id": str(a.transaction_id) if a.transaction_id else None, "type": a.type,
            "amount": r2(a.amount), "date": a.date.isoformat() if a.date else None, "reason": a.reason, "severity": a.severity,
            "confidence": a.confidence, "recommended_action": a.recommended_action, "status": a.status, "details": a.details,
            "is_confirmed_fraud": False, "note": DISCLAIMER}
