from sqlalchemy import inspect

from app.infrastructure.database import database_for_path, dispose_databases
from app.infrastructure.orm import OrmBase
from app.repositories import core, schema


def test_buyout_and_funnel_tables_are_migrated_to_automatic_weekly_metrics(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "legacy-buyout.sqlite3"
    monkeypatch.setattr(core, "DB_PATH", path)
    dispose_databases()
    database = database_for_path(path)
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE unit_economics_1c_product_settings (
                store_slug TEXT NOT NULL,
                marketplace TEXT NOT NULL DEFAULT 'WB',
                article TEXT NOT NULL,
                delivery_wb_rub FLOAT NOT NULL DEFAULT 0,
                buyout_percent FLOAT NOT NULL DEFAULT 0,
                return_cost_rub FLOAT NOT NULL DEFAULT 0,
                volume_l FLOAT NOT NULL DEFAULT 0,
                storage_wb_rub FLOAT NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                updated_by_user_id INTEGER NOT NULL,
                updated_by_name TEXT NOT NULL,
                PRIMARY KEY (store_slug, marketplace, article)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO unit_economics_1c_product_settings
                (store_slug, marketplace, article, delivery_wb_rub, buyout_percent,
                 return_cost_rub, volume_l, storage_wb_rub, updated_at,
                 updated_by_user_id, updated_by_name)
            VALUES
                ('rimili', 'WB', '123', 120, 75, 50, 1.5, 2,
                 '2026-08-25T10:00:00+00:00', 7, 'Unit Admin')
            """
        )
        connection.execute(
            """
            CREATE TABLE wb_funnel_daily_orders (
                store_slug TEXT NOT NULL,
                day TEXT NOT NULL,
                article TEXT NOT NULL,
                vendor_code TEXT NOT NULL DEFAULT '',
                product_name TEXT NOT NULL DEFAULT '',
                orders_count INTEGER NOT NULL DEFAULT 0,
                orders_amount FLOAT NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (store_slug, day, article)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO wb_funnel_daily_orders
                (store_slug, day, article, orders_count, orders_amount, updated_at)
            VALUES
                ('rimili', '2026-08-25', '123', 3, 1500, '2026-08-25T10:00:00+00:00')
            """
        )
        connection.commit()

    schema.init_db()
    schema.init_db()

    with database_for_path(path).connect() as connection:
        product_columns = connection.column_names("unit_economics_1c_product_settings")
        product = connection.execute(
            """
            SELECT delivery_wb_rub, return_cost_rub, volume_l, storage_wb_rub
              FROM unit_economics_1c_product_settings
             WHERE store_slug='rimili' AND article='123'
            """
        ).fetchone()
        funnel_columns = connection.column_names("wb_funnel_daily_orders")
        funnel = connection.execute(
            "SELECT source_version FROM wb_funnel_daily_orders WHERE article='123'"
        ).fetchone()
        metric_columns = connection.column_names("wb_funnel_product_metrics")

    assert "buyout_percent" not in product_columns
    assert product is not None
    assert dict(product) == {
        "delivery_wb_rub": 120.0,
        "return_cost_rub": 50.0,
        "volume_l": 1.5,
        "storage_wb_rub": 2.0,
    }
    assert "source_version" in funnel_columns
    assert {"cancel_count", "cancel_amount"}.issubset(funnel_columns)
    assert funnel is not None and funnel["source_version"] == 1
    assert {
        "orders_count",
        "orders_amount",
        "cancel_count",
        "cancel_amount",
        "buyout_percent",
        "source_version",
    }.issubset(metric_columns)
    dispose_databases()


def test_cabinet_settings_are_migrated_to_current_commissions_and_taxes(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-cabinet-settings.sqlite3"
    monkeypatch.setattr(core, "DB_PATH", path)
    dispose_databases()
    database = database_for_path(path)
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE unit_economics_1c_cabinet_settings (
                store_slug TEXT NOT NULL,
                marketplace TEXT NOT NULL DEFAULT 'WB',
                acceptance_coefficient FLOAT NOT NULL DEFAULT 0,
                wb_extra_tariff_percent FLOAT NOT NULL DEFAULT 0,
                annual_capital_rate_percent FLOAT NOT NULL DEFAULT 18.5,
                days_in_year INTEGER NOT NULL DEFAULT 365,
                custom_parameter_1 FLOAT NOT NULL DEFAULT 21,
                custom_parameter_2 FLOAT NOT NULL DEFAULT 35,
                overhead_percent FLOAT NOT NULL DEFAULT 0.57,
                team_percent FLOAT NOT NULL DEFAULT 2.27,
                contrib_percent FLOAT NOT NULL DEFAULT 0,
                tax_percent FLOAT NOT NULL DEFAULT 9,
                updated_at TEXT NOT NULL,
                updated_by_user_id INTEGER NOT NULL,
                updated_by_name TEXT NOT NULL,
                PRIMARY KEY (store_slug, marketplace)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO unit_economics_1c_cabinet_settings
                (store_slug, marketplace, tax_percent, updated_at,
                 updated_by_user_id, updated_by_name)
            VALUES ('rimili', 'WB', 11, '2026-08-18T12:00:00+00:00', 7, 'Unit Admin')
            """
        )
        connection.commit()

    schema.init_db()
    schema.init_db()

    with database_for_path(path).connect() as connection:
        columns = connection.column_names("unit_economics_1c_cabinet_settings")
        row = connection.execute(
            """
            SELECT buyout_period_days, acquiring_percent, team_commission_percent, vat_percent,
                   usn_percent, osno_percent, tax_system, updated_by_name
              FROM unit_economics_1c_cabinet_settings
             WHERE store_slug='rimili' AND marketplace='WB'
            """
        ).fetchone()

    assert "acquiring_percent" in columns
    assert "buyout_period_days" in columns
    assert "team_commission_percent" in columns
    assert "vat_percent" in columns
    assert "usn_percent" in columns
    assert "osno_percent" in columns
    assert "tax_system" in columns
    assert {
        "annual_capital_rate_percent",
        "days_in_year",
        "custom_parameter_1",
        "custom_parameter_2",
        "overhead_percent",
        "team_percent",
        "contrib_percent",
        "tax_percent",
    }.isdisjoint(columns)
    assert row is not None
    assert row["buyout_period_days"] == 14
    assert row["acquiring_percent"] == 3.8
    assert row["team_commission_percent"] == 2.27
    assert row["vat_percent"] == 11
    assert row["usn_percent"] == 0
    assert row["osno_percent"] == 0
    assert row["tax_system"] == "usn"
    assert row["updated_by_name"] == "Unit Admin"
    dispose_databases()


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
