"""Additive schema: old snapshot tables stay untouched on SQLite and PostgreSQL."""

from sqlalchemy import Column, Index, Integer, String, Table, Text

from app.infrastructure.orm import OrmBase

Table(
    "economics_daily",
    OrmBase.metadata,
    *(Column(key, String, primary_key=True) for key in ("marketplace", "store_slug", "article", "day")),
    Column("revision", Integer, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("updated_at", String, nullable=False),
    Index("idx_economics_daily_period", "marketplace", "store_slug", "day"),
)
Table(
    "economics_daily_events",
    OrmBase.metadata,
    Column("id", String, primary_key=True),
    *(Column(key, String, nullable=False) for key in ("marketplace", "store_slug", "article", "day")),
    Column("revision", Integer, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("actor", String, nullable=False),
    Index("idx_economics_daily_events", "marketplace", "store_slug", "article", "day", "revision"),
)
