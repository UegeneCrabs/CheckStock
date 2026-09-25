"""Day-level proof of complete WB source replacement, including empty days."""

from sqlalchemy import Column, String, Table

from app.infrastructure.orm import OrmBase

Table(
    "economics_source_days", OrmBase.metadata,
    *(Column(key, String, primary_key=True) for key in ("marketplace", "store_slug", "source", "day")),
    Column("updated_at", String, nullable=False),
)
