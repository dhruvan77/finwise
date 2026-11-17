"""Synthetic Indian financial dataset (INR). Entirely fictional; used by the seed script, the demo import endpoint and tests."""
from __future__ import annotations

import random
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Account, Budget, FinancialProfile, Goal, Transaction, User
from app.services import categorization as cat
from app.services.transactions import build_transaction, detect_internal_transfers
from app.utils.money import q2

MAIN, SAVE = "HDFC Savings (demo)", "Emergency Savings (demo)"


def _ref(r: random.Random) -> str:
    return "".join(r.choice("0123456789") for _ in range(12))


def generate_rows(end: date, months: int = 12, seed: int = 7) -> list[tuple]:
    """Returns (date, description, amount, direction, account_key)."""
    r = random.Random(seed)
    rows: list[tuple] = []
    start_month = date(end.year, end.month, 1)
    for _ in range(months):
        start_month = (start_month - timedelta(days=1)).replace(day=1)
    m = start_month
    mi = 0
    while m <= end:
        mi += 1
        dim = monthrange(m.year, m.month)[1]

        def add(day, desc, amt, direction="debit", acct="main"):
            d = date(m.year, m.month, min(max(day, 1), dim))
            if d <= end:
                rows.append((d, desc, round(float(amt), 2), direction, acct))

        add(1, "SALARY ACME TECHNOLOGIES PVT LTD", 68000, "credit")
        if mi in (3, 8):
            add(20, "NEFT FREELANCE INVOICE DESIGNCO", 8000, "credit")
        add(2, "SELF TRANSFER TO EMERGENCY SAVINGS", 6000)
        add(2, "SELF TRANSFER FROM HDFC SAVINGS", 6000, "credit", "save")
        add(3, "GROWW MUTUAL FUND SIP", 5000)
        add(5, "UPI/HOUSE RENT/LANDLORD", 15000)
        add(8, "NETFLIX.COM", 649)
        add(10, "BAJAJ FIN EMI", 4200)
        add(12, "SPOTIFY INDIA", 119)
        if m.month in (1, 4, 7, 10):
            add(12, "COLLEGE TUITION FEE", 15000)
        add(15, "KSEB ELECTRICITY BILL", r.randint(1100, 2400))
        add(18, "ACT FIBERNET BROADBAND", 1049)
        add(20, "AIRTEL RECHARGE", 399)
        add(22, "YOUTUBE PREMIUM", 149 if (end - date(m.year, m.month, min(22, dim))).days < 65 else 129)
        add(25, "LIC PREMIUM", 2500)
        for _ in range(8):
            add(r.randint(1, 28), f"UPI/DR/{_ref(r)}/{r.choice(['BIGBASKET', 'DMART', 'BLINKIT', 'ZEPTO'])}/okaxis/Payment", r.randint(300, 1800))
        for _ in range(11):
            add(r.randint(1, 28), f"UPI/DR/{_ref(r)}/{r.choice(['SWIGGY', 'ZOMATO', 'STARBUCKS COFFEE', 'DOMINOS PIZZA'])}/ybl/Payment", r.randint(150, 650))
        for _ in range(10):
            add(r.randint(1, 28), f"UPI/DR/{_ref(r)}/{r.choice(['UBER INDIA', 'OLA CABS', 'RAPIDO'])}/paytm/Payment", r.randint(80, 350))
        add(r.randint(5, 25), "HPCL FUEL STATION", 1500)
        for _ in range(3):
            add(r.randint(1, 28), f"{r.choice(['AMAZON PAY INDIA', 'FLIPKART', 'MYNTRA'])} ORDER {_ref(r)}", r.randint(400, 3500))
        add(r.randint(1, 28), "PVR CINEMAS BOOKMYSHOW", r.randint(400, 900))
        add(r.randint(1, 28), "APOLLO PHARMACY", r.randint(200, 1200))
        if mi == 5:
            add(14, "AMRITA HOSPITAL KOCHI", 6500)
        if mi % 3 == 0:
            add(r.randint(1, 28), "IRCTC TRAIN TICKET", 4500)
        for _ in range(r.randint(1, 2)):
            add(r.randint(1, 28), "ATM WDL KOCHI", r.choice([2000, 2500, 3000]))
        nxt = (m + timedelta(days=32)).replace(day=1)
        m = nxt
    # injected, clearly-reviewable events in the last ~2 weeks
    rows += [(end - timedelta(days=12), f"ONLINE SHOPPING UNKNOWNSTORE {_ref(r)}", 14500.0, "debit", "main"),
             (end - timedelta(days=5), "UPI/DR/111122223333/SWIGGY/ybl/Payment", 480.0, "debit", "main"),
             (end - timedelta(days=5), "UPI/DR/111122223333/SWIGGY/ybl/Payment ", 480.0, "debit", "main"),
             (end - timedelta(days=8), "ATM WDL KOCHI HIGH VALUE", 25000.0, "debit", "main")]
    return sorted(rows, key=lambda x: x[0])


def load_demo(db: Session, user: User, end: date | None = None, months: int = 12, seed: int = 7) -> dict:
    end = end or date.today()
    main = Account(user_id=user.id, name=MAIN, type="savings", institution="Demo Bank")
    save = Account(user_id=user.id, name=SAVE, type="savings", institution="Demo Bank")
    db.add_all([main, save])
    db.flush()
    rows = generate_rows(end, months, seed)
    opening = 120000.0
    bal = {"main": opening, "save": 20000.0}
    accts = {"main": main, "save": save}
    rules = cat.load_user_rules(db, user.id)
    occ: dict = {}
    txns = []
    for d, desc, amt, direction, key in rows:
        bal[key] += amt if direction == "credit" else -amt
        k = (key, d, amt, direction, desc.strip())
        occ[k] = occ.get(k, 0) + 1
        t = build_transaction(db, user, accts[key], d, desc, Decimal(str(amt)), direction, Decimal(str(round(bal[key], 2))), "demo",
                              rules, None, occurrence=occ[k] - 1)
        txns.append(t)
    db.add_all(txns)
    main.current_balance, main.balance_as_of = q2(bal["main"]), end
    save.current_balance, save.balance_as_of = q2(bal["save"]), end
    db.flush()
    detect_internal_transfers(db, user.id)
    for c, lim in [(None, 58000), ("Food and dining", 6000), ("Shopping", 5000), ("Groceries", 8000), ("Entertainment", 1500)]:
        db.add(Budget(user_id=user.id, category=c, limit_amount=Decimal(lim), alert_threshold=0.8))
    db.add(Goal(user_id=user.id, name="Emergency fund", target_amount=Decimal(200000), current_amount=Decimal(26000),
                target_date=end + timedelta(days=365), monthly_contribution=Decimal(6000)))
    db.add(Goal(user_id=user.id, name="New laptop", target_amount=Decimal(35000), current_amount=Decimal(12000),
                target_date=end + timedelta(days=180), monthly_contribution=Decimal(4000)))
    if not db.get(FinancialProfile, user.id):
        db.add(FinancialProfile(user_id=user.id, opening_balance=0))
    db.commit()
    return {"accounts": 2, "transactions": len(txns), "budgets": 5, "goals": 2, "period_end": end.isoformat()}
