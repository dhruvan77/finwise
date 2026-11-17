"""Rule-based + ML + user-learned transaction categorization."""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, CategoryRule, Transaction
from app.utils.text import extract_merchant

# name: (kind, essential, fixed)
CATEGORY_META: dict[str, tuple[str, bool, bool]] = {
    "Food and dining": ("expense", False, False),
    "Groceries": ("expense", True, False),
    "Shopping": ("expense", False, False),
    "Transportation": ("expense", True, False),
    "Housing and rent": ("expense", True, True),
    "Education": ("expense", True, True),
    "Healthcare": ("expense", True, False),
    "Entertainment": ("expense", False, False),
    "Utilities": ("expense", True, False),
    "Subscriptions": ("expense", False, True),
    "Travel": ("expense", False, False),
    "Insurance": ("expense", True, True),
    "EMI and loan payments": ("expense", True, True),
    "Salary and income": ("income", False, False),
    "Investments": ("investment", False, False),
    "Transfers": ("transfer", False, False),
    "Other": ("expense", False, False),
}
CATEGORIES = list(CATEGORY_META)
ESSENTIAL = {c for c, m in CATEGORY_META.items() if m[1]}
FIXED = {c for c, m in CATEGORY_META.items() if m[2]}
INCOME_CATS = {"Salary and income", "Other"}  # credits in these count as income


def _rx(words: str) -> re.Pattern:
    return re.compile(words, re.I)


INCOME_RULES = [("Salary and income", _rx(r"\bSALARY\b|\bSAL\b|PAYROLL|STIPEND|INTEREST (PAID|CREDIT)|\bINT\.?PD|DIVIDEND|FREELANCE|CONSULTING|BONUS|REIMBURSEMENT|INVOICE"))]

EXPENSE_RULES = [
    ("Transfers", _rx(r"\bSELF\b|OWN ACC|FUND TRANSFER|SELF TRANSFER|CREDIT CARD (PAYMENT|BILL)|CC PAYMENT|CARD BILL|SAVINGS ACC|\bTRANSFER TO\b|\bTRF TO\b")),
    ("Investments", _rx(r"ZERODHA|GROWW|UPSTOX|MUTUAL FUND|\bSIP\b|NIPPON|\bMF\b|\bPPF\b|\bNPS\b|KUVERA|SMALLCASE|COIN BY|INDMONEY|\bELSS\b")),
    ("Insurance", _rx(r"INSURANCE|\bLIC\b|HDFC LIFE|SBI LIFE|ICICI PRU|STAR HEALTH|NIVA BUPA|LIC PREMIUM|ACKO|DIGIT INS")),
    ("EMI and loan payments", _rx(r"\bEMI\b|LOAN|BAJAJ FIN|HDFC LTD|FINANCE LTD|HOME CREDIT|\bNACH\b.*\bLN\b|CAR LOAN|HOME LOAN|KREDITBEE|PERSONAL LN")),
    ("Housing and rent", _rx(r"\bRENT\b|HOUSE RENT|LANDLORD|SOCIETY MAINT|MAINTENANCE|NOBROKER|HOUSING\.COM|\bPG\b|HOSTEL")),
    ("Utilities", _rx(r"ELECTRICITY|KSEB|BESCOM|TNEB|MSEB|TATA POWER|ADANI ELEC|WATER (BILL|BOARD)|GAS BILL|PIPED GAS|BROADBAND|AIRTEL|\bJIO\b|VODAFONE|BSNL|RECHARGE|FIBERNET|\bDTH\b|TATA SKY|INDANE|MAHANAGAR GAS")),
    ("Subscriptions", _rx(r"NETFLIX|SPOTIFY|PRIME VIDEO|AMAZON PRIME|HOTSTAR|DISNEY|YOUTUBE PREMIUM|SONYLIV|ZEE5|ICLOUD|ITUNES|APPLE COM|GOOGLE ONE|GOOGLE ?PLAY|MICROSOFT|ADOBE|CHATGPT|OPENAI|NOTION|GITHUB|AUDIBLE|LINKEDIN PREMIUM|SUBSCRIPTION|\bGYM\b|CULT FIT|CULTFIT")),
    ("Education", _rx(r"TUITION|COLLEGE|SCHOOL|UNIVERSITY|COURSERA|UDEMY|BYJU|\bFEES?\b|\bEXAM\b|COACHING|UNACADEMY|SEMESTER|\bMACE\b|LIBRARY|STATIONERY|BOOKSTORE")),
    ("Healthcare", _rx(r"PHARMACY|APOLLO|HOSPITAL|CLINIC|MEDPLUS|\b1MG\b|PHARMEASY|DIAGNOSTIC|\bLABS?\b|DOCTOR|NETMEDS|DENTAL|MEDICAL|AMRITA|ASTER|\bPATHOLOGY\b")),
    ("Groceries", _rx(r"BIGBASKET|BLINKIT|ZEPTO|DMART|D-MART|GROCER|SUPERMARKET|SUPERMKT|MORE RETAIL|SPENCER|NILGIRI|LULU|INSTAMART|JIOMART|FRESH ?TO ?HOME|MILK|VEGETABLE")),
    ("Food and dining", _rx(r"SWIGGY|ZOMATO|DOMINO|MCDONALD|\bKFC\b|PIZZA|CAFE|RESTAURANT|STARBUCKS|BAKERY|BIRYANI|BURGER|CHAI|HOTEL (SARAVANA|MALABAR)|EATERY|DINING|FOOD COURT|BARBEQUE|SUBWAY")),
    ("Transportation", _rx(r"\bUBER\b|\bOLA\b|RAPIDO|METRO|FUEL|PETROL|DIESEL|\bHPCL\b|\bBPCL\b|\bIOCL\b|INDIAN OIL|FASTAG|PARKING|KSRTC|BMTC|AUTO RICKSHAW|SHELL")),
    ("Travel", _rx(r"IRCTC|MAKEMYTRIP|GOIBIBO|CLEARTRIP|INDIGO|AIR INDIA|VISTARA|AKASA|\bOYO\b|AIRBNB|BOOKING|REDBUS|YATRA|\bHOTEL\b|RESORT")),
    ("Entertainment", _rx(r"BOOKMYSHOW|\bPVR\b|\bINOX\b|STEAM|PLAYSTATION|GAMING|CINEMA|MOVIE|DREAM11|CONCERT|\bBOWLING\b|ARCADE")),
    ("Shopping", _rx(r"AMAZON|FLIPKART|MYNTRA|AJIO|MEESHO|NYKAA|RELIANCE DIGITAL|CROMA|DECATHLON|IKEA|LIFESTYLE|TRENDS|\bMALL\b|ONLINE SHOPPING|APPLE STORE|VIJAY SALES|SHOPPERS STOP|WESTSIDE|ELECTRONICS|\bH&M\b|ZARA")),
    ("Other", _rx(r"\bATM\b|CASH WITHDRAWAL|\bWDL\b")),
]
_TRANSFER_SELF_RX = re.compile(r"\bSELF\b|OWN ACC|SELF TRANSFER|SAVINGS ACC", re.I)


