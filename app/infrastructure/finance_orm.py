"""Finance has its own immutable revisions; no foreign key into the sales ledger."""

from datetime import date

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class FinanceConnection(OrmBase):
    __tablename__ = "finance_yandex_connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    store_slug: Mapped[str] = mapped_column(String(100), index=True)
    business_id: Mapped[int] = mapped_column(Integer)
    campaign_ids: Mapped[list] = mapped_column(JSON)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(200))


class FinanceSnapshot(OrmBase):
    __tablename__ = "finance_yandex_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("finance_yandex_connections.id"), index=True)
    source: Mapped[str] = mapped_column(String(30))
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
    raw: Mapped[dict | None] = mapped_column(JSON)
    report_ids: Mapped[list] = mapped_column(JSON)
    issues: Mapped[list] = mapped_column(JSON)
    income_issues: Mapped[list] = mapped_column(JSON)
    captured_at: Mapped[str] = mapped_column(String(40))
    formula_version: Mapped[str] = mapped_column(String(60))
    fingerprint: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(200))


class FinanceHead(OrmBase):
    __tablename__ = "finance_yandex_heads"
    connection_id: Mapped[str] = mapped_column(ForeignKey("finance_yandex_connections.id"), primary_key=True)
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("finance_yandex_snapshots.id"))


class FinanceOperation(OrmBase):
    __tablename__ = "finance_yandex_operations"
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("finance_yandex_snapshots.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    day: Mapped[date] = mapped_column(Date)
    payload: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (Index("ix_finance_yandex_operation_day", "day", "snapshot_id"),)


class FinanceAttempt(OrmBase):
    __tablename__ = "finance_yandex_attempts"
    connection_id: Mapped[str] = mapped_column(ForeignKey("finance_yandex_connections.id"), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    attempted_at: Mapped[str] = mapped_column(String(40))
    error: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(20), default="success")


class FinanceTicket(OrmBase):
    __tablename__ = "finance_yandex_report_tickets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[str] = mapped_column(String(40))


class FinanceCost(OrmBase):
    __tablename__ = "finance_yandex_costs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String(100), index=True)
    article: Mapped[str] = mapped_column(String(250))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    price: Mapped[str] = mapped_column(String(40))
    origin: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40))


class FinanceUnallocated(OrmBase):
    __tablename__ = "finance_yandex_unallocated"
    business_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    captured_at: Mapped[str] = mapped_column(String(40))
