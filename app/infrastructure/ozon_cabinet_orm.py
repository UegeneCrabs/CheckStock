from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class OzonCabinetRecord(OrmBase):
    __tablename__ = "ozon_cabinet_settings"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_by: Mapped[str] = mapped_column(String, nullable=False)


class OzonBuyoutDailyRecord(OrmBase):
    __tablename__ = "ozon_buyout_daily"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonBuyoutRunRecord(OrmBase):
    __tablename__ = "ozon_buyout_runs"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    attempted_at: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
