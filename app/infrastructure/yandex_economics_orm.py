from sqlalchemy import Column, Integer, String, Table, Text

from app.infrastructure.orm import OrmBase

for table_name, keys, fields in (
    (
        "yandex_economics_settings",
        ("store_slug", "article", "scheme"),
        (
            Column("revision", Integer, nullable=False),
            Column("payload_json", Text, nullable=False),
            Column("updated_at", String, nullable=False),
            Column("updated_by", String, nullable=False),
        ),
    ),
    (
        "yandex_economics_sources",
        ("store_slug", "article", "source"),
        (Column("payload_json", Text, nullable=False), Column("updated_at", String, nullable=False)),
    ),
    (
        "yandex_economics_daily",
        ("store_slug", "article", "scheme", "day"),
        (Column("payload_json", Text, nullable=False), Column("captured_at", String, nullable=False)),
    ),
    (
        "yandex_economics_audit",
        ("id",),
        (
            Column("store_slug", String, nullable=False),
            Column("article", String, nullable=False),
            Column("scheme", String, nullable=False),
            Column("payload_json", Text, nullable=False),
            Column("created_at", String, nullable=False),
            Column("actor", String, nullable=False),
        ),
    ),
):
    Table(table_name, OrmBase.metadata, *(Column(key, String, primary_key=True) for key in keys), *fields)
