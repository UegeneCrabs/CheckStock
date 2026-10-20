"""Real source documents and calculated standard-cost reports."""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class WbStandardCostsSnapshot(OrmBase):
    __tablename__ = "wb_standard_costs_snapshots"

    store_slug: Mapped[str] = mapped_column(String(80), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String(40))


class WbStandardCostsSource(OrmBase):
    __tablename__ = "wb_standard_costs_sources"

    store_slug: Mapped[str] = mapped_column(String(80), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String(40))
