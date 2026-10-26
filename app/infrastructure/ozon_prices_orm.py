from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class OzonPriceCurrentRecord(OrmBase):
    __tablename__ = "ozon_prices_current"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(String, primary_key=True)
    offer_id: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonPriceDailyRecord(OrmBase):
    __tablename__ = "ozon_prices_daily"
    __table_args__ = (Index("idx_ozon_prices_daily_close", "store_slug", "closed_at", "day"),)

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    offer_id: Mapped[str] = mapped_column(String, nullable=False)
    first_payload: Mapped[str] = mapped_column(Text, nullable=False)
    last_payload: Mapped[str] = mapped_column(Text, nullable=False)
    complete: Mapped[int] = mapped_column(Integer, nullable=False)
    closed_at: Mapped[str | None] = mapped_column(String, nullable=True)
