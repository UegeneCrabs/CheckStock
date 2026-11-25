from sqlalchemy import inspect, text

from app.core.constants import RETIRED_REPORT_STORAGE, DatabaseDialect
from app.infrastructure.database import Database
from app.repositories import core


def retire_report_tables(database: Database) -> None:
    with core.WRITE_LOCK, database.engine.connect() as connection:
        if database.dialect_name == DatabaseDialect.SQLITE:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.begin()
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:name))"),
                {"name": RETIRED_REPORT_STORAGE.advisory_lock},
            )

        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        retired_tables = tables.intersection(RETIRED_REPORT_STORAGE.drop_order)
        if retired_tables:
            for table in sorted(tables - retired_tables):
                if any(
                    foreign_key["referred_table"] in retired_tables
                    for foreign_key in inspector.get_foreign_keys(table)
                ):
                    raise RuntimeError(f"Unexpected dependency on retired report storage: {table}")
            for table in RETIRED_REPORT_STORAGE.drop_order:
                if table in retired_tables:
                    connection.exec_driver_sql(f'DROP TABLE "{table}"')
        connection.commit()
