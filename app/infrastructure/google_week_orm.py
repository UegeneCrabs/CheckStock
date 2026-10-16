from sqlalchemy import Column, String, Table, Text

from app.infrastructure.orm import OrmBase

google_week_marketplaces = Table(
    "google_week_marketplaces",
    OrmBase.metadata,
    Column("marketplace", String, primary_key=True),
    Column("payload", Text, nullable=False),
)
