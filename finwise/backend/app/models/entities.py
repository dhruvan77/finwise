"""SQLAlchemy ORM models. Money columns are Numeric(14,2) (Decimal), never float."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric,
                        String, Text, UniqueConstraint, Uuid, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

Money = Numeric(14, 2)


def _id() -> uuid.UUID:
    return uuid.uuid4()


class Stamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def owner() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)


class User(Base, Stamped):
    """Profile row. `id` equals the Supabase auth.users.id (JWT `sub`)."""
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Account(Base, Stamped):
    __tablename__ = "accounts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(30), default="savings")  # savings|current|credit_card|wallet|cash
    institution: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    current_balance: Mapped[Decimal | None] = mapped_column(Money)
    balance_as_of: Mapped[date | None] = mapped_column(Date)


class Category(Base):
    """Catalog of categories and their analytic flags (global)."""
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # expense|income|transfer|investment
    is_essential: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fixed: Mapped[bool] = mapped_column(Boolean, default=False)


class CategoryRule(Base, Stamped):
    """Learned from user corrections: merchant key -> category."""
    __tablename__ = "category_rules"
    __table_args__ = (UniqueConstraint("user_id", "merchant_key", name="uq_rule_user_merchant"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    merchant_key: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60))
    hits: Mapped[int] = mapped_column(Integer, default=1)


class ImportJob(Base, Stamped):
    __tablename__ = "import_jobs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    source_type: Mapped[str] = mapped_column(String(20))  # pdf|csv|excel
    filename: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="processing")  # processing|completed|failed
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    imported: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    invalid: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Transaction(Base, Stamped):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_txn_user_date", "user_id", "date"),
        Index("ix_txn_user_category", "user_id", "category"),
        Index("ix_txn_user_fingerprint", "user_id", "fingerprint"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("accounts.id", ondelete="SET NULL"), index=True)
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("import_jobs.id", ondelete="SET NULL"))
    date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(500))
    merchant: Mapped[str | None] = mapped_column(String(120))
    amount: Mapped[Decimal] = mapped_column(Money)               # always positive
    direction: Mapped[str] = mapped_column(String(6))             # debit|credit
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    category: Mapped[str] = mapped_column(String(60), default="Other")
    category_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    category_source: Mapped[str] = mapped_column(String(20), default="default")  # rule|ml|user|user_rule|default
    is_internal_transfer: Mapped[bool] = mapped_column(Boolean, default=False)
    balance_after: Mapped[Decimal | None] = mapped_column(Money)
    notes: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual|pdf|csv|excel|demo
    account = relationship("Account")


class Budget(Base, Stamped):
    __tablename__ = "budgets"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    category: Mapped[str | None] = mapped_column(String(60))      # NULL = overall monthly budget
    limit_amount: Mapped[Decimal] = mapped_column(Money)
    year_month: Mapped[str | None] = mapped_column(String(7))     # 'YYYY-MM'; NULL = applies to every month
    alert_threshold: Mapped[float] = mapped_column(Float, default=0.8)


class RecurringPayment(Base, Stamped):
    __tablename__ = "recurring_payments"
    __table_args__ = (UniqueConstraint("user_id", "merchant_key", "direction", name="uq_recurring_user_merchant"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    name: Mapped[str] = mapped_column(String(120))
    merchant_key: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60), default="Other")
    kind: Mapped[str] = mapped_column(String(20), default="other")  # subscription|rent|emi|insurance|utility|salary|transfer|other
    direction: Mapped[str] = mapped_column(String(6), default="debit")
    frequency: Mapped[str] = mapped_column(String(12), default="monthly")  # weekly|monthly|quarterly|yearly
    avg_amount: Mapped[Decimal] = mapped_column(Money)
    last_amount: Mapped[Decimal | None] = mapped_column(Money)
    last_date: Mapped[date | None] = mapped_column(Date)
    next_date: Mapped[date | None] = mapped_column(Date)
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    amount_change_pct: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(12), default="detected")  # detected|confirmed|dismissed
    is_user_defined: Mapped[bool] = mapped_column(Boolean, default=False)
    last_transaction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class FinancialProfile(Base):
    """Digital-twin head state (one row per user) + user-supplied profile inputs."""
    __tablename__ = "financial_profiles"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    opening_balance: Mapped[Decimal] = mapped_column(Money, default=0)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FinancialSnapshot(Base, Stamped):
    __tablename__ = "financial_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "snapshot_date", name="uq_snapshot_user_date"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    snapshot_date: Mapped[date] = mapped_column(Date)
    state: Mapped[dict] = mapped_column(JSON, default=dict)


class Prediction(Base, Stamped):
    __tablename__ = "predictions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    kind: Mapped[str] = mapped_column(String(20))  # expenses|income|balance
    horizon_days: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(60))
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class Anomaly(Base, Stamped):
    __tablename__ = "anomalies"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key", name="uq_anomaly_dedupe"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("transactions.id", ondelete="CASCADE"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(160))
    type: Mapped[str] = mapped_column(String(40))
    amount: Mapped[Decimal | None] = mapped_column(Money)
    date: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10))   # low|medium|high
    confidence: Mapped[float | None] = mapped_column(Float)
    recommended_action: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|reviewed_ok|dismissed


class Goal(Base, Stamped):
    __tablename__ = "goals"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    name: Mapped[str] = mapped_column(String(160))
    target_amount: Mapped[Decimal] = mapped_column(Money)
    current_amount: Mapped[Decimal] = mapped_column(Money, default=0)
    target_date: Mapped[date | None] = mapped_column(Date)
    monthly_contribution: Mapped[Decimal] = mapped_column(Money, default=0)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active|achieved|paused
    last_notified_pct: Mapped[int] = mapped_column(Integer, default=0)


class Simulation(Base, Stamped):
    __tablename__ = "simulations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    kind: Mapped[str] = mapped_column(String(40))
    horizon_days: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class StressTest(Base, Stamped):
    __tablename__ = "stress_tests"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    kind: Mapped[str] = mapped_column(String(40))
    horizon_days: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class Notification(Base, Stamped):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key", name="uq_notification_dedupe"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info|warning|critical
    dedupe_key: Mapped[str] = mapped_column(String(160))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered_channels: Mapped[list] = mapped_column(JSON, default=list)


class AIConversation(Base, Stamped):
    __tablename__ = "ai_conversations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    user_id: Mapped[uuid.UUID] = owner()
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    messages = relationship("AIMessage", back_populates="conversation", cascade="all, delete-orphan",
                            order_by="AIMessage.created_at")


class AIMessage(Base, Stamped):
    __tablename__ = "ai_messages"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ai_conversations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = owner()
    role: Mapped[str] = mapped_column(String(12))  # user|assistant
    content: Mapped[str] = mapped_column(Text)
    structured: Mapped[dict | None] = mapped_column(JSON)
    tool_calls: Mapped[list | None] = mapped_column(JSON)
    conversation = relationship("AIConversation", back_populates="messages")
