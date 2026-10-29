from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class OzonPromotionEntryRecord(OrmBase):
    __tablename__ = "ozon_promotion_entry_operations"
    __table_args__ = (Index("idx_ozon_entry_history", "store_slug", "article", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    article: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonPromotionEntryReservation(OrmBase):
    __tablename__ = "ozon_promotion_entry_reservations"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
