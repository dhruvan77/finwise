"""Simulations, stress tests, goals, AI coach, notifications, data management."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.core import ratelimit
from app.core.errors import AppError, NotFound
from app.models import AIConversation, Goal, Notification, Simulation, StressTest
from app.schemas.schemas import ChatIn, GoalIn, GoalUpdate, OpportunityIn, PurchaseSimIn, ScenarioSimIn, StressIn
from app.services import ai_coach, data_management, goals as goals_svc, simulation
from app.services.notifications import generate_notifications, serialize as ser_notif
from app.services.twin import refresh_twin
from app.utils.money import q2

router = APIRouter()


# ---- simulations -------------------------------------------------------------
@router.post("/simulations/purchase", status_code=201, tags=["Simulations"], dependencies=[Depends(ratelimit.sensitive_limit)])
def sim_purchase(body: PurchaseSimIn, user: CurrentUser, db: DB):
    res = simulation.simulate_purchase(db, user.id, body.amount, body.item_name, body.horizon_days, body.days_from_now,
                                       body.payment.model_dump() if body.payment else None)
    db.commit()
    return res


@router.post("/simulations/scenario", status_code=201, tags=["Simulations"], dependencies=[Depends(ratelimit.sensitive_limit)])
def sim_scenario(body: ScenarioSimIn, user: CurrentUser, db: DB):
    """Scenario types: purchase, laptop, phone, large_purchase, loan, increase_monthly_savings, reduce_category_spending,
    salary_delay, salary_loss, unexpected_expense, increase_income, new_subscription."""
    try:
        res = simulation.run_scenario(db, user.id, body.type, body.params, body.horizon_days)
    except (KeyError, ValueError, TypeError) as e:
        raise AppError(422, "invalid_params", f"Missing or invalid scenario parameter ({type(e).__name__}: {e}).")
    db.commit()
    return res


@router.post("/simulations/opportunity-cost", tags=["Simulations"])
def sim_opportunity(body: OpportunityIn, user: CurrentUser):
    return simulation.opportunity_cost(body.amount, body.years, body.annual_returns_pct, body.monthly_equivalent)


@router.get("/simulations", tags=["Simulations"])
def list_sims(user: CurrentUser, db: DB, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    rows = db.scalars(select(Simulation).where(Simulation.user_id == user.id).order_by(Simulation.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{"id": str(s.id), "kind": s.kind, "horizon_days": s.horizon_days, "params": s.params, "verdict": s.result.get("verdict"),
                       "feasible": s.result.get("feasible"), "created_at": s.created_at.isoformat() if s.created_at else None} for s in rows], "page": page}


@router.get("/simulations/{simulation_id}", tags=["Simulations"])
def get_sim(simulation_id: uuid.UUID, user: CurrentUser, db: DB):
    s = db.scalar(select(Simulation).where(Simulation.id == simulation_id, Simulation.user_id == user.id))
    if not s:
        raise NotFound("Simulation")
    return {"simulation_id": str(s.id), **s.result}


# ---- stress tests ------------------------------------------------------------
@router.post("/stress-tests", status_code=201, tags=["Stress Tests"], dependencies=[Depends(ratelimit.sensitive_limit)])
def stress(body: StressIn, user: CurrentUser, db: DB):
    try:
        res = simulation.run_stress_test(db, user.id, body.type, body.params, body.horizon_days)
    except (KeyError, ValueError, TypeError) as e:
        raise AppError(422, "invalid_params", f"Missing or invalid stress-test parameter ({type(e).__name__}: {e}).")
    db.commit()
    return res


@router.get("/stress-tests", tags=["Stress Tests"])
def list_stress(user: CurrentUser, db: DB, limit: int = Query(20, ge=1, le=100)):
    rows = db.scalars(select(StressTest).where(StressTest.user_id == user.id).order_by(StressTest.created_at.desc()).limit(limit)).all()
    return {"items": [{"id": str(s.id), "kind": s.kind, "params": s.params, "risk_level": s.result.get("risk_level"),
                       "potential_shortfall": s.result.get("potential_shortfall"), "result": s.result,
                       "created_at": s.created_at.isoformat() if s.created_at else None} for s in rows]}


# ---- goals -------------------------------------------------------------------
def _own_goal(db, user, gid) -> Goal:
    g = db.scalar(select(Goal).where(Goal.id == gid, Goal.user_id == user.id))
    if not g:
        raise NotFound("Goal")
    return g


@router.post("/goals", status_code=201, tags=["Goals"])
def create_goal(body: GoalIn, user: CurrentUser, db: DB):
    g = Goal(user_id=user.id, **body.model_dump())
    db.add(g)
    db.commit()
    return goals_svc.analyze_goal(g, _cap(db, user), __import__("datetime").date.today())


def _cap(db, user) -> float:
    from app.services.forecasting import build_context, run_rate_monthly
    ctx = build_context(db, user.id)
    return run_rate_monthly(ctx) if ctx.n_transactions else 0.0


@router.get("/goals", tags=["Goals"])
def list_goals(user: CurrentUser, db: DB):
    return goals_svc.goals_with_analysis(db, user.id)


@router.put("/goals/{goal_id}", tags=["Goals"])
def update_goal(goal_id: uuid.UUID, body: GoalUpdate, user: CurrentUser, db: DB):
    g = _own_goal(db, user, goal_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None or k == "target_date":
            setattr(g, k, v)
    if g.status == "achieved" or float(g.current_amount) >= float(g.target_amount):
        g.status = "achieved"
    db.commit()
    return goals_svc.analyze_goal(g, _cap(db, user), __import__("datetime").date.today())


@router.delete("/goals/{goal_id}", status_code=204, tags=["Goals"])
def delete_goal(goal_id: uuid.UUID, user: CurrentUser, db: DB):
    db.delete(_own_goal(db, user, goal_id))
    db.commit()


# ---- AI coach ----------------------------------------------------------------
@router.post("/ai/chat", tags=["AI Coach"], dependencies=[Depends(ratelimit.sensitive_limit)])
def ai_chat(body: ChatIn, user: CurrentUser, db: DB, llm=Depends(ai_coach.get_llm_client)):
    return ai_coach.chat(db, user, body.message, body.conversation_id, llm)


@router.get("/ai/conversations", tags=["AI Coach"])
def list_convs(user: CurrentUser, db: DB, limit: int = Query(30, ge=1, le=100)):
    rows = db.scalars(select(AIConversation).where(AIConversation.user_id == user.id).order_by(AIConversation.updated_at.desc()).limit(limit)).all()
    return {"items": [{"id": str(c.id), "title": c.title, "updated_at": c.updated_at.isoformat() if c.updated_at else None} for c in rows]}


@router.get("/ai/conversations/{conversation_id}", tags=["AI Coach"])
def get_conv(conversation_id: uuid.UUID, user: CurrentUser, db: DB):
    c = db.scalar(select(AIConversation).where(AIConversation.id == conversation_id, AIConversation.user_id == user.id))
    if not c:
        raise NotFound("Conversation")
    return {"id": str(c.id), "title": c.title, "messages": [{"id": str(m.id), "role": m.role, "content": m.content, "structured": m.structured,
                                                             "tool_calls": m.tool_calls, "created_at": m.created_at.isoformat() if m.created_at else None} for m in c.messages]}


@router.delete("/ai/conversations/{conversation_id}", status_code=204, tags=["AI Coach"])
def delete_conv(conversation_id: uuid.UUID, user: CurrentUser, db: DB):
    c = db.scalar(select(AIConversation).where(AIConversation.id == conversation_id, AIConversation.user_id == user.id))
    if not c:
        raise NotFound("Conversation")
    db.delete(c)
    db.commit()


# ---- notifications -----------------------------------------------------------
def _own_notif(db, user, nid) -> Notification:
    n = db.scalar(select(Notification).where(Notification.id == nid, Notification.user_id == user.id))
    if not n:
        raise NotFound("Notification")
    return n


@router.get("/notifications", tags=["Notifications"])
def list_notifications(user: CurrentUser, db: DB, unread_only: bool = False, refresh: bool = False, limit: int = Query(50, ge=1, le=200)):
    if refresh:
        generate_notifications(db, user.id)
        db.commit()
    q = select(Notification).where(Notification.user_id == user.id, Notification.is_dismissed == False)  # noqa: E712
    if unread_only:
        q = q.where(Notification.is_read == False)  # noqa: E712
    rows = db.scalars(q.order_by(Notification.created_at.desc()).limit(limit)).all()
    return {"items": [ser_notif(n) for n in rows], "unread": sum(1 for n in rows if not n.is_read)}


@router.put("/notifications/{notification_id}/read", tags=["Notifications"])
def read_notification(notification_id: uuid.UUID, user: CurrentUser, db: DB):
    n = _own_notif(db, user, notification_id)
    n.is_read = True
    db.commit()
    return ser_notif(n)


@router.delete("/notifications/{notification_id}", status_code=204, tags=["Notifications"])
def dismiss_notification(notification_id: uuid.UUID, user: CurrentUser, db: DB):
    _own_notif(db, user, notification_id).is_dismissed = True
    db.commit()


# ---- data management ---------------------------------------------------------
@router.get("/data/export", tags=["Data Management"], dependencies=[Depends(ratelimit.sensitive_limit)])
def export_data(user: CurrentUser, db: DB):
    return data_management.export_user_data(db, user)


@router.delete("/data/account", tags=["Data Management"], dependencies=[Depends(ratelimit.sensitive_limit)])
def delete_account(user: CurrentUser, db: DB, confirm: str = Query(..., description="Must be exactly DELETE")):
    if confirm != "DELETE":
        raise AppError(400, "confirmation_required", "Pass ?confirm=DELETE to permanently delete your account and all data.")
    return data_management.delete_user_data(db, user)
