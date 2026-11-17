import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import core_routes, decision_routes, finance_routes, transaction_routes
from app.core import ratelimit
from app.core.config import get_settings
from app.core.database import Base, SessionLocal, engine
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.services.categorization import ensure_categories

DESCRIPTION = """
**FinWise AI** - AI-powered personal financial decision intelligence (backend).
Don't just track your money. Understand it. Predict it. Make smarter decisions with it.

> FinWise AI provides informational decision support, not guaranteed financial outcomes or regulated professional financial advice.
> Simulations, stress tests and predictions are hypothetical and based on your recorded history.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level)
    if s.database_url.startswith("sqlite"):
        Base.metadata.create_all(engine)           # convenience for local/test only; use Alembic for Postgres
    with SessionLocal() as db:
        try:
            ensure_categories(db)
        except Exception:  # noqa: BLE001 - tables may not exist until migrations are run
            logging.getLogger("finwise").warning("Category seed skipped: run `alembic upgrade head` first.")
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version="1.0.0", description=DESCRIPTION, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])
    register_error_handlers(app)

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        t0 = time.perf_counter()
        resp = await call_next(request)
        logging.getLogger("finwise.access").info("%s %s %s %.0fms", request.method, request.url.path, resp.status_code, (time.perf_counter() - t0) * 1000)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    for r in (core_routes.router, transaction_routes.router, finance_routes.router, decision_routes.router):
        app.include_router(r, prefix="/api/v1", dependencies=[Depends(ratelimit.default_limit)])
    return app


app = create_app()
