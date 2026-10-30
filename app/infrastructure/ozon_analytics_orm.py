from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.orm import OrmBase


class OzonAnalyticsCommentRecord(OrmBase):
    __tablename__ = "ozon_analytics_comments"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, primary_key=True)
    week: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    author_id: Mapped[int | None] = mapped_column(Integer)
    author: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class OzonAnalyticsCommentRevisionRecord(OrmBase):
    __tablename__ = "ozon_analytics_comment_revisions"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, primary_key=True)
    week: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    previous_text: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[int | None] = mapped_column(Integer)
    author: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class OzonAnalyticsStockSnapshotRecord(OrmBase):
    __tablename__ = "ozon_analytics_stock_snapshots"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    week: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonAnalyticsReferenceDailyRecord(OrmBase):
    __tablename__ = "ozon_analytics_reference_daily"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonAnalyticsTransitRecord(OrmBase):
    __tablename__ = "ozon_analytics_transit"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonAnalyticsSourceStatusRecord(OrmBase):
    __tablename__ = "ozon_analytics_source_status"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, primary_key=True)
    source_day: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonAnalyticsFunnelDailyRecord(OrmBase):
    __tablename__ = "ozon_analytics_funnel_daily"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    source_day: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class OzonAnalyticsFunnelRunRecord(OrmBase):
    __tablename__ = "ozon_analytics_funnel_runs"
    __table_args__ = (Index("idx_ozon_analytics_funnel_account", "account", "updated_at"),)

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    source_day: Mapped[str] = mapped_column(String, primary_key=True)
    account: Mapped[str] = mapped_column(String, nullable=False)
    generation: Mapped[str] = mapped_column(String, nullable=False)
    queue: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    offset: Mapped[int] = mapped_column(Integer, nullable=False)
    page_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String)
    next_retry_at: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String)
    lease_until: Mapped[str | None] = mapped_column(String)
    raw_payload: Mapped[str | None] = mapped_column(Text)


class OzonAnalyticsFunnelStagingRecord(OrmBase):
    __tablename__ = "ozon_analytics_funnel_staging"

    generation: Mapped[str] = mapped_column(String, primary_key=True)
    sku: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, nullable=False)
    carts: Mapped[int] = mapped_column(Integer, nullable=False)


class OzonAnalyticsAttemptRecord(OrmBase):
    __tablename__ = "ozon_analytics_attempts"
    __table_args__ = (Index("idx_ozon_analytics_attempt_window", "account", "attempted_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    account: Mapped[str] = mapped_column(String, nullable=False)
    attempted_at: Mapped[str] = mapped_column(String, nullable=False)


class OzonAnalyticsAccountRecord(OrmBase):
    __tablename__ = "ozon_analytics_accounts"

    account: Mapped[str] = mapped_column(String, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    next_retry_at: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(String)
    lease_token: Mapped[str | None] = mapped_column(String)
    lease_until: Mapped[str | None] = mapped_column(String)
    last_queue: Mapped[str | None] = mapped_column(String)
