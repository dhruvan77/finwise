"""JWT validation (Supabase Auth: HS256 shared secret or asymmetric JWKS) + per-request user provisioning."""
from __future__ import annotations

import logging
import uuid
from functools import lru_cache

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import AppError
from app.models import User

log = logging.getLogger("finwise.auth")
bearer = HTTPBearer(auto_error=False, description="Supabase Auth access token (JWT)")


@lru_cache
def _jwks_client(url: str):
    return jwt.PyJWKClient(url)


def decode_token(token: str) -> dict:
    s = get_settings()
    try:
        alg = jwt.get_unverified_header(token).get("alg", "")
        options = {"require": ["exp", "sub"]}
        if alg == "HS256" and s.supabase_jwt_secret:
            return jwt.decode(token, s.supabase_jwt_secret, algorithms=["HS256"], audience=s.jwt_audience, options=options)
        if alg in ("RS256", "ES256") and s.supabase_jwks_url:
            key = _jwks_client(s.supabase_jwks_url).get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=[alg], audience=s.jwt_audience, options=options)
    except jwt.ExpiredSignatureError:
        raise AppError(401, "token_expired", "Access token has expired.")
    except jwt.PyJWTError:
        raise AppError(401, "invalid_token", "Invalid access token.")
    if not s.supabase_jwt_secret and not s.supabase_jwks_url:
        raise AppError(503, "auth_not_configured", "Authentication is not configured (set SUPABASE_JWT_SECRET or SUPABASE_JWKS_URL).")
    raise AppError(401, "invalid_token", "Unsupported token algorithm.")


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise AppError(401, "not_authenticated", "Missing bearer token.", None)
    claims = decode_token(creds.credentials)
    try:
        uid = uuid.UUID(str(claims["sub"]))
    except ValueError:
        raise AppError(401, "invalid_token", "Invalid subject claim.")
    user = db.get(User, uid)
    if user is None:
        email = claims.get("email") or f"{uid}@users.finwise.local"
        meta = claims.get("user_metadata") or {}
        user = User(id=uid, email=email, full_name=meta.get("full_name"), currency="INR", preferences={}, settings={})
        db.add(user)
        db.commit()
        log.info("Provisioned profile for new user")
    return user


def make_dev_token(user_id: uuid.UUID, email: str, minutes: int = 60 * 12) -> str:
    import datetime as dt
    s = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    payload = {"sub": str(user_id), "email": email, "aud": s.jwt_audience, "iat": now, "exp": now + dt.timedelta(minutes=minutes), "role": "authenticated"}
    return jwt.encode(payload, s.supabase_jwt_secret, algorithm="HS256")
