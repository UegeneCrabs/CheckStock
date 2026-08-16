import ast

from sqlalchemy import inspect, select

from app.infrastructure.database import Database, DatabaseConnection, database_for_path
from app.infrastructure.orm import FulfillmentRecord, OrmBase, StockItemRecord
from app.repositories import core
from app.repositories.seed_data import FULFILLMENTS, STOCK_ITEMS
from app.repositories.stock_sheet_export import MARKETPLACES
from app.stores import STORES


def init_db() -> None:
    database = database_for_path(core.DB_PATH)
    _preserve_legacy_wb_funnel_daily_orders(database)
    target_table_was_rebuilt = _prepare_stock_sheet_export_target_migration(database)
    OrmBase.metadata.create_all(database.engine)
    if target_table_was_rebuilt:
        _finish_stock_sheet_export_target_migration(database)
    _backfill_stock_sheet_export_marketplace_urls(database)
    if database.dialect_name != "sqlite":
        return
    with core.get_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        _migrate_stock_to_unified(connection)
        _migrate_ff_stock_marketplace(connection)
        _migrate_catalog_marketplace(connection)
        _migrate_operation_note(connection)
        _migrate_user_permissions(connection)
        _migrate_trash_checked(connection)
        _migrate_mp_updated_at(connection)
        _migrate_image_url(connection)
        _migrate_delivery_marketplace(connection)
        _migrate_activity_log_operation(connection)
        connection.execute("PRAGMA optimize")
        connection.commit()


def _preserve_legacy_wb_funnel_daily_orders(database: Database) -> None:
    """Keep the pre-product aggregate table instead of silently losing its data.

    The first local prototype stored one total per store and day. Product-level
    export requires a separate row for every WB nmId, so the primary key and
    product columns are different. This one-time rename lets metadata.create_all
    create the correct table on both SQLite and PostgreSQL.
    """

    inspector = inspect(database.engine)
    table_name = "wb_funnel_daily_orders"
    legacy_name = "wb_funnel_daily_orders_legacy"
    if not inspector.has_table(table_name):
        return
    columns = {str(column["name"]) for column in inspector.get_columns(table_name)}
    required = {"article", "vendor_code", "product_name"}
    if required.issubset(columns):
        return
    if inspector.has_table(legacy_name):
        raise RuntimeError(
            "Найдена устаревшая таблица воронки. Сохраните её резервную копию и переименуйте "
            "wb_funnel_daily_orders_legacy перед обновлением."
        )
    with database.connect() as connection:
        connection.execute(f"ALTER TABLE {table_name} RENAME TO {legacy_name}")
        connection.commit()


def _backfill_stock_sheet_export_marketplace_urls(database: Database) -> None:
    """Copy the former store-wide URL into each marketplace on first upgrade."""

    with database.connect() as connection:
        settings_rows = connection.execute(
            "SELECT store_slug, spreadsheet_url FROM stock_sheet_export_settings"
        ).fetchall()
        existing_rows = connection.execute(
            "SELECT store_slug, marketplace FROM stock_sheet_export_marketplaces"
        ).fetchall()
        existing = {(str(row["store_slug"]), str(row["marketplace"])) for row in existing_rows}
        missing = [
            (str(row["store_slug"]), marketplace, str(row["spreadsheet_url"] or ""))
            for row in settings_rows
            for marketplace in MARKETPLACES
            if (str(row["store_slug"]), marketplace) not in existing
        ]
        if not missing:
            return
        connection.executemany(
            """
            INSERT INTO stock_sheet_export_marketplaces
                (store_slug, marketplace, spreadsheet_url)
            VALUES (?, ?, ?)
            """,
            missing,
        )
        connection.commit()


def _prepare_stock_sheet_export_target_migration(database: Database) -> bool:
    """Allow more than one export target for the same metric.

    Earlier versions used (store, marketplace, metric) as the primary key.  A
    separate export row needs its own article column, so the replacement table
    has an autoincrementing id instead.  The old configuration is copied after
    SQLAlchemy creates the new table.
    """
    inspector = inspect(database.engine)
    table_name = "stock_sheet_export_targets"
    pending_name = "stock_sheet_export_targets_migration_v1"
    backup_name = "stock_sheet_export_targets_backup_v1"
    if not inspector.has_table(table_name):
        if inspector.has_table(pending_name):
            return True
        if inspector.has_table(backup_name):
            raise RuntimeError(
                "Не найдена рабочая таблица настроек выгрузки, но сохранена её резервная копия. "
                "Остановите приложение и восстановите stock_sheet_export_targets."
            )
        return False
    columns = {str(column["name"]) for column in inspector.get_columns(table_name)}
    if "id" in columns:
        return inspector.has_table(pending_name)
    if inspector.has_table(pending_name) or inspector.has_table(backup_name):
        raise RuntimeError(
            "Найдена незавершённая миграция настроек выгрузки. "
            "Сделайте резервную копию базы и обратитесь в поддержку."
        )
    with database.connect() as connection:
        connection.execute(f"ALTER TABLE {table_name} RENAME TO {pending_name}")
        connection.commit()
    return True


