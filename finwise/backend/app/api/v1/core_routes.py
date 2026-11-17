"""Health, auth (Supabase integration), profile, accounts, categories."""
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.core import ratelimit
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.core.security import bearer, make_dev_token
from app.models import Account, Category, FinancialProfile
from app.schemas.schemas import AccountIn, AccountOut, AccountUpdate, Credentials, DevTokenIn, UserOut, UserUpdate
from app.utils.money import q2

log = logging.getLogger("finwise.auth")
router = APIRouter()


@router.get("/health", tags=["System"])
def health(db: DB):
    from sqlalchemy import text
    db.execute(text("SELECT 1"))
    s = get_settings()
    return {"status": "ok", "app": s.app_name, "environment": s.app_env, "database": "ok",
            "integrations": {"supabase_auth": bool(s.supabase_url and (s.supabase_jwt_secret or s.supabase_jwks_url)), "groq": bool(s.groq_api_key),
                             "ocr": bool(s.ocr_provider)}}


# ---- Supabase Auth integration -------------------------------------------------
def _supabase(method: str, path: str, json: dict | None = None, token: str | None = None) -> httpx.Response:
    s = get_settings()
    if not (s.supabase_url and s.supabase_anon_key):
        raise AppError(503, "auth_not_configured", "Supabase Auth is not configured (SUPABASE_URL / SUPABASE_ANON_KEY).")
    headers = {"apikey": s.supabase_anon_key}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        return httpx.request(method, f"{s.supabase_url}/auth/v1{path}", json=json, headers=headers, timeout=15)
    except httpx.HTTPError:
        raise AppError(502, "auth_provider_unavailable", "Authentication provider unreachable.")


def _relay(r: httpx.Response) -> dict:
    data = r.json() if r.content else {}
    if r.status_code >= 400:
        msg = data.get("msg") or data.get("error_description") or data.get("message") or "Authentication failed."
        raise AppError(400 if r.status_code < 500 else 502, "auth_failed", msg)
    return data


@router.post("/auth/register", tags=["Auth"], dependencies=[Depends(ratelimit.sensitive_limit)])
def register(body: Credentials):
    """Registers via Supabase Auth. Passwords are forwarded to Supabase and never stored/logged by this backend."""
    d = _relay(_supabase("POST", "/signup", {"email": body.email, "password": body.password, "data": {"full_name": body.full_name}}))
    return {"user_id": (d.get("user") or d).get("id"), "email": body.email, "access_token": d.get("access_token"), "refresh_token": d.get("refresh_token"),
            "message": "Registered. Confirm your email if your Supabase project requires it."}


@router.post("/auth/login", tags=["Auth"], dependencies=[Depends(ratelimit.sensitive_limit)])
def login(body: Credentials):
    d = _relay(_supabase("POST", "/token?grant_type=password", {"email": body.email, "password": body.password}))
    return {"access_token": d.get("access_token"), "refresh_token": d.get("refresh_token"), "expires_in": d.get("expires_in"), "token_type": "bearer"}


@router.post("/auth/logout", tags=["Auth"])
def logout(user: CurrentUser, creds=Depends(bearer)):
    """Revokes the session at Supabase. Access tokens remain valid until expiry (stateless JWT) - keep their TTL short."""
    _relay(_supabase("POST", "/logout", token=creds.credentials))
    return {"message": "Signed out."}


@router.post("/auth/dev-token", tags=["Auth"], dependencies=[Depends(ratelimit.sensitive_limit)])
def dev_token(body: DevTokenIn):
    """DEV/TEST ONLY: mint a token for local development without Supabase. Disabled unless ALLOW_DEV_AUTH=true and not production."""
    s = get_settings()
    if not s.allow_dev_auth or s.is_production:
        raise NotFound("Endpoint")
    if not s.supabase_jwt_secret:
        raise AppError(503, "auth_not_configured", "Set SUPABASE_JWT_SECRET (any random 32+ char value works for local dev).")
    uid = uuid.uuid5(uuid.NAMESPACE_DNS, body.email.lower())
    return {"access_token": make_dev_token(uid, body.email.lower()), "user_id": str(uid), "token_type": "bearer", "warning": "development token"}


# ---- profile -------------------------------------------------------------------
@router.get("/users/me", response_model=UserOut, tags=["Users"])
def me(user: CurrentUser):
    return user


@router.put("/users/me", response_model=UserOut, tags=["Users"])
def update_me(body: UserUpdate, user: CurrentUser, db: DB):
    data = body.model_dump(exclude_unset=True)
    if "opening_balance" in data:
        ob = data.pop("opening_balance")
        prof = db.get(FinancialProfile, user.id) or FinancialProfile(user_id=user.id)
        prof.opening_balance = q2(ob if ob is not None else 0)
        db.add(prof)
    for k, v in data.items():
        if v is not None:
            setattr(user, k, v)
    db.commit()
    return user


# ---- accounts / categories -----------------------------------------------------
@router.post("/accounts", response_model=AccountOut, status_code=201, tags=["Accounts"])
def create_account(body: AccountIn, user: CurrentUser, db: DB):
    a = Account(user_id=user.id, currency=user.currency, **body.model_dump())
    db.add(a)
    db.commit()
    return a


@router.get("/accounts", response_model=list[AccountOut], tags=["Accounts"])
def list_accounts(user: CurrentUser, db: DB):
    return db.scalars(select(Account).where(Account.user_id == user.id).order_by(Account.created_at)).all()


@router.put("/accounts/{account_id}", response_model=AccountOut, tags=["Accounts"])
def update_account(account_id: uuid.UUID, body: AccountUpdate, user: CurrentUser, db: DB):
    a = db.scalar(select(Account).where(Account.id == account_id, Account.user_id == user.id))
    if not a:
        raise NotFound("Account")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    db.commit()
    return a


@router.get("/categories", tags=["Transactions"])
def categories(db: DB, user: CurrentUser):
    return {"categories": [{"name": c.name, "kind": c.kind, "essential": c.is_essential, "fixed": c.is_fixed} for c in db.scalars(select(Category).order_by(Category.id))]}
