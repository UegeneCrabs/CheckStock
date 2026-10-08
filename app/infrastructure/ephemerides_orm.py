"""Additive tables: weekly notes, their revisions and saved WB customer transit."""

from sqlalchemy import Column, Integer, String, Table, Text

from app.infrastructure.orm import OrmBase

for name, audit in (("ephemerides_comments", False), ("ephemerides_comment_revisions", True)):
    Table(
        name,
        OrmBase.metadata,
        *(Column(key, String, primary_key=True) for key in ("store_slug", "article", "week", "kind")),
        Column("version", Integer, primary_key=audit, nullable=False),
        Column("text", Text, nullable=False),
        Column("author_id", Integer, nullable=False),
        Column("author", String, nullable=False),
        Column("updated_at", String, nullable=False),
    )

Table(
    "wb_customer_transit",
    OrmBase.metadata,
    Column("store_slug", String, primary_key=True),
    Column("nm_id", String, primary_key=True),
    Column("from_customer", Integer, nullable=False),
    Column("to_customer", Integer, nullable=False),
    Column("updated_at", String, nullable=False),
)
