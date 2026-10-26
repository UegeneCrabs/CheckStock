from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class FboSupplyMovementRecord(OrmBase):
    __tablename__ = "fbo_supply_movements"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True)
    supply_key: Mapped[str] = mapped_column(String, primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class FboSupplyBatchRecord(OrmBase):
    __tablename__ = "fbo_supply_batches"

    batch_id: Mapped[int] = mapped_column(ForeignKey("ff_transit_batches.id"), primary_key=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False, index=True)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    supply_key: Mapped[str] = mapped_column(String, nullable=False)
    fulfillment: Mapped[str] = mapped_column(String, nullable=False)
