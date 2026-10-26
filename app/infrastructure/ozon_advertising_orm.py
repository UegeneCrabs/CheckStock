from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class OzonAdvertisingFactRecord(OrmBase):
    __tablename__ = "ozon_advertising_daily_facts"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonAdvertisingSourceRecord(OrmBase):
    __tablename__ = "ozon_advertising_source_windows"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, primary_key=True)
    period_from: Mapped[str] = mapped_column(String, primary_key=True)
    period_to: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonAdvertisingReportRecord(OrmBase):
    __tablename__ = "ozon_advertising_reports"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    request_key: Mapped[str] = mapped_column(String, primary_key=True)
    uuid: Mapped[str] = mapped_column(String, nullable=False)
    pending: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class OzonAdvertisingResponseRecord(OrmBase):
    __tablename__ = "ozon_advertising_responses"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    request_key: Mapped[str] = mapped_column(String, primary_key=True)
    request_payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class OzonAdvertisingCooldownRecord(OrmBase):
    __tablename__ = "ozon_advertising_cooldowns"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    blocked_until: Mapped[str] = mapped_column(String, nullable=False)


class OzonAdvertisingRequestRecord(OrmBase):
    __tablename__ = "ozon_advertising_requests"
    __table_args__ = (Index("ix_ozon_advertising_requests_store_time", "store_slug", "attempted_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    period_from: Mapped[str | None] = mapped_column(String)
    period_to: Mapped[str | None] = mapped_column(String)
    campaign_count: Mapped[int] = mapped_column(Integer, nullable=False)
    export_units: Mapped[int] = mapped_column(Integer, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String)
    attempted_at: Mapped[str] = mapped_column(String, nullable=False)
