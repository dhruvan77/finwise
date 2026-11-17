import json
from datetime import date, timedelta

import pandas as pd
import pytest

from app.main import app
from app.services import ai_coach
from app.services.forecasting import forecast_series
from app.services.simulation import emi_amount, opportunity_cost

H = "/api/v1"
TODAY = date.today()
PREV_END = TODAY.replace(day=1) - timedelta(days=1)
PREV_START = PREV_END.replace(day=1)


def add(client, auth, d, desc, amt, direction="debit", **kw):
    body = {"date": d.isoformat(), "description": desc, "amount": amt, "direction": direction, **kw}
    r = client.post(f"{H}/transactions?refresh=false&force=true", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------- analytics ----------------
def test_summary_calculations(client, auth):
    d = lambda n: PREV_START + timedelta(days=n)
    add(client, auth, d(0), "SALARY ACME", 50000, "credit")
    add(client, auth, d(4), "HOUSE RENT", 15000)
    add(client, auth, d(9), "SWIGGY ORDER", 5000)
    add(client, auth, d(10), "ZERODHA SIP", 3000)                       # investment: not an expense
    add(client, auth, d(11), "SELF TRANSFER TO SAVINGS", 7000)          # transfer: not an expense
    add(client, auth, d(12), "SWIGGY REFUND", 500, "credit")            # refund reduces food spend
    s = client.get(f"{H}/analytics/summary?start={PREV_START}&end={PREV_END}", headers=auth).json()
    assert s["total_income"] == 50000 and s["total_expenses"] == 19500 and s["net_savings"] == 30500
    assert s["savings_rate"] == 61.0 and s["invested"] == 3000
    cats = {c["category"]: c for c in s["category_breakdown"]}
    assert cats["Housing and rent"]["amount"] == 15000 and cats["Food and dining"]["amount"] == 4500
    assert s["essential_vs_discretionary"] == {"essential": 15000, "discretionary": 4500}
    assert s["fixed_vs_variable"]["fixed"] == 15000
    assert s["largest_expenses"][0]["amount"] == 15000
    days = (PREV_END - PREV_START).days + 1
    assert s["average_daily_spending"] == pytest.approx(19500 / days, abs=0.01)
    m = client.get(f"{H}/analytics/monthly", headers=auth).json()["months"]
    prev = [x for x in m if x["month"] == PREV_START.strftime("%Y-%m")][0]
    assert prev["net_savings"] == 30500 and prev["by_category"]["Housing and rent"] == 15000


def test_analytics_empty_account(client, auth):
    s = client.get(f"{H}/analytics/summary", headers=auth).json()
    assert s["data_available"] is False and s["total_income"] == 0
    assert client.get(f"{H}/analytics/trends", headers=auth).json()["data_available"] is False


def test_trends_and_categories(client, demo):
    t = client.get(f"{H}/analytics/trends?months=6", headers=demo).json()
    assert t["data_available"] and len(t["months"]) == 6 and len(t["expenses"]["values"]) == 6 and t["category_trends"]
    c = client.get(f"{H}/analytics/categories", headers=demo).json()
    assert c["total_expenses"] == pytest.approx(c["essential"] + c["discretionary"], abs=0.02)


# ---------------- budgets ----------------
def test_budget_calculations(client, auth):
    add(client, auth, PREV_START + timedelta(days=3), "SWIGGY", 3000)
    add(client, auth, PREV_START + timedelta(days=5), "ZOMATO", 2000)
    month = PREV_START.strftime("%Y-%m")
    b = client.post(f"{H}/budgets", json={"category": "Food and dining", "limit_amount": 4000, "alert_threshold": 0.8}, headers=auth)
    assert b.status_code == 201
    assert client.post(f"{H}/budgets", json={"category": "Food and dining", "limit_amount": 1}, headers=auth).status_code == 409
    r = client.get(f"{H}/budgets?month={month}", headers=auth).json()["budgets"][0]
    assert r["spent"] == 5000 and r["remaining"] == -1000 and r["utilization_pct"] == 125.0 and r["status"] == "exceeded"
    u = client.put(f"{H}/budgets/{b.json()['id']}", json={"limit_amount": 10000}, headers=auth)
    assert u.status_code == 200
    r = client.get(f"{H}/budgets?month={month}", headers=auth).json()["budgets"][0]
    assert r["status"] == "ok" and r["remaining"] == 5000
    assert client.delete(f"{H}/budgets/{b.json()['id']}", headers=auth).status_code == 204
    assert client.get(f"{H}/budgets", headers=auth).json()["budgets"] == []
    assert client.post(f"{H}/budgets", json={"limit_amount": -1}, headers=auth).status_code == 422


# ---------------- recurring ----------------
def test_recurring_detection_not_every_repeat(client, auth):
    for i, ago in enumerate([25, 55, 85, 115]):
        add(client, auth, TODAY - timedelta(days=ago), "NETFLIX.COM", 649)
    for ago, amt in [(3, 250), (10, 480), (40, 300), (41, 120), (95, 700), (60, 330)]:
        add(client, auth, TODAY - timedelta(days=ago), "SWIGGY ORDER", amt)
    r = client.get(f"{H}/recurring?redetect=true", headers=auth).json()
    names = {i["name"]: i for i in r["items"]}
    assert "Netflix" in names and names["Netflix"]["frequency"] == "monthly" and names["Netflix"]["kind"] == "subscription"
    assert names["Netflix"]["avg_amount"] == 649 and names["Netflix"]["next_date"] > TODAY.isoformat()[:0]
    assert "Swiggy" not in names                                  # irregular repeats are not subscriptions
    last = TODAY - timedelta(days=25)
    assert date.fromisoformat(names["Netflix"]["next_date"]) > last


def test_recurring_user_review(client, auth):
    for ago in [25, 55, 85]:
        add(client, auth, TODAY - timedelta(days=ago), "SPOTIFY INDIA", 119)
    items = client.get(f"{H}/recurring?redetect=true", headers=auth).json()["items"]
    pid = items[0]["id"]
    assert client.put(f"{H}/recurring/{pid}", json={"status": "confirmed", "avg_amount": 129}, headers=auth).json()["status"] == "confirmed"
    client.get(f"{H}/recurring?redetect=true", headers=auth)
    assert client.get(f"{H}/recurring", headers=auth).json()["items"][0]["status"] == "confirmed"
    assert client.delete(f"{H}/recurring/{pid}", headers=auth).status_code == 204
    client.get(f"{H}/recurring?redetect=true", headers=auth)
    assert client.get(f"{H}/recurring", headers=auth).json()["items"] == []        # dismissed stays dismissed
    c = client.post(f"{H}/recurring", json={"name": "Gym", "avg_amount": 1500, "next_date": (TODAY + timedelta(days=5)).isoformat(), "kind": "subscription"}, headers=auth)
    assert c.status_code == 201 and c.json()["is_user_defined"]


def test_demo_recurring_set(client, demo):
    names = {i["name"]: i for i in client.get(f"{H}/recurring", headers=demo).json()["items"]}
    for n, k in [("Salary Acme", "salary"), ("House Rent", "rent"), ("Netflix", "subscription"), ("Bajaj Fin", "emi")]:
        assert names[n]["kind"] == k
    assert "Swiggy" not in names and "Uber India" not in names
    assert names["Youtube Premium"]["amount_change_pct"] > 10


# ---------------- forecasting ----------------
def test_forecast_series_data_sufficiency():
    assert forecast_series(pd.Series(dtype=float))["status"] == "insufficient_data"
    two = forecast_series(pd.Series([1000.0, 1200.0], index=pd.period_range("2026-01", periods=2, freq="M")))
    assert two["status"] == "limited_data" and two["confidence"] == "low" and two["forecast"][0]["point"] == 1100
    s = pd.Series([50000 + (i % 3) * 1000 for i in range(12)], index=pd.period_range("2025-01", periods=12, freq="M"), dtype=float)
    f = forecast_series(s, 2)
    assert f["status"] == "ok" and f["method"] == "holt_damped_exponential_smoothing" and len(f["forecast"]) == 2
    p = f["forecast"][0]
    assert p["lower"] <= p["point"] <= p["upper"] and 45000 < p["point"] < 56000


def test_predictions_no_data_and_with_data(client, auth, demo):
    b = client.get(f"{H}/predictions/balance", headers=demo).json()
    assert b["series_kind"] == "predicted" and set(b["horizons"]) == {"7d", "30d", "90d"}
    assert len(b["daily_series"]) == 90 and b["horizons"]["30d"]["lower"] <= b["horizons"]["30d"]["predicted_balance"] <= b["horizons"]["30d"]["upper"]
    e = client.get(f"{H}/predictions/expenses", headers=demo).json()
    assert e["status"] == "ok" and e["history_months"] >= 6 and e["category_forecast"]
    assert e["history"] and "next_months" in e and e["expected_over_next_days"]["30d"] > 0
    i = client.get(f"{H}/predictions/income", headers=demo).json()
    assert 60000 < i["forecast"][0]["point"] < 80000


def test_predictions_insufficient_data(client, auth_b):
    b = client.get(f"{H}/predictions/balance", headers=auth_b).json()
    assert b["status"] == "insufficient_data" and "horizons" not in b
    e = client.get(f"{H}/predictions/expenses", headers=auth_b).json()
    assert e["status"] == "insufficient_data" and "forecast" not in e


def test_limited_history_is_flagged(client, auth):
    for n in range(3):
        add(client, auth, TODAY - timedelta(days=10 - n), "SWIGGY", 200)
    b = client.get(f"{H}/predictions/balance", headers=auth).json()
    assert b["status"] == "limited_data" and b["confidence"] == "low" and b["warnings"]


# ---------------- anomalies ----------------
def test_anomaly_detection_rules(client, auth):
    for i in range(20):
        add(client, auth, TODAY - timedelta(days=40 - i * 2), "SWIGGY ORDER", 280 + (i % 5) * 20)
    big = add(client, auth, TODAY - timedelta(days=3), "SWIGGY ORDER", 9500)
    add(client, auth, TODAY - timedelta(days=2), "ZOMATO", 650)
    add(client, auth, TODAY - timedelta(days=2), "ZOMATO", 650)
    r = client.get(f"{H}/anomalies", headers=auth).json()
    types = {a["type"] for a in r["items"]}
    assert "unusually_high_transaction" in types and "possible_duplicate" in types
    hi = [a for a in r["items"] if a["transaction_id"] == big["id"]][0]
    for k in ("transaction_id", "type", "amount", "date", "reason", "severity", "recommended_action", "confidence"):
        assert k in hi
    assert hi["severity"] in ("medium", "high") and hi["is_confirmed_fraud"] is False and "not a determination of fraud" in hi["note"]
    one = client.get(f"{H}/anomalies/{hi['id']}", headers=auth).json()
    assert one["id"] == hi["id"]
    assert client.put(f"{H}/anomalies/{hi['id']}/status", json={"status": "reviewed_ok"}, headers=auth).json()["status"] == "reviewed_ok"
    assert hi["id"] not in [a["id"] for a in client.get(f"{H}/anomalies", headers=auth).json()["items"]]


def test_demo_anomalies(client, demo):
    types = {a["type"] for a in client.get(f"{H}/anomalies", headers=demo).json()["items"]}
    assert {"unusually_large_withdrawal", "possible_duplicate", "recurring_payment_change"} <= types


def test_anomaly_isolation(client, demo, auth_b):
    a = client.get(f"{H}/anomalies", headers=demo).json()["items"][0]
    assert client.get(f"{H}/anomalies/{a['id']}", headers=auth_b).status_code == 404


# ---------------- simulations ----------------
def test_purchase_simulation_math(client, demo):
    r = client.post(f"{H}/simulations/purchase", json={"item_name": "laptop", "amount": 35000, "days_from_now": 20, "horizon_days": 90}, headers=demo)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["hypothetical"] and s["before"]["end_balance"] - s["after"]["end_balance"] == pytest.approx(35000, abs=0.05)
    assert s["delta"]["end_balance"] == pytest.approx(-35000, abs=0.05)
    assert s["delta"]["monthly_savings"] == 0 and s["verdict"] in ("affordable", "affordable_with_caution")
    assert s["feasible"] and s["assumptions"] and s["effect_on_goals"] and len(s["series"]["after"]) == 90
    assert client.get(f"{H}/simulations/{s['simulation_id']}", headers=demo).json()["verdict"] == s["verdict"]
    assert client.get(f"{H}/simulations", headers=demo).json()["items"][0]["kind"] == "purchase"


def test_unaffordable_purchase(client, demo):
    s = client.post(f"{H}/simulations/purchase", json={"amount": 5_000_000}, headers=demo).json()
    assert s["feasible"] is False and s["verdict"] == "not_recommended" and s["risk_level"] in ("high", "critical") and s["cash_flow_risks"]


@pytest.mark.parametrize("horizon", [30, 60, 90, 180])
def test_horizons(client, demo, horizon):
    s = client.post(f"{H}/simulations/purchase", json={"amount": 10000, "horizon_days": horizon}, headers=demo).json()
    assert s["horizon_days"] == horizon and len(s["series"]["before"]) == horizon


def test_emi_and_loan_scenarios(client, demo):
    assert emi_amount(100000, 12, 12) == pytest.approx(8884.88, abs=0.01)
    assert emi_amount(12000, 0, 12) == 1000
    r = client.post(f"{H}/simulations/purchase", json={"amount": 60000, "payment": {"type": "emi", "months": 6, "annual_rate_pct": 12}, "horizon_days": 180}, headers=demo).json()
    assert r["emi"]["monthly_emi"] == pytest.approx(emi_amount(60000, 12, 6), abs=0.01)
    assert r["delta"]["monthly_savings"] == pytest.approx(-r["emi"]["monthly_emi"], abs=1)
    ln = client.post(f"{H}/simulations/scenario", json={"type": "loan", "params": {"principal": 200000, "annual_rate_pct": 10, "months": 24}, "horizon_days": 180}, headers=demo).json()
    assert ln["loan"]["total_interest"] > 0 and ln["delta"]["monthly_savings"] < 0


def test_other_scenarios(client, demo):
    base = client.post(f"{H}/simulations/scenario", json={"type": "increase_monthly_savings", "params": {"monthly_amount": 5000}}, headers=demo).json()
    assert base["after"]["savings_transferred"] > 10000
    cut = client.post(f"{H}/simulations/scenario", json={"type": "reduce_category_spending", "params": {"category": "Food and dining", "percent": 50}}, headers=demo).json()
    assert cut["delta"]["monthly_savings"] > 0
    inc = client.post(f"{H}/simulations/scenario", json={"type": "increase_income", "params": {"monthly_amount": 10000}}, headers=demo).json()
    assert inc["delta"]["monthly_savings"] == pytest.approx(10000, abs=1)
    sub = client.post(f"{H}/simulations/scenario", json={"type": "new_subscription", "params": {"monthly_amount": 500}}, headers=demo).json()
    assert sub["delta"]["monthly_savings"] == pytest.approx(-500, abs=1)
    for kind, p in [("salary_delay", {"days": 20}), ("salary_loss", {"months": 2}), ("unexpected_expense", {"amount": 30000})]:
        assert client.post(f"{H}/simulations/scenario", json={"type": kind, "params": p}, headers=demo).status_code == 201
    assert client.post(f"{H}/simulations/scenario", json={"type": "teleport", "params": {}}, headers=demo).status_code == 422
    assert client.post(f"{H}/simulations/scenario", json={"type": "loan", "params": {}}, headers=demo).status_code == 422


def test_simulation_without_data(client, auth):
    r = client.post(f"{H}/simulations/purchase", json={"amount": 1000}, headers=auth)
    assert r.status_code == 422 and r.json()["error"]["code"] == "insufficient_data"


def test_opportunity_cost():
    r = opportunity_cost(35000, 5, [8])
    assert r["scenarios"][0]["lump_sum_value"] == pytest.approx(35000 * 1.08 ** 5, abs=0.01)
    assert any("NOT forecasts" in a for a in r["assumptions"])


def test_opportunity_cost_endpoint(client, auth):
    r = client.post(f"{H}/simulations/opportunity-cost", json={"amount": 35000, "years": 3, "monthly_equivalent": True}, headers=auth)
    assert r.status_code == 200 and len(r.json()["scenarios"]) == 3 and "if_spread_monthly_value" in r.json()["scenarios"][0]
    assert client.post(f"{H}/simulations/opportunity-cost", json={"amount": -1}, headers=auth).status_code == 422


# ---------------- stress tests ----------------
def test_stress_tests(client, demo):
    d = client.post(f"{H}/stress-tests", json={"type": "salary_delay", "params": {"days": 30}}, headers=demo).json()
    assert d["hypothetical"] and d["emergency_cash_runway"]["months_of_essential_expenses"] > 0 and d["suggested_actions"] and "hypothetical" in d["disclaimer"].lower()
    loss = client.post(f"{H}/stress-tests", json={"type": "income_loss", "params": {"months": 3}}, headers=demo).json()
    assert loss["stressed"]["end_balance"] < loss["baseline"]["end_balance"] - 100000
    big = client.post(f"{H}/stress-tests", json={"type": "medical_expense", "params": {"amount": 900000}}, headers=demo).json()
    assert big["potential_shortfall"] > 0 and big["risk_level"] in ("high", "critical") and big["can_cover_essential_expenses"] is False
    assert len(client.get(f"{H}/stress-tests", headers=demo).json()["items"]) == 3
    assert client.post(f"{H}/stress-tests", json={"type": "bogus"}, headers=demo).status_code == 422
    red = client.post(f"{H}/stress-tests", json={"type": "income_reduction", "params": {"percent": 50, "months": 2}}, headers=demo).json()
    assert red["stressed"]["end_balance"] < red["baseline"]["end_balance"]


# ---------------- goals ----------------
def test_goal_lifecycle(client, demo):
    target = (TODAY + timedelta(days=300)).isoformat()
    g = client.post(f"{H}/goals", json={"name": "Trip", "target_amount": 30000, "current_amount": 6000, "target_date": target, "monthly_contribution": 2500}, headers=demo).json()
    assert g["progress_pct"] == 20.0 and g["remaining_amount"] == 24000
    est = date.fromisoformat(g["estimated_completion_date"])
    assert abs((est - TODAY).days - 9.6 * 30.4375) < 3
    assert g["required_monthly_to_hit_target_date"] == pytest.approx(24000 / (300 / 30.4375), abs=1)
    assert g["on_track_with_current_contribution"] is True and g["feasibility"]["verdict"]
    up = client.put(f"{H}/goals/{g['id']}", json={"current_amount": 30000}, headers=demo).json()
    assert up["status"] == "achieved" and up["progress_pct"] == 100.0
    lst = client.get(f"{H}/goals", headers=demo).json()
    assert len(lst["goals"]) == 3 and "free_cash_flow_monthly" in lst
    assert client.delete(f"{H}/goals/{g['id']}", headers=demo).status_code == 204
    assert client.post(f"{H}/goals", json={"name": "x", "target_amount": 0}, headers=demo).status_code == 422


def test_savings_feasibility(client, demo, db):
    from app.models import User
    from app.services.goals import savings_feasibility
    uid = db.query(User).first().id
    ok = savings_feasibility(db, uid, 10000, 10)
    no = savings_feasibility(db, uid, 5_000_000, 6)
    assert ok["feasible"] and not no["feasible"] and no["shortfall_monthly"] > 0


# ---------------- twin / notifications ----------------
def test_twin_and_history(client, demo):
    t = client.get(f"{H}/financial-twin?refresh=true", headers=demo).json()
    for k in ("balance", "income", "spending", "savings", "recurring", "upcoming_payments", "goals", "forecast", "anomalies", "data_quality"):
        assert k in t
    assert t["income"]["regular_income_detected"] and t["forecast"]["series_kind"] == "predicted"
    h = client.get(f"{H}/financial-twin/history", headers=demo).json()
    assert h["count"] >= 1 and h["snapshots"][0]["balance"] == t["balance"]["current"]


def test_twin_updates_on_new_transaction(client, demo):
    before = client.get(f"{H}/financial-twin", headers=demo).json()["balance"]["current"]
    r = client.post(f"{H}/transactions", json={"date": TODAY.isoformat(), "description": "ONLINE SHOPPING LAPTOP", "amount": 20000, "direction": "debit"}, headers=demo)
    assert r.status_code == 201
    after = client.get(f"{H}/financial-twin", headers=demo).json()["balance"]["current"]
    assert before - after == pytest.approx(20000, abs=0.01)
    assert client.get(f"{H}/financial-twin", headers={}).status_code == 401


def test_twin_empty(client, auth):
    t = client.get(f"{H}/financial-twin", headers=auth).json()
    assert t["data_quality"]["status"] == "none" and "balance" not in t


def test_notifications(client, demo):
    n = client.get(f"{H}/notifications?refresh=true", headers=demo).json()
    types = {i["type"] for i in n["items"]}
    assert n["items"] and ("budget_limit" in types) and ("unusual_spending" in types or "spending_pattern_change" in types)
    nid = n["items"][0]["id"]
    assert client.put(f"{H}/notifications/{nid}/read", headers=demo).json()["is_read"] is True
    assert client.delete(f"{H}/notifications/{nid}", headers=demo).status_code == 204
    again = client.get(f"{H}/notifications?refresh=true", headers=demo).json()
    assert nid not in [i["id"] for i in again["items"]]                # dismissed stays dismissed, no duplicate re-created
    assert client.put(f"{H}/notifications/{nid}/read", headers=demo).status_code == 200


# ---------------- AI coach (mocked LLM) ----------------
class FakeLLM:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def chat(self, messages, tools):
        self.calls.append((messages, tools))
        return self.script.pop(0)


def tc(name, args, id_="c1"):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


FINAL = json.dumps({"answer": "Yes, you can afford it.", "key_figures": [{"label": "Lowest balance", "value": "₹1,00,000"}],
                    "assumptions": ["recurring bills continue"], "uncertainty": "spending may vary", "suggested_follow_ups": []})


@pytest.fixture
def fake_llm(client):
    holder = {}

    def set_script(script):
        holder["llm"] = FakeLLM(script)
        app.dependency_overrides[ai_coach.get_llm_client] = lambda: holder["llm"]
        return holder["llm"]
    yield set_script
    app.dependency_overrides.clear()


def test_ai_chat_tool_execution(client, demo, fake_llm):
    llm = fake_llm([{"content": "", "tool_calls": [tc("simulate_purchase", {"amount": 35000, "item_name": "laptop"}), tc("get_financial_summary", {"period_days": 30}, "c2")]},
                    {"content": FINAL}])
    r = client.post(f"{H}/ai/chat", json={"message": "Can I afford a ₹35,000 laptop next month?"}, headers=demo)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["tools_used"] == ["simulate_purchase", "get_financial_summary"] and d["structured"]["key_figures"] and "not guaranteed" in d["disclaimer"]
    tool_msgs = [m for m in llm.calls[1][0] if m["role"] == "tool"]
    sim = json.loads(tool_msgs[0]["content"])
    assert sim["kind"] == "purchase" and "series" not in sim and sim["delta"]["end_balance"] == pytest.approx(-35000, abs=0.05)
    assert llm.calls[0][1] and llm.calls[0][0][0]["role"] == "system"
    cid = d["conversation_id"]
    conv = client.get(f"{H}/ai/conversations/{cid}", headers=demo).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"] and conv["messages"][1]["tool_calls"][0]["name"] == "simulate_purchase"
    fake_llm([{"content": FINAL}])
    r2 = client.post(f"{H}/ai/chat", json={"message": "thanks", "conversation_id": cid}, headers=demo)
    assert r2.json()["conversation_id"] == cid and len(client.get(f"{H}/ai/conversations/{cid}", headers=demo).json()["messages"]) == 4
    assert len(client.get(f"{H}/ai/conversations", headers=demo).json()["items"]) == 1
    assert client.delete(f"{H}/ai/conversations/{cid}", headers=demo).status_code == 204
    assert client.get(f"{H}/ai/conversations/{cid}", headers=demo).status_code == 404


def test_ai_tool_safety(client, demo, auth_b, fake_llm, db):
    fake_llm([{"content": "", "tool_calls": [tc("get_transaction_history", {"limit": 5000, "user_id": "00000000-0000-0000-0000-000000000000"}), tc("drop_database", {}, "c2"),
                                             tc("calculate_savings_feasibility", {"months": 6}, "c3")]},
              {"content": "plain text, not json"}])
    r = client.post(f"{H}/ai/chat", json={"message": "hi"}, headers=demo).json()
    assert r["answer"] == "plain text, not json"
    msgs = ai_coach.__dict__  # noqa: F841
    from app.models import User
    uid = db.query(User).filter_by(email="alice@example.com").first().id
    hist = ai_coach.execute_tool(db, uid, "get_transaction_history", {"limit": 5000})
    assert hist["returned"] <= 30
    assert "error" in ai_coach.execute_tool(db, uid, "drop_database", {})
    assert "error" in ai_coach.execute_tool(db, uid, "calculate_savings_feasibility", {"months": 6})
    bob = db.query(User).filter_by(email="bob@example.com").first()
    assert bob is None or ai_coach.execute_tool(db, bob.id, "get_transaction_history", {})["total_matching"] == 0


def test_ai_tool_call_limit_and_rounds(client, demo, fake_llm):
    many = [tc("get_recurring_payments", {}, f"c{i}") for i in range(12)]
    llm = fake_llm([{"content": "", "tool_calls": many}, {"content": FINAL}])
    r = client.post(f"{H}/ai/chat", json={"message": "x"}, headers=demo).json()
    assert len(r["tools_used"]) == 8
    loop = [{"content": "", "tool_calls": [tc("get_financial_goals", {})]} for _ in range(5)] + [{"content": FINAL}]
    llm = fake_llm(loop)
    assert client.post(f"{H}/ai/chat", json={"message": "loop"}, headers=demo).status_code == 200
    assert llm.calls[-1][1] is None                                   # forced final answer without tools


def test_ai_not_configured_and_validation(client, demo, auth_b, fake_llm):
    r = client.post(f"{H}/ai/chat", json={"message": "hi"}, headers=demo)
    assert r.status_code == 503 and r.json()["error"]["code"] == "ai_not_configured"
    fake_llm([])
    assert client.post(f"{H}/ai/chat", json={"message": ""}, headers=demo).status_code == 422
    assert client.post(f"{H}/ai/chat", json={"message": "x" * 2001}, headers=demo).status_code == 422
    assert client.get(f"{H}/ai/conversations/00000000-0000-4000-8000-000000000001", headers=auth_b).status_code == 404


def test_ai_provider_failure_is_clean(client, demo, fake_llm):
    from app.core.errors import AppError

    class Boom:
        def chat(self, *a):
            raise AppError(502, "llm_unavailable", "The AI provider is unavailable. Please try again.")
    app.dependency_overrides[ai_coach.get_llm_client] = lambda: Boom()
    r = client.post(f"{H}/ai/chat", json={"message": "hi"}, headers=demo)
    assert r.status_code == 502 and r.json()["error"]["code"] == "llm_unavailable"


# ---------------- data management ----------------
def test_export_and_delete(client, demo, auth_b):
    exp = client.get(f"{H}/data/export", headers=demo).json()
    assert len(exp["data"]["transactions"]) > 500 and exp["data"]["user"]["email"] == "alice@example.com"
    assert client.delete(f"{H}/data/account", headers=demo).status_code == 422
    assert client.delete(f"{H}/data/account?confirm=nope", headers=demo).status_code == 400
    r = client.delete(f"{H}/data/account?confirm=DELETE", headers=demo)
    assert r.status_code == 200 and r.json()["deleted_rows"]["transactions"] > 500
    assert client.get(f"{H}/transactions", headers=demo).json()["total"] == 0          # profile re-provisioned empty
    assert client.get(f"{H}/data/export", headers=auth_b).status_code == 200


def test_demo_import_guard(client, demo):
    r = client.post(f"{H}/import/demo", headers=demo)
    assert r.status_code == 409 and r.json()["error"]["code"] == "data_exists"
