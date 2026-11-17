import io
import json
from datetime import date, timedelta

import pytest

from app.services import categorization as cat
from app.services.forecasting import forecast_series
from app.services.simulation import emi_amount, opportunity_cost
import pandas as pd

H = "/api/v1"


def txn(client, auth, **kw):
    body = {"date": (date.today() - timedelta(days=kw.pop("ago", 1))).isoformat(), "description": "Test", "amount": 100, "direction": "debit"}
    body.update(kw)
    return client.post(f"{H}/transactions?refresh=false&force=true", json=body, headers=auth)


# ---------------- health / auth ----------------
def test_health(client):
    r = client.get(f"{H}/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["database"] == "ok"


@pytest.mark.parametrize("path", ["/users/me", "/transactions", "/analytics/summary", "/financial-twin", "/goals", "/data/export", "/ai/conversations"])
def test_unauthorized(client, path):
    r = client.get(H + path)
    assert r.status_code == 401 and r.json()["error"]["code"] == "not_authenticated"


def test_invalid_and_expired_token(client):
    assert client.get(f"{H}/users/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    import jwt, time
    tok = jwt.encode({"sub": "00000000-0000-4000-8000-000000000009", "aud": "authenticated", "exp": int(time.time()) - 10},
                     "test-secret-key-that-is-at-least-32-bytes-long!", algorithm="HS256")
    r = client.get(f"{H}/users/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "token_expired"


def test_profile_update(client, auth):
    assert client.get(f"{H}/users/me", headers=auth).json()["email"] == "alice@example.com"
    r = client.put(f"{H}/users/me", json={"full_name": "Alice", "preferences": {"theme": "dark"}, "opening_balance": 5000}, headers=auth)
    assert r.status_code == 200 and r.json()["full_name"] == "Alice" and r.json()["preferences"] == {"theme": "dark"}
    assert client.put(f"{H}/users/me", json={"currency": "inr"}, headers=auth).status_code == 422


def test_supabase_proxy_unconfigured_and_mocked(client, monkeypatch, auth):
    r = client.post(f"{H}/auth/login", json={"email": "a@b.co", "password": "password123"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "auth_not_configured"
    import httpx
    from app.api.v1 import core_routes
    monkeypatch.setattr(core_routes, "_supabase", lambda *a, **k: httpx.Response(200, json={"access_token": "tok", "refresh_token": "r", "expires_in": 3600}))
    r = client.post(f"{H}/auth/login", json={"email": "a@b.co", "password": "password123"})
    assert r.status_code == 200 and r.json()["access_token"] == "tok"
    assert client.post(f"{H}/auth/logout", headers=auth).status_code == 200


# ---------------- transactions ----------------
def test_transaction_crud_and_validation(client, auth):
    r = txn(client, auth, description="UPI/DR/123456789012/SWIGGY/okaxis/Payment", amount=350.5)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["category"] == "Food and dining" and t["merchant"] == "SWIGGY" and float(t["amount"]) == 350.5
    assert client.get(f"{H}/transactions/{t['id']}", headers=auth).json()["id"] == t["id"]
    assert client.get(f"{H}/transactions", headers=auth).json()["total"] == 1
    r = client.put(f"{H}/transactions/{t['id']}", json={"notes": "dinner"}, headers=auth)
    assert r.status_code == 200 and r.json()["transaction"]["notes"] == "dinner"
    assert client.delete(f"{H}/transactions/{t['id']}", headers=auth).status_code == 204
    assert client.get(f"{H}/transactions/{t['id']}", headers=auth).status_code == 404


@pytest.mark.parametrize("bad", [{"amount": -5}, {"amount": 0}, {"direction": "sideways"}, {"description": ""}, {"date": "not-a-date"}, {"category": "Nope"}, {"amount": "abc"}])
def test_invalid_transaction_input(client, auth, bad):
    r = txn(client, auth, **bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_duplicate_manual_transaction_conflict(client, auth):
    body = {"date": "2026-01-05", "description": "Cafe", "amount": 120, "direction": "debit"}
    assert client.post(f"{H}/transactions?refresh=false", json=body, headers=auth).status_code == 201
    r = client.post(f"{H}/transactions?refresh=false", json=body, headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "possible_duplicate"


def test_pagination_filter_sort(client, auth):
    for i, amt in enumerate([100, 500, 250, 900, 50]):
        txn(client, auth, description=f"Shop {i}", amount=amt, ago=i + 1)
    r = client.get(f"{H}/transactions?page=1&page_size=2&sort_by=amount&order=desc", headers=auth).json()
    assert r["total"] == 5 and r["pages"] == 3 and [float(x["amount"]) for x in r["items"]] == [900, 500]
    r = client.get(f"{H}/transactions?min_amount=100&max_amount=500", headers=auth).json()
    assert r["total"] == 3
    assert client.get(f"{H}/transactions?q=shop 3", headers=auth).json()["total"] == 1
    assert client.get(f"{H}/transactions?page_size=999", headers=auth).status_code == 422


def test_user_data_isolation(client, auth, auth_b):
    t = txn(client, auth, description="Private", amount=777).json()
    assert client.get(f"{H}/transactions/{t['id']}", headers=auth_b).status_code == 404
    assert client.put(f"{H}/transactions/{t['id']}", json={"notes": "x"}, headers=auth_b).status_code == 404
    assert client.delete(f"{H}/transactions/{t['id']}", headers=auth_b).status_code == 404
    assert client.get(f"{H}/transactions", headers=auth_b).json()["total"] == 0
    g = client.post(f"{H}/goals", json={"name": "Mine", "target_amount": 1000}, headers=auth).json()
    assert client.put(f"{H}/goals/{g['id']}", json={"name": "Hacked"}, headers=auth_b).status_code == 404
    assert client.get(f"{H}/data/export", headers=auth_b).json()["data"]["transactions"] == []


# ---------------- categorization ----------------
@pytest.mark.parametrize("desc,direction,expected", [
    ("NETFLIX.COM", "debit", "Subscriptions"), ("AMAZON PRIME VIDEO", "debit", "Subscriptions"), ("AMAZON PAY INDIA", "debit", "Shopping"),
    ("UBER INDIA TRIP", "debit", "Transportation"), ("SALARY ACME TECH", "credit", "Salary and income"), ("HOUSE RENT NOV", "debit", "Housing and rent"),
    ("BAJAJ FIN EMI", "debit", "EMI and loan payments"), ("APOLLO PHARMACY", "debit", "Healthcare"), ("ZERODHA SIP", "debit", "Investments"),
    ("SELF TRANSFER TO SAVINGS", "debit", "Transfers"), ("KSEB ELECTRICITY BILL", "debit", "Utilities"), ("COLLEGE TUITION FEE", "debit", "Education"),
    ("BIGBASKET ORDER", "debit", "Groceries"), ("IRCTC TICKET", "debit", "Travel"), ("YOUTUBE PREMIUM", "debit", "Subscriptions"),
])
def test_rule_categorization(desc, direction, expected):
    r = cat.categorize(desc, direction)
    assert r.category == expected and r.confidence >= 0.8


def test_uncategorized_low_confidence():
    r = cat.categorize("XYZZY QQQ 99", "debit")
    assert r.category == "Other" and r.confidence < 0.6


def test_user_correction_learns(client, auth):
    a = txn(client, auth, description="ZORBA MARKET 1", amount=200, ago=3).json()
    b = txn(client, auth, description="ZORBA MARKET 2", amount=300, ago=2).json()
    assert a["category"] == "Other"
    r = client.put(f"{H}/transactions/{a['id']}", json={"category": "Groceries"}, headers=auth).json()
    assert r["transaction"]["category"] == "Groceries" and r["transaction"]["category_confidence"] == 1.0
    assert r["similar_transactions_recategorized"] == 1
    assert client.get(f"{H}/transactions/{b['id']}", headers=auth).json()["category"] == "Groceries"
    c = txn(client, auth, description="ZORBA MARKET 3", amount=50).json()      # future txn uses learned rule
    assert c["category"] == "Groceries" and c["category_source"] == "user_rule"
    assert client.put(f"{H}/transactions/{a['id']}", json={"category": "Bogus"}, headers=auth).status_code == 422


def test_internal_transfer_detection(client, auth):
    a1 = client.post(f"{H}/accounts", json={"name": "A", "type": "savings"}, headers=auth).json()
    a2 = client.post(f"{H}/accounts", json={"name": "B", "type": "savings"}, headers=auth).json()
    txn(client, auth, description="NEFT 998877", amount=5000, direction="debit", account_id=a1["id"])
    txn(client, auth, description="NEFT 998877", amount=5000, direction="credit", account_id=a2["id"])
    r = client.post(f"{H}/transactions?refresh=false&force=true", json={"date": date.today().isoformat(), "description": "Lunch", "amount": 99, "direction": "debit", "account_id": a1["id"]}, headers=auth)
    items = client.get(f"{H}/transactions?is_internal_transfer=true", headers=auth).json()
    assert items["total"] == 2 and all(i["category"] == "Transfers" for i in items["items"])


# ---------------- imports ----------------
CSV = """Account Statement,,,,
Date,Narration,Withdrawal Amt.,Deposit Amt.,Closing Balance
01/10/2026,SALARY ACME,,"50,000.00","80,000.00"
05/10/2026,UPI/DR/123456789012/SWIGGY/okaxis,450.00,,"79,550.00"
05/10/2026,UPI/DR/123456789012/SWIGGY/okaxis,450.00,,"79,100.00"
badrow,xx,,,
07/10/2026,NETFLIX.COM,649.00,,"78,451.00"
"""


def test_csv_import_dedupe_and_errors(client, auth):
    r = client.post(f"{H}/import/csv", files={"file": ("stmt.csv", CSV.encode(), "text/csv")}, headers=auth)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["status"] == "completed" and j["imported"] == 4 and j["invalid_rows"] == 1 and j["duplicates_skipped"] == 0
    again = client.post(f"{H}/import/csv", files={"file": ("stmt.csv", CSV.encode(), "text/csv")}, headers=auth).json()
    assert again["imported"] == 0 and again["duplicates_skipped"] == 4        # re-import is idempotent
    assert client.get(f"{H}/import/{j['job_id']}", headers=auth).json()["imported"] == 4
    items = client.get(f"{H}/transactions?sort_by=date&order=asc", headers=auth).json()["items"]
    assert items[0]["direction"] == "credit" and items[0]["category"] == "Salary and income"
    accts = client.get(f"{H}/accounts", headers=auth).json()
    assert float(accts[0]["current_balance"]) == 78451.0


def test_excel_import(client, auth):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Transaction Date", "Description", "Amount", "Type"])
    ws.append(["2026-09-01", "SALARY CO", 40000, "CR"])
    ws.append(["2026-09-02", "RAPIDO RIDE", 120, "DR"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(f"{H}/import/excel", files={"file": ("s.xlsx", buf.getvalue(), "application/octet-stream")}, headers=auth)
    assert r.status_code == 201 and r.json()["imported"] == 2


def test_pdf_import_and_scanned_pdf(client, auth):
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    lines = ["Statement of Account", "01/10/2026 SALARY ACME CORP 50,000.00 80,000.00", "03/10/2026 UPI/ZOMATO/ORDER 600.00 79,400.00",
             "04/10/2026 RENT PAYMENT LANDLORD 15,000.00 64,400.00"]
    for i, l in enumerate(lines):
        page.insert_text((40, 60 + 20 * i), l)
    pdf = doc.tobytes()
    r = client.post(f"{H}/import/pdf", files={"file": ("s.pdf", pdf, "application/pdf")}, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["imported"] == 3
    items = client.get(f"{H}/transactions?sort_by=date&order=asc", headers=auth).json()["items"]
    assert [i["direction"] for i in items] == ["credit", "debit", "debit"] and float(items[2]["amount"]) == 15000.0
    blank = pymupdf.open()
    blank.new_page()
    r = client.post(f"{H}/import/pdf", files={"file": ("scan.pdf", blank.tobytes(), "application/pdf")}, headers=auth)
    assert r.status_code == 422 and "OCR" in r.json()["error"]["message"]


def test_upload_validation(client, auth):
    def up(kind, name, data):
        return client.post(f"{H}/import/{kind}", files={"file": (name, data, "application/octet-stream")}, headers=auth)
    assert up("pdf", "x.pdf", b"not a pdf").status_code == 415
    assert up("csv", "x.exe", b"a,b").status_code == 415
    assert up("csv", "x.csv", b"").status_code == 400
    assert up("excel", "x.xlsx", b"not zip").status_code == 415
    assert up("csv", "x.csv", b"a,b\x00c").status_code == 415
    assert up("csv", "x.csv", b"a,b\n1,2\n").status_code == 422        # no recognisable header
    assert up("csv", "../../etc/passwd.csv", CSV.encode()).status_code == 201   # path components are stripped
    from app.core.config import get_settings
    big = b"x" * (get_settings().max_upload_mb * 1024 * 1024 + 1)
    assert up("csv", "big.csv", big).status_code == 413


def test_amount_and_date_parsing():
    from app.services.parsers import parse_amount, parse_date
    assert parse_amount("₹ 1,23,456.50")[0] == pytest.approx(123456.5)
    assert parse_amount("(1,200.00)")[0] == -1200
    assert parse_amount("500.00 Dr") == (500, "dr")
    assert parse_date("05-Jan-2026") == date(2026, 1, 5) and parse_date("05/01/2026") == date(2026, 1, 5)
    with pytest.raises(ValueError):
        parse_amount("abc")
    with pytest.raises(ValueError):
        parse_date("32/13/2026")
