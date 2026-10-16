from sqlalchemy import Column, String, Table, Text

from app.infrastructure.orm import OrmBase

economics_source_days = Table(
    "economics_source_days",
    OrmBase.metadata,
    *(Column(key, String, primary_key=True) for key in ("marketplace", "store_slug", "source", "day")),
    Column("updated_at", String, nullable=False),
)

ozon_order_days = Table(
    "ozon_order_days",
    OrmBase.metadata,
    *(Column(key, String, primary_key=True) for key in ("store_slug", "scheme", "day")),
    Column("payload", Text, nullable=False),
)
