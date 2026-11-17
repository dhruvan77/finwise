"""Transaction persistence, dedupe, import pipeline and internal-transfer detection."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.models import Account, ImportJob, Transaction, User
from app.services import categorization as cat
from app.services.parsers import ParseResult, RawTxn, get_parser
from app.utils.money import q2
from app.utils.text import clean_description, extract_merchant


def fingerprint(account_id, d: date, amount: Decimal, direction: str, desc_: str, balance: Decimal | None, occurrence: int = 0) -> str:
    norm = re.sub(r"[^A-Z0-9]", "", desc_.upper())
    raw = f"{account_id}|{d.isoformat()}|{q2(amount)}|{direction}|{norm}|{q2(balance) if balance is not None else ''}|{occurrence}"
    return hashlib.sha256(raw.encode()).hexdigest()


def get_or_create_default_account(db: Session, user_id, name: str = "Primary account") -> Account:
    acc = db.scalar(select(Account).where(Account.user_id == user_id).order_by(Account.created_at))
    if acc:
        return acc
    acc = Account(user_id=user_id, name=name, type="savings")
    db.add(acc)
    db.flush()
    return acc


def get_account(db: Session, user_id, account_id) -> Account:
    acc = db.scalar(select(Account).where(Account.id == account_id, Account.user_id == user_id))
    if not acc:
        raise NotFound("Account")
    return acc


def get_transaction(db: Session, user_id, txn_id) -> Transaction:
    t = db.scalar(select(Transaction).where(Transaction.id == txn_id, Transaction.user_id == user_id))
    if not t:
        raise NotFound("Transaction")
    return t


def build_transaction(db: Session, user: User, account: Account, d: date, description: str, amount: Decimal, direction: str,
                      balance: Decimal | None, source: str, rules, ml, job_id=None, occurrence: int = 0,
                      category: str | None = None, notes: str | None = None) -> Transaction:
    desc_ = clean_description(description)
    res = cat.categorize(desc_, direction, rules, ml)
    t = Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=account.id, import_job_id=job_id, date=d, description=desc_,
        merchant=res.merchant, amount=q2(amount), direction=direction, currency=account.currency or user.currency,
        category=res.category, category_confidence=res.confidence, category_source=res.source,
        balance_after=q2(balance) if balance is not None else None, notes=notes, source=source,
        fingerprint=fingerprint(account.id, d, amount, direction, desc_, balance, occurrence),
    )
    if category:
        t.category, t.category_confidence, t.category_source = category, 1.0, "user"
    if t.category == "Transfers" and cat.is_self_transfer_text(desc_):
        t.is_internal_transfer = True
    return t


def create_manual(db: Session, user: User, data: dict, force: bool = False) -> Transaction:
    account = get_account(db, user.id, data["account_id"]) if data.get("account_id") else get_or_create_default_account(db, user.id)
    rules = cat.load_user_rules(db, user.id)
    t = build_transaction(db, user, account, data["date"], data["description"], data["amount"], data["direction"],
                          data.get("balance_after"), "manual", rules, cat.get_ml(db, user.id),
                          category=data.get("category"), notes=data.get("notes"))
    if not force and db.scalar(select(Transaction.id).where(Transaction.user_id == user.id, Transaction.fingerprint == t.fingerprint)):
        raise AppError(409, "possible_duplicate", "An identical transaction already exists. Resubmit with ?force=true to add it anyway.")
    db.add(t)
    db.flush()
    detect_internal_transfers(db, user.id)
    return t


def update_transaction(db: Session, user: User, t: Transaction, changes: dict, apply_to_similar: bool = True) -> tuple[Transaction, int]:
    similar = 0
    new_cat = changes.pop("category", None)
    for k, v in changes.items():
        if v is not None or k in ("notes",):
            setattr(t, k, v if k != "amount" else q2(v))
    if "description" in changes and changes["description"]:
        t.description = clean_description(t.description)
        t.merchant = extract_merchant(t.description)
    if new_cat and new_cat != t.category:
        if new_cat not in cat.CATEGORY_META:
            raise AppError(422, "invalid_category", f"Unknown category '{new_cat}'. Valid: {', '.join(cat.CATEGORIES)}")
        t.category, t.category_confidence, t.category_source = new_cat, 1.0, "user"
        db.flush()
        if t.merchant:
            similar = cat.learn_from_correction(db, user.id, t.merchant, new_cat, apply_to_similar)
            t.category_source = "user"
            t.category_confidence = 1.0
    t.fingerprint = fingerprint(t.account_id, t.date, t.amount, t.direction, t.description, t.balance_after)
    db.flush()
    return t, similar


_FILTERS = ("date_from", "date_to", "category", "min_amount", "max_amount", "direction", "account_id", "q", "is_internal_transfer", "needs_review")


def query_transactions(db: Session, user_id, page=1, page_size=50, sort_by="date", order="desc", **f):
    conds = [Transaction.user_id == user_id]
    if f.get("date_from"):
        conds.append(Transaction.date >= f["date_from"])
    if f.get("date_to"):
        conds.append(Transaction.date <= f["date_to"])
    if f.get("category"):
        conds.append(Transaction.category == f["category"])
    if f.get("min_amount") is not None:
        conds.append(Transaction.amount >= f["min_amount"])
    if f.get("max_amount") is not None:
        conds.append(Transaction.amount <= f["max_amount"])
    if f.get("direction"):
        conds.append(Transaction.direction == f["direction"])
    if f.get("account_id"):
        conds.append(Transaction.account_id == f["account_id"])
    if f.get("q"):
        like = f"%{f['q'].lower()}%"
        conds.append(or_(func.lower(Transaction.description).like(like), func.lower(Transaction.merchant).like(like)))
    if f.get("is_internal_transfer") is not None:
        conds.append(Transaction.is_internal_transfer == f["is_internal_transfer"])
    if f.get("needs_review"):
        conds.append(Transaction.category_confidence < 0.6)
    col = {"date": Transaction.date, "amount": Transaction.amount, "description": Transaction.description,
           "category": Transaction.category}.get(sort_by, Transaction.date)
    ordering = [asc(col) if order == "asc" else desc(col), desc(Transaction.created_at)]
    total = db.scalar(select(func.count()).select_from(Transaction).where(and_(*conds))) or 0
    items = db.scalars(select(Transaction).where(and_(*conds)).order_by(*ordering)
                       .offset((page - 1) * page_size).limit(page_size)).all()
    return items, total


def detect_internal_transfers(db: Session, user_id) -> int:
    """Mark matching debit/credit pairs across a user's accounts (same amount, <=2 days apart) as internal transfers."""
    n_acc = db.scalar(select(func.count()).select_from(Account).where(Account.user_id == user_id)) or 0
    if n_acc < 2:
        return 0
    cands = db.scalars(select(Transaction).where(Transaction.user_id == user_id, Transaction.is_internal_transfer == False,  # noqa: E712
                                                 Transaction.category.in_(["Transfers", "Other", "Investments"]))).all()
    debits = [t for t in cands if t.direction == "debit"]
    credits = [t for t in cands if t.direction == "credit"]
    used: set = set()
    marked = 0
    for dtx in debits:
        for ctx in credits:
            if ctx.id in used or ctx.account_id == dtx.account_id or ctx.amount != dtx.amount:
                continue
            if abs((ctx.date - dtx.date).days) <= 2:
                for t in (dtx, ctx):
                    t.is_internal_transfer, t.category, t.category_confidence = True, "Transfers", max(t.category_confidence, 0.9)
                used.add(ctx.id)
                marked += 2
                break
    return marked


