import ast

from sqlalchemy import select

from app.infrastructure.database import DatabaseConnection, database_for_path
from app.infrastructure.orm import FulfillmentRecord, OrmBase, StockItemRecord
from app.repositories import core
from app.repositories.seed_data import FULFILLMENTS, STOCK_ITEMS
from app.stores import STORES


def init_db() -> None:
    database = database_for_path(core.DB_PATH)
    OrmBase.metadata.create_all(database.engine)
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


def _column_names(connection: DatabaseConnection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


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
