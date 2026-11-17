from __future__ import annotations

import re
import uuid
import datetime as dt
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.categorization import CATEGORY_META

MAX_MONEY = Decimal("1000000000")
EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
Money = Field(gt=0, le=MAX_MONEY, decimal_places=2)


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str | None
    currency: str
    preferences: dict
    settings: dict
    created_at: datetime | None = None


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=200)
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    preferences: dict | None = None
    settings: dict | None = None
    opening_balance: Decimal | None = Field(None, ge=-MAX_MONEY, le=MAX_MONEY, description="Starting balance used when no account balance is known")


class Credentials(BaseModel):
    email: str = Field(pattern=EMAIL_RE, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(None, max_length=200)


class DevTokenIn(BaseModel):
    email: str = Field(pattern=EMAIL_RE, max_length=320)


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: Literal["savings", "current", "credit_card", "wallet", "cash"] = "savings"
    institution: str | None = Field(None, max_length=120)
    current_balance: Decimal | None = Field(None, ge=-MAX_MONEY, le=MAX_MONEY)
    balance_as_of: date | None = None


class AccountUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    institution: str | None = None
    current_balance: Decimal | None = Field(None, ge=-MAX_MONEY, le=MAX_MONEY)
    balance_as_of: date | None = None


class AccountOut(ORM):
    id: uuid.UUID
    name: str
    type: str
    institution: str | None
    currency: str
    current_balance: Decimal | None
    balance_as_of: date | None


class TransactionIn(BaseModel):
    date: dt.date
    description: str = Field(min_length=1, max_length=500)
    amount: Decimal = Money
    direction: Literal["debit", "credit"]
    account_id: uuid.UUID | None = None
    category: str | None = None
    notes: str | None = Field(None, max_length=1000)
    balance_after: Decimal | None = None

    @field_validator("date")
    @classmethod
    def _not_future(cls, v: dt.date):
        if v > date.today().replace(year=date.today().year + 1):
            raise ValueError("date too far in the future")
        return v

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        if v is not None and v not in CATEGORY_META:
            raise ValueError(f"unknown category; valid: {', '.join(CATEGORY_META)}")
        return v


class TransactionUpdate(BaseModel):
    date: dt.date | None = None
    description: str | None = Field(None, min_length=1, max_length=500)
    amount: Decimal | None = Field(None, gt=0, le=MAX_MONEY, decimal_places=2)
    direction: Literal["debit", "credit"] | None = None
    category: str | None = None
    notes: str | None = Field(None, max_length=1000)
    is_internal_transfer: bool | None = None


class TransactionOut(ORM):
    id: uuid.UUID
    account_id: uuid.UUID | None
    date: dt.date
    description: str
    merchant: str | None
    amount: Decimal
    direction: str
    currency: str
    category: str
    category_confidence: float
    category_source: str
    is_internal_transfer: bool
    balance_after: Decimal | None
    notes: str | None
    source: str


class BudgetIn(BaseModel):
    category: str | None = None
    limit_amount: Decimal = Money
    year_month: str | None = Field(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    alert_threshold: float = Field(0.8, gt=0, le=1)

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        if v is not None and v not in CATEGORY_META:
            raise ValueError("unknown category")
        return v


class BudgetUpdate(BaseModel):
    limit_amount: Decimal | None = Field(None, gt=0, le=MAX_MONEY, decimal_places=2)
    alert_threshold: float | None = Field(None, gt=0, le=1)
    year_month: str | None = Field(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


class RecurringIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = "Other"
    kind: Literal["subscription", "rent", "emi", "insurance", "utility", "salary", "transfer", "investment", "other"] = "other"
    direction: Literal["debit", "credit"] = "debit"
    frequency: Literal["weekly", "monthly", "quarterly", "yearly"] = "monthly"
    avg_amount: Decimal = Money
    next_date: date


class RecurringUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    status: Literal["detected", "confirmed", "dismissed"] | None = None
    frequency: Literal["weekly", "monthly", "quarterly", "yearly"] | None = None
    avg_amount: Decimal | None = Field(None, gt=0, le=MAX_MONEY, decimal_places=2)
    next_date: date | None = None
    kind: Literal["subscription", "rent", "emi", "insurance", "utility", "salary", "transfer", "investment", "other"] | None = None


class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    target_amount: Decimal = Money
    current_amount: Decimal = Field(Decimal("0"), ge=0, le=MAX_MONEY)
    target_date: date | None = None
    monthly_contribution: Decimal = Field(Decimal("0"), ge=0, le=MAX_MONEY)


class GoalUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=160)
    target_amount: Decimal | None = Field(None, gt=0, le=MAX_MONEY)
    current_amount: Decimal | None = Field(None, ge=0, le=MAX_MONEY)
    target_date: date | None = None
    monthly_contribution: Decimal | None = Field(None, ge=0, le=MAX_MONEY)
    status: Literal["active", "paused", "achieved"] | None = None


class PaymentPlan(BaseModel):
    type: Literal["cash", "emi"] = "cash"
    months: int = Field(12, ge=1, le=120)
    annual_rate_pct: float = Field(0, ge=0, le=60)
    down_payment: float = Field(0, ge=0)


class PurchaseSimIn(BaseModel):
    item_name: str = Field("purchase", max_length=120)
    amount: float = Field(gt=0, le=1e9)
    horizon_days: int = Field(90, ge=7, le=365)
    days_from_now: int = Field(1, ge=1, le=365)
    payment: PaymentPlan | None = None


class ScenarioSimIn(BaseModel):
    type: str = Field(max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)
    horizon_days: int = Field(90, ge=7, le=365)


class StressIn(BaseModel):
    type: Literal["salary_delay", "medical_expense", "large_purchase", "income_reduction", "expense_increase", "income_loss"]
    params: dict[str, Any] = Field(default_factory=dict)
    horizon_days: int = Field(90, ge=7, le=365)


class OpportunityIn(BaseModel):
    amount: float = Field(gt=0, le=1e9)
    years: float = Field(5, gt=0, le=40)
    annual_returns_pct: list[float] | None = Field(None, max_length=5)
    monthly_equivalent: bool = False

    @field_validator("annual_returns_pct")
    @classmethod
    def _rates(cls, v):
        if v and any(r < -50 or r > 50 for r in v):
            raise ValueError("annual return must be between -50 and 50")
        return v


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None


class AnomalyStatusIn(BaseModel):
    status: Literal["open", "reviewed_ok", "dismissed"]
