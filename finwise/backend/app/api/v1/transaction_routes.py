import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.core import ratelimit
from app.core.errors import NotFound
from app.models import ImportJob
from app.schemas.schemas import TransactionIn, TransactionOut, TransactionUpdate
from app.services import demo_data, transactions as tx
from app.services.twin import refresh_twin

router = APIRouter()


@router.post("/transactions", response_model=TransactionOut, status_code=201, tags=["Transactions"])
def create_transaction(body: TransactionIn, user: CurrentUser, db: DB, force: bool = False, refresh: bool = True):
    t = tx.create_manual(db, user, body.model_dump(), force)
    db.commit()
    if refresh:
        refresh_twin(db, user.id)
    return t


@router.get("/transactions", tags=["Transactions"])
def list_transactions(user: CurrentUser, db: DB, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                      date_from: date | None = None, date_to: date | None = None, category: str | None = None,
                      min_amount: Decimal | None = Query(None, ge=0), max_amount: Decimal | None = Query(None, ge=0),
                      direction: Literal["debit", "credit"] | None = None, account_id: uuid.UUID | None = None, q: str | None = Query(None, max_length=100),
                      is_internal_transfer: bool | None = None, needs_review: bool = False,
                      sort_by: Literal["date", "amount", "description", "category"] = "date", order: Literal["asc", "desc"] = "desc"):
    items, total = tx.query_transactions(db, user.id, page, page_size, sort_by, order, date_from=date_from, date_to=date_to, category=category,
                                         min_amount=min_amount, max_amount=max_amount, direction=direction, account_id=account_id, q=q,
                                         is_internal_transfer=is_internal_transfer, needs_review=needs_review)
    return {"items": [TransactionOut.model_validate(i) for i in items], "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size}


@router.get("/transactions/{transaction_id}", response_model=TransactionOut, tags=["Transactions"])
def get_transaction(transaction_id: uuid.UUID, user: CurrentUser, db: DB):
    return tx.get_transaction(db, user.id, transaction_id)


@router.put("/transactions/{transaction_id}", tags=["Transactions"])
def update_transaction(transaction_id: uuid.UUID, body: TransactionUpdate, user: CurrentUser, db: DB, apply_to_similar: bool = True):
    t = tx.get_transaction(db, user.id, transaction_id)
    t, similar = tx.update_transaction(db, user, t, body.model_dump(exclude_unset=True), apply_to_similar)
    db.commit()
    refresh_twin(db, user.id)
    return {"transaction": TransactionOut.model_validate(t), "similar_transactions_recategorized": similar}


@router.delete("/transactions/{transaction_id}", status_code=204, tags=["Transactions"])
def delete_transaction(transaction_id: uuid.UUID, user: CurrentUser, db: DB):
    t = tx.get_transaction(db, user.id, transaction_id)
    db.delete(t)
    db.commit()
    refresh_twin(db, user.id)


# ---- imports ----------------------------------------------------------------
def _job(j: ImportJob) -> dict:
    return {"job_id": str(j.id), "source_type": j.source_type, "filename": j.filename, "status": j.status, "total_rows": j.total_rows,
            "imported": j.imported, "duplicates_skipped": j.duplicates, "invalid_rows": j.invalid, "errors": j.errors, "warnings": j.warnings,
            "created_at": j.created_at.isoformat() if j.created_at else None, "completed_at": j.completed_at.isoformat() if j.completed_at else None}


async def _do_import(kind: str, file: UploadFile, account_id, user, db):
    content = await file.read(11 * 1024 * 1024)
    name = tx.validate_upload(kind, file.filename, content)
    job = tx.run_import(db, user, kind, name, content, account_id)
    refresh_twin(db, user.id)
    return _job(job)


@router.post("/import/pdf", status_code=201, tags=["Import"], dependencies=[Depends(ratelimit.sensitive_limit)])
async def import_pdf(user: CurrentUser, db: DB, file: UploadFile = File(...), account_id: uuid.UUID | None = Form(None)):
    return await _do_import("pdf", file, account_id, user, db)


@router.post("/import/csv", status_code=201, tags=["Import"], dependencies=[Depends(ratelimit.sensitive_limit)])
async def import_csv(user: CurrentUser, db: DB, file: UploadFile = File(...), account_id: uuid.UUID | None = Form(None)):
    return await _do_import("csv", file, account_id, user, db)


@router.post("/import/excel", status_code=201, tags=["Import"], dependencies=[Depends(ratelimit.sensitive_limit)])
async def import_excel(user: CurrentUser, db: DB, file: UploadFile = File(...), account_id: uuid.UUID | None = Form(None)):
    return await _do_import("excel", file, account_id, user, db)


@router.post("/import/demo", status_code=201, tags=["Import"], dependencies=[Depends(ratelimit.sensitive_limit)])
def import_demo(user: CurrentUser, db: DB, force: bool = False):
    """Load a synthetic Indian dataset (12 months, INR) for demos/testing. Refuses if you already have data unless force=true."""
    from app.core.errors import AppError
    from app.models import Transaction
    if not force and db.scalar(select(Transaction.id).where(Transaction.user_id == user.id).limit(1)):
        raise AppError(409, "data_exists", "You already have transactions. Use ?force=true to add demo data anyway.")
    res = demo_data.load_demo(db, user)
    refresh_twin(db, user.id)
    return {"message": "Synthetic demo data loaded (fictional).", **res}


@router.get("/import/{job_id}", tags=["Import"])
def get_import(job_id: uuid.UUID, user: CurrentUser, db: DB):
    j = db.scalar(select(ImportJob).where(ImportJob.id == job_id, ImportJob.user_id == user.id))
    if not j:
        raise NotFound("Import job")
    return _job(j)
