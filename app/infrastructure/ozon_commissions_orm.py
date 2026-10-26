from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class OzonFboCommissionCurrentRecord(OrmBase):
    __tablename__ = "ozon_fbo_commissions_current"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(String, primary_key=True)
    offer_id: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonFboCommissionDailyRecord(OrmBase):
    __tablename__ = "ozon_fbo_commissions_daily"
    __table_args__ = (Index("idx_ozon_fbo_commissions_daily_close", "store_slug", "closed_at", "day"),)

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    offer_id: Mapped[str] = mapped_column(String, nullable=False)
    first_payload: Mapped[str] = mapped_column(Text, nullable=False)
    last_payload: Mapped[str] = mapped_column(Text, nullable=False)
    closed_at: Mapped[str | None] = mapped_column(String, nullable=True)
