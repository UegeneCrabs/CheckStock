"""Retire access-control storage before ORM metadata is created."""

from sqlalchemy import inspect

from app.infrastructure.database import Database
from app.repositories import core

RETIRED_TABLES = (
    "temporary_access_grants",
    "access_requests",
    "user_access_profiles",
    "user_marketplace_access",
    "user_store_access",
    "user_section_access",
    "user_ui_preferences",
    "user_section_usage",
    "user_usage_sessions",
)
RETIRED_USER_COLUMNS = ("role", "can_edit_stock", "can_manage_users")


def retire_access_control(database: Database) -> None:
    """Atomic, repeatable SQLite/PostgreSQL migration. Back up before deployment.

    Only the explicitly named tables/columns are removed; unexpected inbound
    dependencies make the transaction fail instead of cascading into business data.
    """
    with core.WRITE_LOCK, database.engine.connect() as connection:
        try:
            if database.dialect_name == "sqlite":
                # SQLite's legacy driver does not begin transactions for DDL.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
                connection.exec_driver_sql("SELECT pg_advisory_xact_lock(731942681)")
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            for table in tables - set(RETIRED_TABLES):
                if any(fk["referred_table"] in RETIRED_TABLES for fk in inspector.get_foreign_keys(table)):
                    raise RuntimeError(f"Unexpected dependency on retired access storage: {table}")
            for table in RETIRED_TABLES:
                if table in tables:
                    connection.exec_driver_sql(f'DROP TABLE "{table}"')
            if "users" in tables:
                columns = {column["name"] for column in inspector.get_columns("users")}
                for column in RETIRED_USER_COLUMNS:
                    if column in columns:
                        connection.exec_driver_sql(f'ALTER TABLE users DROP COLUMN "{column}"')
            connection.commit()
        except Exception:
            connection.rollback()
            raise
