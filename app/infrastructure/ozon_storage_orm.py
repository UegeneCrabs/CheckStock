from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class OzonStorageDailyRecord(OrmBase):
    __tablename__ = "ozon_storage_daily"
    __table_args__ = (Index("idx_ozon_storage_product_day", "store_slug", "article", "day"),)

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, primary_key=True)
    period_from: Mapped[str] = mapped_column(String, nullable=False)
    period_to: Mapped[str] = mapped_column(String, nullable=False)
    observed_at: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonStorageRunRecord(OrmBase):
    __tablename__ = "ozon_storage_runs"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    attempted_at: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class OzonStorageReportRecord(OrmBase):
    __tablename__ = "ozon_storage_reports"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