def _finish_stock_sheet_export_target_migration(database: Database) -> None:
    table_name = "stock_sheet_export_targets"
    pending_name = "stock_sheet_export_targets_migration_v1"
    backup_name = "stock_sheet_export_targets_backup_v1"
    columns = (
        "store_slug",
        "marketplace",
        "metric",
        "sheet_name",
        "key_column_name",
        "value_column_name",
    )
    selected_columns = ", ".join(columns)
    order_by = ", ".join(columns)
    inspector = inspect(database.engine)
    if not inspector.has_table(pending_name):
        raise RuntimeError("Не найдена исходная таблица для переноса настроек Google-выгрузки")
    if inspector.has_table(backup_name):
        raise RuntimeError(
            "Резервная таблица stock_sheet_export_targets_backup_v1 уже существует; "
            "автоматическая миграция остановлена."
        )
    with database.connect() as connection:
        table_columns = connection.column_names(table_name)
        if table_columns and "id" not in table_columns:
            raise RuntimeError("Новая таблица настроек выгрузки создана в неожиданном формате")

        source_rows = connection.execute(
            f"SELECT {selected_columns} FROM {pending_name} ORDER BY {order_by}"
        ).fetchall()
        target_rows = connection.execute(
            f"SELECT {selected_columns} FROM {table_name} ORDER BY {order_by}"
        ).fetchall()
        if not target_rows:
            connection.execute(
                f"""
                INSERT INTO {table_name} ({selected_columns})
                SELECT {selected_columns} FROM {pending_name}
                """
            )
            target_rows = connection.execute(
                f"SELECT {selected_columns} FROM {table_name} ORDER BY {order_by}"
            ).fetchall()

        source_values = [tuple(row[column] for column in columns) for row in source_rows]
        target_values = [tuple(row[column] for column in columns) for row in target_rows]
        if target_values != source_values:
            raise RuntimeError(
                "Проверка переноса настроек Google-выгрузки не пройдена; "
                "исходная таблица сохранена, миграция отменена."
            )
        connection.execute(f"ALTER TABLE {pending_name} RENAME TO {backup_name}")
        connection.commit()


def _column_names(connection: DatabaseConnection, table: str) -> set[str]:
    return connection.column_names(table)


def _migrate_mp_updated_at(connection: DatabaseConnection) -> None:
    columns = _column_names(connection, "stock_items")
    if columns and "mp_updated_at" not in columns:
        connection.execute("ALTER TABLE stock_items ADD COLUMN mp_updated_at TEXT")


def _migrate_image_url(connection: DatabaseConnection) -> None:
    columns = _column_names(connection, "stock_items")
    if columns and "image_url" not in columns:
        connection.execute("ALTER TABLE stock_items ADD COLUMN image_url TEXT")

    rows = connection.execute("SELECT id, image_url FROM stock_items WHERE image_url LIKE '[%'").fetchall()
    for row in rows:
        try:
            values = ast.literal_eval(str(row["image_url"]))
        except (SyntaxError, ValueError):
            continue
        if not isinstance(values, list):
            continue
        image_url = next(
            (
                str(value).strip()
                for value in values
                if str(value).strip().startswith(("https://", "http://"))
            ),
            "",
        )
        if image_url:
            connection.execute(
                "UPDATE stock_items SET image_url = ? WHERE id = ?",
                (image_url, row["id"]),
            )


def _migrate_delivery_marketplace(connection: DatabaseConnection) -> None:
    columns = _column_names(connection, "ff_stock_deliveries")
    if columns and "marketplace" not in columns:
        connection.execute(
            "ALTER TABLE ff_stock_deliveries ADD COLUMN marketplace TEXT NOT NULL DEFAULT 'WB'"
        )


