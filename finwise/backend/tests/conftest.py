import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db", "APP_ENV": "test", "ALLOW_DEV_AUTH": "true",
    "SUPABASE_JWT_SECRET": "test-secret-key-that-is-at-least-32-bytes-long!", "GROQ_API_KEY": "",
    "RATE_LIMIT_SENSITIVE_PER_MIN": "1000", "RATE_LIMIT_DEFAULT_PER_MIN": "100000",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import ratelimit  # noqa: E402
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.categorization import ensure_categories  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        ensure_categories(db)
    ratelimit.reset()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _headers(client, email):
    r = client.post("/api/v1/auth/dev-token", json={"email": email})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def auth(client):
    return _headers(client, "alice@example.com")


@pytest.fixture
def auth_b(client):
    return _headers(client, "bob@example.com")


@pytest.fixture
def demo(client, auth):
    r = client.post("/api/v1/import/demo", headers=auth)
    assert r.status_code == 201, r.text
    return auth


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s
