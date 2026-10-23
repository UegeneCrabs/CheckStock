from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class OzonMarginDailyRecord(OrmBase):
    __tablename__ = "ozon_margin_daily"
    __table_args__ = (Index("idx_ozon_margin_product_day", "store_slug", "article", "day"),)

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, primary_key=True)
    observed_at: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