def _migrate_stock_to_unified(connection: DatabaseConnection) -> None:
    existing = {
        str(row["name"]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    moves = (
        ("fbs_stock", "fbs", None),
        ("fbo_stock", "fbo", None),
        ("fbs_ff_stock", "fbs", "fulfillment"),
        ("fbo_warehouse_stock", "fbo", "warehouse"),
    )

    for table, scheme, warehouse_column in moves:
        if table not in existing:
            continue
        if warehouse_column is None:
            connection.execute(
                f"""
                INSERT OR IGNORE INTO mp_stock
                    (store_slug, article, marketplace, scheme, quantity, updated_at)
                SELECT store_slug, article, 'WB', ?, quantity, updated_at FROM {table}
                """,
                (scheme,),
            )
        else:
            connection.execute(
                f"""
                INSERT OR IGNORE INTO mp_warehouse_stock
                    (store_slug, article, marketplace, scheme, warehouse, quantity, updated_at)
                SELECT store_slug, article, 'WB', ?, {warehouse_column}, quantity, updated_at
                FROM {table}
                """,
                (scheme,),
            )
        connection.execute(f"DROP TABLE {table}")


def _migrate_trash_checked(connection: DatabaseConnection) -> None:
    columns = _column_names(connection, "trash_stock")
    if columns and "checked" not in columns:
        connection.execute("ALTER TABLE trash_stock ADD COLUMN checked INTEGER NOT NULL DEFAULT 0")


def _migrate_user_permissions(connection: DatabaseConnection) -> None:
    columns = _column_names(connection, "users")
    if not columns:
        return
    if "can_edit_stock" not in columns:
        connection.execute("ALTER TABLE users ADD COLUMN can_edit_stock INTEGER NOT NULL DEFAULT 1")
        connection.execute("UPDATE users SET can_edit_stock = 0 WHERE role = 'user'")
    if "can_manage_users" not in columns:
        connection.execute("ALTER TABLE users ADD COLUMN can_manage_users INTEGER NOT NULL DEFAULT 1")
        connection.execute("UPDATE users SET can_manage_users = 0 WHERE login = 'test'")

    for row in connection.execute("SELECT id FROM users"):
        user_id = int(row["id"] or 0)
        has_access = connection.execute(
            "SELECT 1 FROM user_store_access WHERE user_id = ? LIMIT 1",
            (user_id,),
        ).fetchone()
        if has_access:
            continue
        connection.executemany(
            "INSERT OR IGNORE INTO user_store_access (user_id, store_slug) VALUES (?, ?)",
            ((user_id, slug) for slug in STORES),
        )


def _migrate_operation_note(connection: DatabaseConnection) -> None:
    columns = _column_names(connection, "stock_operations")
    if columns and "note" not in columns:
        connection.execute("ALTER TABLE stock_operations ADD COLUMN note TEXT")


def _migrate_catalog_marketplace(connection: DatabaseConnection) -> None:
    columns = _column_names(connection, "stock_items")
    if not columns or "marketplace" in columns:
        return
    connection.executescript(
        """
        ALTER TABLE stock_items RENAME TO stock_items_old;
        CREATE TABLE stock_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            marketplace TEXT NOT NULL DEFAULT 'WB',
            article TEXT NOT NULL,
            barcode TEXT NOT NULL,
            name TEXT NOT NULL,
            mp_sku TEXT,
            mp_product_id TEXT,
            image_url TEXT,
            is_service INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            mp_updated_at TEXT,
            UNIQUE(store_slug, marketplace, article)
        );
        INSERT INTO stock_items (id, store_slug, marketplace, article, barcode, name)
        SELECT id, store_slug, 'WB', article, barcode, name FROM stock_items_old;
        DROP TABLE stock_items_old;
        """
    )


def _migrate_activity_log_operation(connection: DatabaseConnection) -> None:
    columns = _column_names(connection, "activity_log")
    if columns and "operation_id" not in columns:
        connection.execute("ALTER TABLE activity_log ADD COLUMN operation_id INTEGER")


def _migrate_ff_stock_marketplace(connection: DatabaseConnection) -> None:
    columns = _column_names(connection, "ff_stock")
    if not columns or "marketplace" in columns:
        return
    connection.executescript(
        """
        ALTER TABLE ff_stock RENAME TO ff_stock_old;
        CREATE TABLE ff_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            article TEXT NOT NULL,
            fulfillment TEXT NOT NULL,
            marketplace TEXT NOT NULL DEFAULT 'WB',
            quantity INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(store_slug, article, fulfillment, marketplace)
        );
        INSERT INTO ff_stock (store_slug, article, fulfillment, marketplace, quantity, updated_at)
        SELECT store_slug, article, fulfillment, 'WB', quantity, updated_at FROM ff_stock_old;
        DROP TABLE ff_stock_old;
        """
    )


def seed_defaults() -> None:
    database = database_for_path(core.DB_PATH)
    with database.unit_of_work() as unit_of_work:
        if unit_of_work.session is None:
            raise RuntimeError("UnitOfWork is not active")
        session = unit_of_work.session
        existing_fulfillments = set(session.scalars(select(FulfillmentRecord.name)))
        session.add_all(
            FulfillmentRecord(name=name) for name in FULFILLMENTS if name not in existing_fulfillments
        )
        for store_slug, article, barcode, name in STOCK_ITEMS:
            exists = session.scalar(
                select(StockItemRecord.id).where(
                    StockItemRecord.store_slug == store_slug,
                    StockItemRecord.marketplace == "WB",
                    StockItemRecord.article == article,
                )
            )
            if exists is None:
                session.add(
                    StockItemRecord(
                        store_slug=store_slug,
                        marketplace="WB",
                        article=article,
                        barcode=barcode,
                        name=name,
                    )
                )
        unit_of_work.commit()