@dataclass
class CatResult:
    category: str
    confidence: float
    source: str
    merchant: str


def is_self_transfer_text(description: str) -> bool:
    return bool(_TRANSFER_SELF_RX.search(description or ""))


def categorize(description: str, direction: str, user_rules: dict[str, str] | None = None, ml=None) -> CatResult:
    merchant = extract_merchant(description)
    if user_rules and merchant in user_rules:
        return CatResult(user_rules[merchant], 0.98, "user_rule", merchant)
    text = (description or "").upper()
    if direction == "credit":
        for cat, rx in INCOME_RULES:
            if rx.search(text):
                return CatResult(cat, 0.92, "rule", merchant)
    for cat, rx in EXPENSE_RULES:
        if rx.search(text):
            conf = 0.8 if cat == "Other" else 0.9
            return CatResult(cat, conf, "rule", merchant)
    if ml is not None:
        pred = ml.predict(text)
        if pred and pred[1] >= 0.45:
            return CatResult(pred[0], round(min(pred[1], 0.85), 2), "ml", merchant)
    if direction == "credit":
        return CatResult("Other", 0.4, "default", merchant)
    return CatResult("Other", 0.3, "default", merchant)


class MLCategorizer:
    """TF-IDF(char n-grams) + LogisticRegression trained on the user's own labelled transactions."""

    def __init__(self, texts: list[str], labels: list[str]):
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        self.pipe = make_pipeline(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), lowercase=True),
                                  LogisticRegression(max_iter=300, C=5.0))
        self.pipe.fit(texts, labels)

    def predict(self, text: str):
        proba = self.pipe.predict_proba([text])[0]
        i = int(proba.argmax())
        return self.pipe.classes_[i], float(proba[i])


_ml_cache: dict = {}


def get_ml(db: Session, user_id) -> MLCategorizer | None:
    rows = db.execute(select(Transaction.description, Transaction.category).where(
        Transaction.user_id == user_id, Transaction.category != "Other",
        Transaction.category_source.in_(["user", "user_rule", "rule"]))).all()
    labels = [r[1] for r in rows]
    if len(rows) < 30 or len(set(labels)) < 3:
        return None
    key = (str(user_id), len(rows), hash(tuple(sorted(set(labels)))))
    if key in _ml_cache:
        return _ml_cache[key]
    from collections import Counter
    counts = Counter(labels)
    keep = [(r[0].upper(), r[1]) for r in rows if counts[r[1]] >= 2]
    if len({k[1] for k in keep}) < 3:
        return None
    model = MLCategorizer([k[0] for k in keep], [k[1] for k in keep])
    _ml_cache.clear()
    _ml_cache[key] = model
    return model


def load_user_rules(db: Session, user_id) -> dict[str, str]:
    return {r.merchant_key: r.category for r in db.scalars(select(CategoryRule).where(CategoryRule.user_id == user_id))}


def learn_from_correction(db: Session, user_id, merchant_key: str, category: str, apply_to_similar: bool = True) -> int:
    rule = db.scalar(select(CategoryRule).where(CategoryRule.user_id == user_id, CategoryRule.merchant_key == merchant_key))
    if rule:
        rule.category, rule.hits = category, rule.hits + 1
    else:
        db.add(CategoryRule(user_id=user_id, merchant_key=merchant_key, category=category))
    updated = 0
    if apply_to_similar:
        for t in db.scalars(select(Transaction).where(Transaction.user_id == user_id, Transaction.merchant == merchant_key,
                                                      Transaction.category_source != "user")):
            t.category, t.category_confidence, t.category_source = category, 0.98, "user_rule"
            updated += 1
    return updated


def ensure_categories(db: Session) -> None:
    existing = {c.name for c in db.scalars(select(Category))}
    for name, (kind, ess, fixed) in CATEGORY_META.items():
        if name not in existing:
            db.add(Category(name=name, kind=kind, is_essential=ess, is_fixed=fixed))
    db.commit()
