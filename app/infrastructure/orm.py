from __future__ import annotations

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class OrmBase(DeclarativeBase):
    pass


class FulfillmentRecord(OrmBase):
    __tablename__ = "fulfillments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class StockItemRecord(OrmBase):
    __tablename__ = "stock_items"
    __table_args__ = (UniqueConstraint("store_slug", "marketplace", "article"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False, default="WB", server_default="WB")
    article: Mapped[str] = mapped_column(String, nullable=False)
    barcode: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    mp_sku: Mapped[str | None] = mapped_column(String)
    mp_product_id: Mapped[str | None] = mapped_column(String)
    image_url: Mapped[str | None] = mapped_column(String)
    is_service: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[str | None] = mapped_column(String)
    mp_updated_at: Mapped[str | None] = mapped_column(String)


class CatalogProductExclusionRecord(OrmBase):
    __tablename__ = "catalog_product_exclusions"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True)
    nm_id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class FulfillmentStockRecord(OrmBase):
    __tablename__ = "ff_stock"
    __table_args__ = (UniqueConstraint("store_slug", "article", "fulfillment", "marketplace"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    article: Mapped[str] = mapped_column(String, nullable=False)
    fulfillment: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False, default="WB", server_default="WB")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[str | None] = mapped_column(String)


class FulfillmentWarehouseMapRecord(OrmBase):
    __tablename__ = "ff_warehouse_map"
    __table_args__ = (UniqueConstraint("store_slug", "fulfillment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    fulfillment: Mapped[str] = mapped_column(String, nullable=False)
    wb_warehouse_id: Mapped[int] = mapped_column(Integer, nullable=False)
    wb_warehouse_name: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str | None] = mapped_column(String)


class FulfillmentDeliveryRecord(OrmBase):
    __tablename__ = "ff_stock_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    fulfillment: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    sheet_url: Mapped[str | None] = mapped_column(String)
    table_title: Mapped[str] = mapped_column(String, nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    unmatched: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False, default="WB", server_default="WB")


class FulfillmentImportSnapshotRecord(OrmBase):
    __tablename__ = "ff_import_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "store_slug",
            "fulfillment",
            "marketplace",
            "source_type",
            "source_key",
            "article",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    fulfillment: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_key: Mapped[str] = mapped_column(String, nullable=False)
    article: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class TrashStockRecord(OrmBase):
    __tablename__ = "trash_stock"
    __table_args__ = (UniqueConstraint("store_slug", "article", "marketplace", "fulfillment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    article: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    fulfillment: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[str | None] = mapped_column(String)


class SyncHealthRecord(OrmBase):
    __tablename__ = "sync_health"
    __table_args__ = (UniqueConstraint("store_slug", "marketplace", "scope"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    ok: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[str] = mapped_column(String, nullable=False)


class UsedSourceRecord(OrmBase):
    __tablename__ = "used_sources"
    __table_args__ = (UniqueConstraint("store_slug", "kind", "fingerprint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    operation_id: Mapped[int | None] = mapped_column(Integer)
    user_name: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class UserRecord(OrmBase):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    google_email: Mapped[str] = mapped_column(String, nullable=False)
    login: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    can_edit_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    can_manage_users: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    store_access: Mapped[list[UserStoreAccessRecord]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    section_access: Mapped[list[UserSectionAccessRecord]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class UserStoreAccessRecord(OrmBase):
    __tablename__ = "user_store_access"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    user: Mapped[UserRecord] = relationship(back_populates="store_access")


class UserSectionAccessRecord(OrmBase):
    __tablename__ = "user_section_access"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    section: Mapped[str] = mapped_column(String, primary_key=True)
    access_level: Mapped[str] = mapped_column(String, nullable=False)
    user: Mapped[UserRecord] = relationship(back_populates="section_access")


class SessionRecord(OrmBase):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)


class UsageSessionRecord(OrmBase):
    __tablename__ = "user_usage_sessions"
    __table_args__ = (
        Index("idx_user_usage_sessions_user_started", "user_id", "started_at"),
        Index("idx_user_usage_sessions_last_seen", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    last_seen_at: Mapped[str] = mapped_column(String, nullable=False)
    idle_at: Mapped[str | None] = mapped_column(String)
    ended_at: Mapped[str | None] = mapped_column(String)
    active_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_section: Mapped[str | None] = mapped_column(String)
    last_path: Mapped[str | None] = mapped_column(String)


class UserSectionUsageRecord(OrmBase):
    __tablename__ = "user_section_usage"
    __table_args__ = (Index("idx_user_section_usage_date", "usage_date"),)

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    section: Mapped[str] = mapped_column(String, primary_key=True)
    usage_date: Mapped[str] = mapped_column(String, primary_key=True)
    page_views: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    active_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_viewed_at: Mapped[str] = mapped_column(String, nullable=False)


class ActivityLogRecord(OrmBase):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    user_name: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    operation_id: Mapped[int | None] = mapped_column(Integer)


class WbTokenInfoRecord(OrmBase):
    __tablename__ = "wb_token_info"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    expires_at: Mapped[str | None] = mapped_column(String)
    checked_at: Mapped[str] = mapped_column(String, nullable=False)


class FulfillmentTransferRecord(OrmBase):
    __tablename__ = "ff_transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    article: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    from_fulfillment: Mapped[str] = mapped_column(String, nullable=False)
    from_marketplace: Mapped[str] = mapped_column(String, nullable=False)
    to_fulfillment: Mapped[str] = mapped_column(String, nullable=False)
    to_marketplace: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer)
    user_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class StockOperationRecord(OrmBase):
    __tablename__ = "stock_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_name: Mapped[str | None] = mapped_column(String)
    sheet_url: Mapped[str | None] = mapped_column(String)
    from_fulfillment: Mapped[str | None] = mapped_column(String)
    from_marketplace: Mapped[str | None] = mapped_column(String)
    to_fulfillment: Mapped[str | None] = mapped_column(String)
    to_marketplace: Mapped[str | None] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(Integer)
    user_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class StockOperationItemRecord(OrmBase):
    __tablename__ = "stock_operation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("stock_operations.id", ondelete="CASCADE"),
        nullable=False,
    )
    article: Mapped[str] = mapped_column(String, nullable=False)
    barcode: Mapped[str | None] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)


class StockOperationReportFlagRecord(OrmBase):
    __tablename__ = "stock_operation_report_flags"

    operation_id: Mapped[int] = mapped_column(
        ForeignKey("stock_operations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    is_fbs_transfer: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    updated_by: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class ManualSupplyRecord(OrmBase):
    __tablename__ = "manual_supplies"
    __table_args__ = (Index("idx_manual_supplies_delivery_at", "delivery_at", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False, default="", server_default="")
    delivery_at: Mapped[str] = mapped_column(String, nullable=False)
    origin: Mapped[str] = mapped_column(String, nullable=False)
    destination: Mapped[str] = mapped_column(String, nullable=False)
    supply_type: Mapped[str] = mapped_column(String, nullable=False)
    ready: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by_user_id: Mapped[int | None] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class MarketplaceStockRecord(OrmBase):
    __tablename__ = "mp_stock"
    __table_args__ = (UniqueConstraint("store_slug", "article", "marketplace", "scheme"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    article: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    scheme: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[str | None] = mapped_column(String)


class MarketplaceStockDailyHistoryRecord(OrmBase):
    __tablename__ = "marketplace_stock_daily_history"
    __table_args__ = (Index("idx_mp_stock_daily_lookup", "store_slug", "marketplace", "article", "day"),)

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, primary_key=True)
    scheme: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    captured_at: Mapped[str] = mapped_column(String, nullable=False)


class FulfillmentStockDailyHistoryRecord(OrmBase):
    __tablename__ = "fulfillment_stock_daily_history"
    __table_args__ = (Index("idx_ff_stock_daily_lookup", "store_slug", "marketplace", "article", "day"),)

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, primary_key=True)
    fulfillment: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    captured_at: Mapped[str] = mapped_column(String, nullable=False)


class MarketplaceWarehouseStockRecord(OrmBase):
    __tablename__ = "mp_warehouse_stock"
    __table_args__ = (UniqueConstraint("store_slug", "article", "marketplace", "scheme", "warehouse"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    article: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    scheme: Mapped[str] = mapped_column(String, nullable=False)
    warehouse: Mapped[str] = mapped_column(String, nullable=False)
    cluster: Mapped[str | None] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[str | None] = mapped_column(String)


class MarketplaceWarehouseClusterRecord(OrmBase):
    __tablename__ = "mp_warehouse_cluster"
    __table_args__ = (UniqueConstraint("marketplace", "warehouse"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    warehouse: Mapped[str] = mapped_column(String, nullable=False)
    cluster: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str | None] = mapped_column(String)


class StockAuditRandomizationRecord(OrmBase):
    __tablename__ = "stock_audit_randomizations"
    __table_args__ = (
        UniqueConstraint("month_key", "article"),
        Index("idx_stock_audit_randomizations_batch", "batch_key"),
        Index("idx_stock_audit_randomizations_month", "month_key", "fulfillment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_key: Mapped[str] = mapped_column(String(32), nullable=False)
    month_key: Mapped[str] = mapped_column(String(7), nullable=False)
    fulfillment: Mapped[str] = mapped_column(String, nullable=False)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    article: Mapped[str] = mapped_column(String, nullable=False)
    ff_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    fbs_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer)
    user_name: Mapped[str] = mapped_column(String, nullable=False)
    generated_at: Mapped[str] = mapped_column(String, nullable=False)


class SalesOrderLineRecord(OrmBase):
    __tablename__ = "sales_order_lines"
    __table_args__ = (
        Index("idx_sales_order_lines_ordered", "marketplace", "store_slug", "ordered_at"),
        Index("idx_sales_order_lines_sold", "marketplace", "store_slug", "sold_at"),
        Index("idx_sales_order_lines_returned", "marketplace", "store_slug", "returned_at"),
    )

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True)
    order_key: Mapped[str] = mapped_column(String, primary_key=True)
    line_key: Mapped[str] = mapped_column(String, primary_key=True)
    external_order_id: Mapped[str | None] = mapped_column(String)
    scheme: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str | None] = mapped_column(String)
    substatus: Mapped[str | None] = mapped_column(String)
    article: Mapped[str | None] = mapped_column(String)
    barcode: Mapped[str | None] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String)
    ordered_at: Mapped[str] = mapped_column(String, nullable=False)
    source_updated_at: Mapped[str | None] = mapped_column(String)
    cancelled_at: Mapped[str | None] = mapped_column(String)
    sold_at: Mapped[str | None] = mapped_column(String)
    returned_at: Mapped[str | None] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cancelled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sold_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    return_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    order_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    cancelled_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    sale_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    return_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String, nullable=False, default="RUB", server_default="RUB")
    raw_json: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[str] = mapped_column(String, nullable=False)


class SalesSyncStateRecord(OrmBase):
    __tablename__ = "sales_sync_state"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True)
    last_attempt_at: Mapped[str] = mapped_column(String, nullable=False)
    last_success_at: Mapped[str | None] = mapped_column(String)
    ok: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    error: Mapped[str | None] = mapped_column(Text)
    rows_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lookback_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90, server_default="90")


class WbFunnelDailyOrderRecord(OrmBase):
    __tablename__ = "wb_funnel_daily_orders"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, primary_key=True)
    vendor_code: Mapped[str] = mapped_column(String, nullable=False, default="", server_default="")
    product_name: Mapped[str] = mapped_column(String, nullable=False, default="", server_default="")
    orders_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    orders_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class WbFunnelOrdersSyncStateRecord(OrmBase):
    __tablename__ = "wb_funnel_orders_sync_state"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    last_attempt_at: Mapped[str] = mapped_column(String, nullable=False)
    last_success_at: Mapped[str | None] = mapped_column(String)
    records: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)


class StockSheetExportSettingRecord(OrmBase):
    __tablename__ = "stock_sheet_export_settings"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    schedule_kind: Mapped[str] = mapped_column(
        String, nullable=False, default="weekly", server_default="weekly"
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False, default=6, server_default="6")
    run_time: Mapped[str] = mapped_column(String, nullable=False, default="01:00", server_default="01:00")
    spreadsheet_url: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    last_attempt_at: Mapped[str | None] = mapped_column(String)
    last_success_at: Mapped[str | None] = mapped_column(String)
    last_error: Mapped[str | None] = mapped_column(Text)


class StockSheetExportMarketplaceRecord(OrmBase):
    __tablename__ = "stock_sheet_export_marketplaces"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True)
    spreadsheet_url: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class StockSheetExportTargetRecord(OrmBase):
    __tablename__ = "stock_sheet_export_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    metric: Mapped[str] = mapped_column(String, nullable=False)
    sheet_name: Mapped[str] = mapped_column(String, nullable=False)
    key_column_name: Mapped[str] = mapped_column(String, nullable=False)
    value_column_name: Mapped[str] = mapped_column(String, nullable=False)


class UnitEconomics1CCabinetSettingRecord(OrmBase):
    __tablename__ = "unit_economics_1c_cabinet_settings"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True, default="WB", server_default="WB")
    acceptance_coefficient: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    wb_extra_tariff_percent: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    acquiring_percent: Mapped[float] = mapped_column(Float, nullable=False, default=3.8, server_default="3.8")
    team_commission_percent: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    vat_percent: Mapped[float] = mapped_column(Float, nullable=False, default=9, server_default="9")
    usn_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    osno_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    tax_system: Mapped[str] = mapped_column(String, nullable=False, default="usn", server_default="usn")
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by_name: Mapped[str] = mapped_column(String, nullable=False)


class UnitEconomics1CProductSettingRecord(OrmBase):
    __tablename__ = "unit_economics_1c_product_settings"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True, default="WB", server_default="WB")
    article: Mapped[str] = mapped_column(String, primary_key=True)
    delivery_wb_rub: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    buyout_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    return_cost_rub: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    volume_l: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    storage_wb_rub: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by_name: Mapped[str] = mapped_column(String, nullable=False)


class UnitEconomics1CProductClassificationRecord(OrmBase):
    __tablename__ = "unit_economics_1c_product_classifications"

    stock_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("stock_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    abc_code: Mapped[str | None] = mapped_column(String)
    turnover_days: Mapped[int] = mapped_column(Integer, nullable=False, default=21, server_default="21")
    source_article: Mapped[str | None] = mapped_column(String)
    source_barcode: Mapped[str | None] = mapped_column(String)
    source_row: Mapped[int | None] = mapped_column(Integer)
    synced_at: Mapped[str] = mapped_column(String, nullable=False)


class UnitEconomics1CSourceValueRecord(OrmBase):
    __tablename__ = "unit_economics_1c_source_values"

    stock_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("stock_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    purchase_price: Mapped[float | None] = mapped_column(Float)
    fulfillment_cost: Mapped[float | None] = mapped_column(Float)
    team_commission_percent: Mapped[float | None] = mapped_column(Float)
    tag_raw: Mapped[str | None] = mapped_column(Text)
    goal_week: Mapped[float | None] = mapped_column(Float)
    goal_day: Mapped[float | None] = mapped_column(Float)
    stock_status: Mapped[str | None] = mapped_column(String)
    stock_end_week: Mapped[str | None] = mapped_column(String)
    supplier_external_raw: Mapped[str | None] = mapped_column(Text)
    abc_code: Mapped[str | None] = mapped_column(String)
    fact_sales: Mapped[float | None] = mapped_column(Float)
    plan_sales: Mapped[float | None] = mapped_column(Float)
    source_sheet_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sheet_title: Mapped[str] = mapped_column(String, nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    synced_at: Mapped[str] = mapped_column(String, nullable=False)


class UnitEconomics1CProductCategoryRecord(OrmBase):
    __tablename__ = "unit_economics_1c_product_categories"

    stock_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("stock_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    wb_subject_id: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[str | None] = mapped_column(String)
    category_key: Mapped[str | None] = mapped_column(String, index=True)
    synced_at: Mapped[str] = mapped_column(String, nullable=False)


class UnitEconomics1CWBCommissionRecord(OrmBase):
    __tablename__ = "unit_economics_1c_wb_commissions"

    category_key: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str] = mapped_column(String, nullable=False)
    commission_percent: Mapped[float] = mapped_column(Float, nullable=False)
    synced_at: Mapped[str] = mapped_column(String, nullable=False)


class UnitEconomics1CDailyPriceRecord(OrmBase):
    __tablename__ = "unit_economics_1c_wb_daily_prices"
    __table_args__ = (
        Index("idx_ue1c_wb_price_latest", "store_slug", "article", "day"),
        Index("idx_ue1c_wb_price_nm", "store_slug", "nm_id", "day"),
    )

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, nullable=False, default="WB", server_default="WB")
    nm_id: Mapped[str] = mapped_column(String, nullable=False)
    size_id: Mapped[int | None] = mapped_column(Integer)
    tech_size_name: Mapped[str | None] = mapped_column(String)
    vendor_code: Mapped[str | None] = mapped_column(String)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="RUB", server_default="RUB")
    seller_base_price: Mapped[float | None] = mapped_column(Float)
    retail_price: Mapped[float | None] = mapped_column(Float)
    club_discounted_price: Mapped[float | None] = mapped_column(Float)
    customer_price_with_spp: Mapped[float | None] = mapped_column(Float)
    customer_price_with_wallet: Mapped[float | None] = mapped_column(Float)
    customer_price_window_days: Mapped[int | None] = mapped_column(Integer)
    customer_price_orders_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_order_at: Mapped[str | None] = mapped_column(String)
    orders_synced_at: Mapped[str | None] = mapped_column(String)
    retail_synced_at: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class UnitEconomics1CPriceSyncStateRecord(OrmBase):
    __tablename__ = "unit_economics_1c_wb_price_sync_state"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True, default="WB", server_default="WB")
    status: Mapped[str] = mapped_column(String, nullable=False)
    orders_ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    retail_ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_attempt_at: Mapped[str] = mapped_column(String, nullable=False)
    last_success_at: Mapped[str | None] = mapped_column(String)
    rows_saved: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)


class UnitEconomics1CDailyAdvertisingRecord(OrmBase):
    __tablename__ = "unit_economics_1c_wb_daily_advertising"
    __table_args__ = (Index("idx_ue1c_wb_ad_period", "store_slug", "day", "nm_id"),)

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    nm_id: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, nullable=False, default="WB", server_default="WB")
    spend: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    synced_at: Mapped[str] = mapped_column(String, nullable=False)


class UnitEconomics1CAdvertisingSyncStateRecord(OrmBase):
    __tablename__ = "unit_economics_1c_wb_advertising_sync_state"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True, default="WB", server_default="WB")
    status: Mapped[str] = mapped_column(String, nullable=False)
    period_from: Mapped[str] = mapped_column(String, nullable=False)
    period_to: Mapped[str] = mapped_column(String, nullable=False)
    last_attempt_at: Mapped[str] = mapped_column(String, nullable=False)
    last_success_at: Mapped[str | None] = mapped_column(String)
    rows_saved: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    campaigns_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)


