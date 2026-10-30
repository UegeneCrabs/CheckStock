from sqlalchemy import Column, Integer, String, Table, Text

from app.infrastructure.orm import OrmBase

Table(
    "yandex_analytics_funnel_days",
    OrmBase.metadata,
    Column("store_slug", String, primary_key=True),
    Column("source_date", String, primary_key=True),
    Column("payload_json", Text, nullable=False),
    Column("received_at", String, nullable=False),
)

for name, audit in (("yandex_analytics_comments", False), ("yandex_analytics_comment_revisions", True)):
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
    "yandex_analytics_settings",
    OrmBase.metadata,
    Column("store_slug", String, primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("payload_json", Text, nullable=False),
)

Table(
    "yandex_analytics_customer_transit",
    OrmBase.metadata,
    Column("store_slug", String, primary_key=True),
    Column("payload_json", Text),
    Column("last_success_at", String),
    Column("last_attempt_at", String, nullable=False),
    Column("error", String),
)

Table(
    "yandex_analytics_stock_snapshots",
    OrmBase.metadata,
    Column("store_slug", String, primary_key=True),
    Column("week", String, primary_key=True),
    Column("payload_json", Text, nullable=False),
    Column("captured_at", String, nullable=False),
)
