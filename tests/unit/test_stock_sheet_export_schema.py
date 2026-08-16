from sqlalchemy import inspect

from app.infrastructure.database import database_for_path, dispose_databases
from app.infrastructure.orm import OrmBase
from app.repositories import core, schema


def test_old_export_targets_are_migrated_without_losing_rows(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-export.sqlite3"
    monkeypatch.setattr(core, "DB_PATH", path)
    dispose_databases()
    database = database_for_path(path)
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE stock_sheet_export_targets (
                store_slug TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                metric TEXT NOT NULL,
                sheet_name TEXT NOT NULL,
                key_column_name TEXT NOT NULL,
                value_column_name TEXT NOT NULL,
                PRIMARY KEY (store_slug, marketplace, metric)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO stock_sheet_export_targets
                (store_slug, marketplace, metric, sheet_name, key_column_name, value_column_name)
            VALUES ('rimili', 'WB', 'fbo_stock', 'Warehouse', 'Артикул', 'FBO')
            """
        )
        connection.commit()

    schema.init_db()

    with database_for_path(path).connect() as connection:
        columns = connection.column_names("stock_sheet_export_targets")
        row = connection.execute(
            "SELECT marketplace, metric, sheet_name, key_column_name, value_column_name "
            "FROM stock_sheet_export_targets"
        ).fetchone()
        backup_row = connection.execute(
            "SELECT marketplace, metric, sheet_name, key_column_name, value_column_name "
            "FROM stock_sheet_export_targets_backup_v1"
        ).fetchone()

    assert "id" in columns
    assert row is not None
    expected = (
        "WB",
        "fbo_stock",
        "Warehouse",
        "Артикул",
        "FBO",
    )
    assert (
        tuple(
            row[key]
            for key in ("marketplace", "metric", "sheet_name", "key_column_name", "value_column_name")
        )
        == expected
    )
    assert backup_row is not None
    assert (
        tuple(
            backup_row[key]
            for key in ("marketplace", "metric", "sheet_name", "key_column_name", "value_column_name")
        )
        == expected
    )
    dispose_databases()


def test_interrupted_export_target_migration_resumes_without_duplicate_rows(tmp_path, monkeypatch) -> None:
    path = tmp_path / "interrupted-export.sqlite3"
    monkeypatch.setattr(core, "DB_PATH", path)
    dispose_databases()
    database = database_for_path(path)
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE stock_sheet_export_targets (
                store_slug TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                metric TEXT NOT NULL,
                sheet_name TEXT NOT NULL,
                key_column_name TEXT NOT NULL,
                value_column_name TEXT NOT NULL,
                PRIMARY KEY (store_slug, marketplace, metric)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO stock_sheet_export_targets
                (store_slug, marketplace, metric, sheet_name, key_column_name, value_column_name)
            VALUES ('rimili', 'WB', 'fbs_stock', 'WB', 'Артикул', 'FBS')
            """
        )
        connection.commit()

    assert schema._prepare_stock_sheet_export_target_migration(database)
    OrmBase.metadata.create_all(database.engine)
    schema.init_db()

    inspector = inspect(database.engine)
    assert inspector.has_table("stock_sheet_export_targets_backup_v1")
    assert not inspector.has_table("stock_sheet_export_targets_migration_v1")
    with database.connect() as connection:
        rows = connection.execute("SELECT metric FROM stock_sheet_export_targets").fetchall()
    assert [row["metric"] for row in rows] == ["fbs_stock"]
    dispose_databases()


def test_store_spreadsheet_url_is_copied_to_each_marketplace(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-spreadsheet-url.sqlite3"
    monkeypatch.setattr(core, "DB_PATH", path)
    dispose_databases()
    database = database_for_path(path)
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE stock_sheet_export_settings (
                store_slug TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                schedule_kind TEXT NOT NULL DEFAULT 'weekly',
                weekday INTEGER NOT NULL DEFAULT 6,
                run_time TEXT NOT NULL DEFAULT '01:00',
                spreadsheet_url TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                last_attempt_at TEXT,
                last_success_at TEXT,
                last_error TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO stock_sheet_export_settings
                (store_slug, spreadsheet_url, updated_at)
            VALUES ('rimili', 'https://docs.google.com/spreadsheets/d/legacy-id/edit', '2026-08-16')
            """
        )
        connection.commit()

    schema.init_db()
    schema.init_db()

    with database_for_path(path).connect() as connection:
        rows = connection.execute(
            """
            SELECT marketplace, spreadsheet_url
            FROM stock_sheet_export_marketplaces
            WHERE store_slug = 'rimili'
            ORDER BY marketplace
            """
        ).fetchall()

    assert [(row["marketplace"], row["spreadsheet_url"]) for row in rows] == [
        ("OZON", "https://docs.google.com/spreadsheets/d/legacy-id/edit"),
        ("WB", "https://docs.google.com/spreadsheets/d/legacy-id/edit"),
        ("YANDEX MARKET", "https://docs.google.com/spreadsheets/d/legacy-id/edit"),
    ]
    dispose_databases()