class RnpStrategyRecord(OrmBase):
    __tablename__ = "rnp_strategies"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, primary_key=True)
    strategy: Mapped[str] = mapped_column(Text, nullable=False)
    date_from: Mapped[str] = mapped_column(String, nullable=False)
    date_to: Mapped[str] = mapped_column(String, nullable=False)
    updated_by: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class RnpActionRecord(OrmBase):
    __tablename__ = "rnp_action_log"
    __table_args__ = (Index("idx_rnp_action_lookup", "store_slug", "marketplace", "article", "action_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_slug: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, nullable=False)
    article: Mapped[str] = mapped_column(String, nullable=False)
    action_date: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer)
    user_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class DecisionMetricRecord(OrmBase):
    __tablename__ = "wb_decision_metrics"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    nm_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_name: Mapped[str | None] = mapped_column(String)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    carts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    buyouts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cancels: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    order_sum: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    rating: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    delivery_days: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    order_growth: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    avg_position: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    visibility: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    search_views: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    search_carts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    search_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    search_growth: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    estimated_reach: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ad_impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ad_clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ad_spend: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    ad_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ad_position: Mapped[float] = mapped_column(Float, nullable=False, default=0, server_default="0")
    funnel_synced_at: Mapped[str | None] = mapped_column(String)
    search_synced_at: Mapped[str | None] = mapped_column(String)
    advertising_synced_at: Mapped[str | None] = mapped_column(String)


class DecisionSyncStateRecord(OrmBase):
    __tablename__ = "wb_decision_sync_state"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    last_attempt_at: Mapped[str] = mapped_column(String, nullable=False)
    last_success_at: Mapped[str | None] = mapped_column(String)
    records: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class DecisionActionRecord(OrmBase):
    __tablename__ = "wb_decision_actions"

    fingerprint: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="new", server_default="new")
    user_id: Mapped[int | None] = mapped_column(Integer)
    user_name: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class RnpDailyMetricRecord(OrmBase):
    __tablename__ = "rnp_daily_metrics"
    __table_args__ = (Index("idx_rnp_daily_lookup", "store_slug", "marketplace", "day", "article"),)

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True)
    article: Mapped[str] = mapped_column(String, primary_key=True)
    day: Mapped[str] = mapped_column(String, primary_key=True)
    traffic_clicks: Mapped[int | None] = mapped_column(Integer)
    traffic_carts: Mapped[int | None] = mapped_column(Integer)
    traffic_orders: Mapped[int | None] = mapped_column(Integer)
    buyout_count: Mapped[int | None] = mapped_column(Integer)
    buyout_amount: Mapped[float | None] = mapped_column(Float)
    buyout_percent: Mapped[float | None] = mapped_column(Float)
    return_count: Mapped[int | None] = mapped_column(Integer)
    return_amount: Mapped[float | None] = mapped_column(Float)
    ad_spend: Mapped[float | None] = mapped_column(Float)
    ad_media: Mapped[float | None] = mapped_column(Float)
    ad_internal: Mapped[float | None] = mapped_column(Float)
    ad_external: Mapped[float | None] = mapped_column(Float)
    ad_impressions: Mapped[int | None] = mapped_column(Integer)
    ad_clicks: Mapped[int | None] = mapped_column(Integer)
    ad_carts: Mapped[int | None] = mapped_column(Integer)
    ad_orders: Mapped[int | None] = mapped_column(Integer)
    ad_sales_amount: Mapped[float | None] = mapped_column(Float)
    unified_impressions: Mapped[int | None] = mapped_column(Integer)
    unified_clicks: Mapped[int | None] = mapped_column(Integer)
    unified_spend: Mapped[float | None] = mapped_column(Float)
    unified_orders: Mapped[int | None] = mapped_column(Integer)
    unified_carts: Mapped[int | None] = mapped_column(Integer)
    manual_search_impressions: Mapped[int | None] = mapped_column(Integer)
    manual_search_clicks: Mapped[int | None] = mapped_column(Integer)
    manual_search_spend: Mapped[float | None] = mapped_column(Float)
    manual_search_orders: Mapped[int | None] = mapped_column(Integer)
    manual_search_carts: Mapped[int | None] = mapped_column(Integer)
    manual_recommendations_impressions: Mapped[int | None] = mapped_column(Integer)
    manual_recommendations_clicks: Mapped[int | None] = mapped_column(Integer)
    manual_recommendations_spend: Mapped[float | None] = mapped_column(Float)
    manual_recommendations_orders: Mapped[int | None] = mapped_column(Integer)
    manual_recommendations_carts: Mapped[int | None] = mapped_column(Integer)
    cpc_search_impressions: Mapped[int | None] = mapped_column(Integer)
    cpc_search_clicks: Mapped[int | None] = mapped_column(Integer)
    cpc_search_spend: Mapped[float | None] = mapped_column(Float)
    cpc_search_orders: Mapped[int | None] = mapped_column(Integer)
    cpc_search_carts: Mapped[int | None] = mapped_column(Integer)
    self_purchase_count: Mapped[int | None] = mapped_column(Integer)
    self_purchase_amount: Mapped[float | None] = mapped_column(Float)
    price_before_spp: Mapped[float | None] = mapped_column(Float)
    price_after_spp: Mapped[float | None] = mapped_column(Float)
    spp_percent: Mapped[float | None] = mapped_column(Float)
    stock_units: Mapped[int | None] = mapped_column(Integer)
    stock_value: Mapped[float | None] = mapped_column(Float)
    stock_total: Mapped[int | None] = mapped_column(Integer)
    stock_velocity_7d: Mapped[float | None] = mapped_column(Float)
    stock_turnover_days: Mapped[float | None] = mapped_column(Float)
    stock_depletion_date: Mapped[str | None] = mapped_column(String)
    stock_to_client: Mapped[int | None] = mapped_column(Integer)
    stock_from_client: Mapped[int | None] = mapped_column(Integer)
    stock_regions: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[float | None] = mapped_column(Float)
    reviews_count: Mapped[int | None] = mapped_column(Integer)
    reviews_delta: Mapped[int | None] = mapped_column(Integer)
    reviews_1: Mapped[int | None] = mapped_column(Integer)
    reviews_2: Mapped[int | None] = mapped_column(Integer)
    plan_orders_amount: Mapped[float | None] = mapped_column(Float)
    plan_orders_count: Mapped[int | None] = mapped_column(Integer)
    plan_buyouts_amount: Mapped[float | None] = mapped_column(Float)
    plan_buyouts_count: Mapped[int | None] = mapped_column(Integer)
    plan_ad_budget: Mapped[float | None] = mapped_column(Float)
    plan_drr: Mapped[float | None] = mapped_column(Float)
    plan_margin: Mapped[float | None] = mapped_column(Float)
    plan_roi: Mapped[float | None] = mapped_column(Float)
    plan_profit: Mapped[float | None] = mapped_column(Float)
    funnel_synced_at: Mapped[str | None] = mapped_column(String)
    advertising_synced_at: Mapped[str | None] = mapped_column(String)
    snapshot_synced_at: Mapped[str | None] = mapped_column(String)


class RnpMetricSyncStateRecord(OrmBase):
    __tablename__ = "rnp_metric_sync_state"

    store_slug: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, primary_key=True)
    period_from: Mapped[str] = mapped_column(String, nullable=False)
    period_to: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    last_attempt_at: Mapped[str] = mapped_column(String, nullable=False)
    last_success_at: Mapped[str | None] = mapped_column(String)
    rows_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
