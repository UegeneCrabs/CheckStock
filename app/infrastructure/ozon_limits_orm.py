from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class OzonApiControlRecord(OrmBase):
    __tablename__ = "ozon_api_control"

    scope: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonPerformanceBudgetRecord(OrmBase):
    __tablename__ = "ozon_performance_budget"
    __table_args__ = (
        Index("idx_ozon_budget_account_time", "account_scope", "attempted_at"),
        Index("idx_ozon_budget_organization_time", "organization_scope", "attempted_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    account_scope: Mapped[str] = mapped_column(String, nullable=False)
    organization_scope: Mapped[str] = mapped_column(String, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    export_units: Mapped[int] = mapped_column(Integer, nullable=False)
    http_units: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    request_key: Mapped[str | None] = mapped_column(String)


class OzonPerformanceLeaseRecord(OrmBase):
    __tablename__ = "ozon_performance_leases"
    __table_args__ = (UniqueConstraint("account_scope", name="uq_ozon_report_account"),)

    token: Mapped[str] = mapped_column(String, primary_key=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    account_scope: Mapped[str] = mapped_column(String, nullable=False)
    organization_scope: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