def run_import(db: Session, user: User, source_type: str, filename: str | None, content: bytes, account_id=None) -> ImportJob:
    job = ImportJob(user_id=user.id, source_type=source_type, filename=(filename or "")[:255], status="processing")
    db.add(job)
    db.flush()
    try:
        result: ParseResult = get_parser(source_type).parse(content)
    except AppError as e:
        job.status, job.errors, job.completed_at = "failed", [{"error": e.message}], datetime.now(timezone.utc)
        db.commit()
        raise
    account = get_account(db, user.id, account_id) if account_id else get_or_create_default_account(db, user.id)
    rules, ml = cat.load_user_rules(db, user.id), cat.get_ml(db, user.id)
    seen: dict[tuple, int] = {}
    existing = set(db.scalars(select(Transaction.fingerprint).where(Transaction.user_id == user.id)))
    new_txns, dups = [], 0
    for raw in result.txns:
        key = (raw.date, raw.amount, raw.direction, re.sub(r"[^A-Z0-9]", "", raw.description.upper()), raw.balance)
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        t = build_transaction(db, user, account, raw.date, raw.description, raw.amount, raw.direction, raw.balance,
                              source_type, rules, ml, job_id=job.id, occurrence=occ)
        if t.fingerprint in existing:
            dups += 1
            continue
        existing.add(t.fingerprint)
        new_txns.append(t)
    db.add_all(new_txns)
    db.flush()
    with_bal = [t for t in new_txns if t.balance_after is not None]
    if with_bal:
        latest = max(with_bal, key=lambda t: (t.date, t.created_at or datetime.min.replace(tzinfo=timezone.utc)))
        if account.balance_as_of is None or latest.date >= account.balance_as_of:
            account.current_balance, account.balance_as_of = latest.balance_after, latest.date
    detect_internal_transfers(db, user.id)
    job.total_rows, job.imported, job.duplicates, job.invalid = result.total_rows, len(new_txns), dups, len(result.errors)
    job.errors, job.warnings = result.errors[:50], result.warnings
    job.status, job.completed_at = "completed", datetime.now(timezone.utc)
    db.commit()
    return job


# ---------- upload validation ----------
_EXT = {"pdf": {".pdf"}, "csv": {".csv", ".txt"}, "excel": {".xlsx", ".xlsm"}}


def validate_upload(source_type: str, filename: str | None, content: bytes) -> str:
    s = get_settings()
    if not content:
        raise AppError(400, "empty_file", "Uploaded file is empty.")
    if len(content) > s.max_upload_mb * 1024 * 1024:
        raise AppError(413, "file_too_large", f"File exceeds {s.max_upload_mb} MB limit.")
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", (filename or "upload").split("/")[-1].split("\\")[-1])[:120]
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in _EXT[source_type]:
        raise AppError(415, "unsupported_file_type", f"Expected a {'/'.join(sorted(_EXT[source_type]))} file for {source_type} import.")
    if source_type == "pdf" and not content.lstrip()[:5].startswith(b"%PDF"):
        raise AppError(415, "invalid_file", "File content is not a valid PDF.")
    if source_type == "excel" and not content.startswith(b"PK"):
        raise AppError(415, "invalid_file", "File content is not a valid .xlsx workbook.")
    if source_type == "csv" and b"\x00" in content[:4096]:
        raise AppError(415, "invalid_file", "File content does not look like text/CSV.")
    return name
