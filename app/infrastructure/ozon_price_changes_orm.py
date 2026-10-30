from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class OzonPriceChangeRecord(OrmBase):
    __tablename__ = "ozon_price_change_operations"
    __table_args__ = (
        Index("idx_ozon_price_change_history", "store_slug", "article", "user_id", "created_at"),
        Index("idx_ozon_price_change_rate", "store_slug", "article", "sent_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    article: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    sent_at: Mapped[str | None] = mapped_column(String)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
