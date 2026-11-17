"""Seed synthetic (fictional) Indian demo data.

Usage:
  python -m scripts.seed                       # demo user demo@finwise.dev
  python -m scripts.seed --user-id <supabase-uuid> --email you@example.com   # attach demo data to a real Supabase user
  python -m scripts.seed --reset               # wipe that user's data first
"""
import argparse
import uuid

from app.core.config import get_settings
from app.core.database import Base, SessionLocal, engine
from app.core.security import make_dev_token
from app.models import User
from app.services import data_management
from app.services.categorization import ensure_categories
from app.services.demo_data import load_demo
from app.services.twin import refresh_twin

DEMO_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", default=str(DEMO_ID))
    ap.add_argument("--email", default="demo@finwise.dev")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--months", type=int, default=12)
    a = ap.parse_args()
    s = get_settings()
    if s.database_url.startswith("sqlite"):
        Base.metadata.create_all(engine)
    uid = uuid.UUID(a.user_id)
    with SessionLocal() as db:
        ensure_categories(db)
        user = db.get(User, uid)
        if user and a.reset:
            data_management.delete_user_data(db, user)
            user = None
        if user:
            print(f"User {a.email} already has data. Use --reset to recreate.")
            return
        user = User(id=uid, email=a.email, full_name="Demo User", currency="INR", preferences={}, settings={})
        db.add(user)
        db.commit()
        print("Seeded:", load_demo(db, user, months=a.months))
        refresh_twin(db, user.id)
    print(f"Demo user: {a.email} ({uid})")
    if s.allow_dev_auth and not s.is_production and s.supabase_jwt_secret:
        print("Dev bearer token (12h):", make_dev_token(uid, a.email))


if __name__ == "__main__":
    main()
