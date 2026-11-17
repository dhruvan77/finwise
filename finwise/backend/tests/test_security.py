from app.core.config import get_settings

H = "/api/v1"


def test_sensitive_rate_limit(client, auth):
    s = get_settings()
    old = s.rate_limit_sensitive_per_min
    s.rate_limit_sensitive_per_min = 3
    try:
        codes = [client.get(f"{H}/data/export", headers=auth).status_code for _ in range(5)]
    finally:
        s.rate_limit_sensitive_per_min = old
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


def test_errors_do_not_leak_internals(client, auth):
    r = client.get(f"{H}/transactions/not-a-uuid", headers=auth)
    assert r.status_code == 422 and "Traceback" not in r.text
    r = client.get(f"{H}/nonexistent", headers=auth)
    assert r.status_code == 404 and r.json()["error"]["code"] == "http_error"


def test_dev_token_disabled_in_production(client):
    s = get_settings()
    s.allow_dev_auth = False
    try:
        assert client.post(f"{H}/auth/dev-token", json={"email": "a@b.co"}).status_code == 404
    finally:
        s.allow_dev_auth = True


def test_security_headers_and_cors(client):
    r = client.get(f"{H}/health", headers={"Origin": "http://localhost:3000"})
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["access-control-allow-origin"] == "http://localhost:3000"
    r = client.get(f"{H}/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers
