"""Simple in-memory sliding-window rate limiter (single-process).
For multi-instance deployments swap `_hits` for Redis; the dependency interface stays the same."""
import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import AppError

_hits: dict[str, deque] = defaultdict(deque)


def reset() -> None:
    _hits.clear()


def _check(key: str, limit: int, window: float = 60.0) -> None:
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise AppError(429, "rate_limited", "Too many requests. Please slow down.")
    q.append(now)


def _ident(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[-24:] if auth else (request.client.host if request.client else "anon")


def default_limit(request: Request) -> None:
    _check(f"d:{_ident(request)}", get_settings().rate_limit_default_per_min)


def sensitive_limit(request: Request) -> None:
    _check(f"s:{request.url.path}:{_ident(request)}", get_settings().rate_limit_sensitive_per_min)
