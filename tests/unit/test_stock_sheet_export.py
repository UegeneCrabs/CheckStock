from dataclasses import replace
from datetime import date, datetime
from unittest import mock

import pytest

from app import stock_sheet_export
from app.domain import MOSCOW_TIMEZONE


class _Execute:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _FakeValues:
    def __init__(self):
        self.updates = []
        self.cleared_ranges = []

    def batchClear(self, *, spreadsheetId, body):
        assert spreadsheetId == "sheet-id"
        self.cleared_ranges.extend(body["ranges"])
        return _Execute({})

    def batchUpdate(self, *, spreadsheetId, body):
        assert spreadsheetId == "sheet-id"
        self.updates.extend(body["data"])
        return _Execute({"totalUpdatedCells": len(body["data"])})


class _FakeSpreadsheets:
    def __init__(self, sheet_names=("WB",)):
        self.value_api = _FakeValues()
        self.sheet_names = sheet_names

    def get(self, **kwargs):
        assert kwargs["spreadsheetId"] == "sheet-id"
        return _Execute(
            {"sheets": [{"properties": {"title": title}} for title in self.sheet_names]}
        )

    def values(self):
        return self.value_api


class _FakeService:
    def __init__(self, sheet_names=("WB",)):
        self.sheets = _FakeSpreadsheets(sheet_names)

    def spreadsheets(self):
        return self.sheets


def test_default_rimili_schedule_is_daily_at_one() -> None:
    settings = stock_sheet_export.default_settings(
        "rimili",
        datetime(2026, 8, 14, tzinfo=MOSCOW_TIMEZONE),
    )
    assert settings.enabled is True
    assert settings.schedule_kind == "daily"
    assert settings.run_time == "01:00"
    assert settings.spreadsheet_url_for("WB") == stock_sheet_export.RIMILI_SPREADSHEET_URL
    assert settings.spreadsheet_url_for("OZON") == stock_sheet_export.RIMILI_SPREADSHEET_URL
    assert settings.target("WB", "ff_stock").key_column_name == "АРТИКУЛ"
    assert settings.target("OZON", "fbo_stock").value_column_name == "ТЕКУЩИЙ СТОК В ПРОДАЖЕ FBO"
    assert settings.target("YANDEX MARKET", "fbs_stock").sheet_name == "YANDEX MARKET"
    assert settings.target("WB", "fbs_orders").sheet_name == ""


def test_schedule_due_uses_each_store_last_attempt() -> None:
    now = datetime(2026, 8, 14, 12, 0, tzinfo=MOSCOW_TIMEZONE)
    settings = stock_sheet_export.default_settings(
        "rimili",
        datetime(2026, 8, 14, 0, 30, tzinfo=MOSCOW_TIMEZONE),
    )
    assert stock_sheet_export.is_due(settings, now)

    completed = settings.__class__(
        **{
            **{field: getattr(settings, field) for field in settings.__dataclass_fields__},
            "last_attempt_at": "2026-08-14T01:05:00+03:00",
        }
    )
    assert not stock_sheet_export.is_due(completed, now)


def test_new_weekly_schedule_waits_for_configured_time() -> None:
    saved_at = datetime(2026, 8, 17, 1, 20, 57, tzinfo=MOSCOW_TIMEZONE)
    settings = replace(
        stock_sheet_export.default_settings("rimili", saved_at),
        schedule_kind="weekly",
        weekday=0,
        run_time="09:00",
    )

    assert not stock_sheet_export.is_due(
        settings,
        datetime(2026, 8, 17, 8, 59, tzinfo=MOSCOW_TIMEZONE),
    )
    assert stock_sheet_export.is_due(
        settings,
        datetime(2026, 8, 17, 9, 0, tzinfo=MOSCOW_TIMEZONE),
    )


def test_wb_order_totals_require_both_requested_statuses() -> None:
    orders = [
        {"id": 1, "nmId": 101, "article": "A", "skus": ["barcode-a"]},
        {"id": 2, "nmId": 102, "article": "A"},
        {"id": 3, "nmId": 103, "article": "B"},
    ]
    statuses = {
        1: {"supplierStatus": "new", "wbStatus": "waiting"},
        2: {"supplierStatus": "complete", "wbStatus": "sold"},
        3: {"supplierStatus": "confirm", "wbStatus": "ready_for_pickup"},
    }
    with (
        mock.patch.object(stock_sheet_export, "_unix_bounds", return_value=(1, 2)),
        mock.patch.object(stock_sheet_export.wb_tokens, "get_token", return_value="token"),
        mock.patch.object(stock_sheet_export.wb_api, "get_fbs_orders", return_value=orders),
        mock.patch.object(stock_sheet_export.wb_api, "get_fbs_order_statuses", return_value=statuses),
    ):
        assert stock_sheet_export._wb_fbs_order_totals(
            "rimili", datetime(2026, 8, 16, 1, tzinfo=MOSCOW_TIMEZONE)
        ) == {"101": 1, "103": 1}


def test_wb_order_totals_split_periods_longer_than_thirty_days() -> None:
    statuses = {
        1: {"supplierStatus": "new", "wbStatus": "waiting"},
        2: {"supplierStatus": "complete", "wbStatus": "sorted"},
    }
    with (
        mock.patch.object(stock_sheet_export, "FBS_ORDER_LOOKBACK_DAYS", 31),
        mock.patch.object(stock_sheet_export, "_unix_bounds", side_effect=[(1, 2), (3, 4)]) as bounds,
        mock.patch.object(stock_sheet_export.wb_tokens, "get_token", return_value="token"),
        mock.patch.object(
            stock_sheet_export.wb_api,
            "get_fbs_orders",
            side_effect=[
                [{"id": 1, "nmId": 101}],
                [{"id": 1, "nmId": 101}, {"id": 2, "nmId": 102}],
            ],
        ) as orders,
        mock.patch.object(stock_sheet_export.wb_api, "get_fbs_order_statuses", return_value=statuses),
    ):
        assert stock_sheet_export._wb_fbs_order_totals(
            "rimili", datetime(2026, 9, 1, tzinfo=MOSCOW_TIMEZONE)
        ) == {"101": 1, "102": 1}

    assert bounds.call_args_list == [
        mock.call(date(2026, 8, 1), date(2026, 8, 31)),
        mock.call(date(2026, 8, 31), date(2026, 9, 1)),
    ]
    assert orders.call_args_list == [mock.call("token", 1, 2), mock.call("token", 3, 4)]


def test_wb_completed_week_uses_the_seven_finished_moscow_days() -> None:
    assert stock_sheet_export.wb_completed_week(datetime(2026, 8, 16, 1, tzinfo=MOSCOW_TIMEZONE)) == (
        date(2026, 8, 9),
        date(2026, 8, 16),
    )


def test_fbs_period_uses_thirty_finished_moscow_days() -> None:
    assert stock_sheet_export.fbs_completed_period(
        datetime(2026, 8, 16, 1, tzinfo=MOSCOW_TIMEZONE)
    ) == (date(2026, 7, 17), date(2026, 8, 16))


def test_ozon_order_totals_read_the_requested_period_and_statuses_from_db() -> None:
    now = datetime(2026, 8, 16, 1, tzinfo=MOSCOW_TIMEZONE)
    with mock.patch.object(
        stock_sheet_export.db,
        "get_fbs_order_totals_for_period",
        return_value={"A-1": 3, "F-6": 8},
    ) as loader:
        result = stock_sheet_export._ozon_fbs_order_totals("rimili", now)

    assert result == {"A-1": 3, "F-6": 8}
    loader.assert_called_once_with(
        "rimili",
        "OZON",
        "2026-07-17",
        "2026-08-16",
        tuple(sorted(stock_sheet_export.OZON_FBS_EXPORT_STATUSES)),
    )


def test_yandex_order_totals_read_the_requested_period_and_statuses_from_db() -> None:
    now = datetime(2026, 8, 16, 1, tzinfo=MOSCOW_TIMEZONE)
    with mock.patch.object(
        stock_sheet_export.db,
        "get_fbs_order_totals_for_period",
        return_value={"YA-1": 3, "YA-4": 6, "YA-5": 7},
    ) as loader:
        result = stock_sheet_export._yandex_fbs_order_totals("rimili", now)

    assert result == {"YA-1": 3, "YA-4": 6, "YA-5": 7}
    loader.assert_called_once_with(
        "rimili",
        "YANDEX MARKET",
        "2026-07-17",
        "2026-08-16",
        tuple(sorted(stock_sheet_export.YANDEX_FBS_EXPORT_STATUSES)),
    )


def test_writer_replaces_a2_g_with_header_and_complete_catalog_snapshot() -> None:
    settings = stock_sheet_export.default_settings("rimili")
    service = _FakeService()
    catalog = [
        {"article": "A-1", "barcode": "46001", "name": "Первый товар"},
        {"article": "A-2", "barcode": "46002", "name": "Второй товар"},
    ]
    values = {
        "ff_stock": {"A-1": 3, "A-2": 4},
        "fbs_stock": {"A-1": 5, "A-2": 6},
        "fbo_stock": {"A-1": 7, "A-2": 8},
    }

    report = stock_sheet_export._write_marketplace(
        service,
        "sheet-id",
        settings,
        "WB",
        catalog,
        values,
    )

    assert report["updated_cells"] == 21
    assert report["rows"] == 2
    assert service.sheets.value_api.cleared_ranges == ["'WB'!A2:G"]
    assert service.sheets.value_api.updates == [
        {
            "range": "'WB'!A2:G4",
            "values": [
                list(stock_sheet_export.EXPORT_HEADERS),
                ["A-1", 46001, "Первый товар", 15, 3, 5, 7],
                ["A-2", 46002, "Второй товар", 18, 4, 6, 8],
            ],
        }
    ]


def test_fbs_order_writer_replaces_a2_b_and_excludes_zero_articles() -> None:
    settings = replace(
        stock_sheet_export.default_settings("rimili"),
        targets=tuple(
            replace(target, sheet_name="WB FBS заказы")
            if target.marketplace == "WB" and target.metric == "fbs_orders"
            else target
            for target in stock_sheet_export.default_settings("rimili").targets
        ),
    )
    service = _FakeService(("WB", "WB FBS заказы"))

    report = stock_sheet_export._write_fbs_orders(
        service,
        "sheet-id",
        settings,
        "WB",
        {"412648673": 12, "0": 0},
    )

    assert report["rows"] == 1
    assert service.sheets.value_api.cleared_ranges == ["'WB FBS заказы'!A2:B"]
    assert service.sheets.value_api.updates == [
        {
            "range": "'WB FBS заказы'!A2:B3",
            "values": [
                list(stock_sheet_export.ORDER_EXPORT_HEADERS),
                [412648673, 12],
            ],
        }
    ]


def test_sheet_identifiers_are_numeric_without_apostrophes() -> None:
    assert stock_sheet_export._sheet_identifier("'964286427") == 964286427
    assert stock_sheet_export._sheet_identifier("'2050453811850") == 2050453811850
    assert stock_sheet_export._sheet_identifier("OZON-42") == "OZON-42"
    assert stock_sheet_export._sheet_identifier("1234567890123456") == "1234567890123456"


def test_record_result_success_uses_postgresql_safe_parameters(monkeypatch) -> None:
    connection = mock.Mock()
    monkeypatch.setattr(stock_sheet_export.repository, "get_connection", lambda: connection)

    stock_sheet_export.repository.record_result(
        "rimili",
        "2026-08-17T09:00:00+03:00",
        error=None,
    )

    statement, parameters = connection.execute.call_args.args
    assert "CASE WHEN" not in statement
    assert "last_error = NULL" in statement
    assert parameters == (
        "2026-08-17T09:00:00+03:00",
        "2026-08-17T09:00:00+03:00",
        "rimili",
    )
    connection.commit.assert_called_once_with()
    connection.close.assert_called_once_with()


def test_run_store_records_attempt_before_writing_to_google(monkeypatch) -> None:
    events = []
    report = {"marketplaces": []}
    monkeypatch.setattr(
        stock_sheet_export.repository,
        "record_attempt",
        lambda *_args, **_kwargs: events.append("attempt"),
    )
    monkeypatch.setattr(
        stock_sheet_export,
        "export_store",
        lambda *_args, **_kwargs: events.append("google") or report,
    )
    monkeypatch.setattr(
        stock_sheet_export.repository,
        "record_result",
        lambda *_args, **_kwargs: events.append("result"),
    )

    assert stock_sheet_export.run_store("rimili") == report
    assert events == ["attempt", "google", "result"]


def test_scoped_run_does_not_mark_the_full_schedule_as_completed(monkeypatch) -> None:
    report = {"marketplaces": []}
    writer = mock.Mock(return_value=report)
    attempt = mock.Mock()
    result = mock.Mock()
    monkeypatch.setattr(stock_sheet_export, "export_store", writer)
    monkeypatch.setattr(stock_sheet_export.repository, "record_attempt", attempt)
    monkeypatch.setattr(stock_sheet_export.repository, "record_result", result)

    assert stock_sheet_export.run_store(
        "rimili",
        marketplace="WB",
        export_kind="fbs_orders",
    ) == report

    writer.assert_called_once_with(
        "rimili",
        None,
        marketplace="WB",
        export_kind="fbs_orders",
    )
    attempt.assert_not_called()
    result.assert_not_called()


def test_run_store_does_not_write_to_google_when_attempt_cannot_be_recorded(monkeypatch) -> None:
    writer = mock.Mock()
    monkeypatch.setattr(
        stock_sheet_export.repository,
        "record_attempt",
        mock.Mock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(stock_sheet_export, "export_store", writer)

    with pytest.raises(RuntimeError, match="database unavailable"):
        stock_sheet_export.run_store("rimili")

    writer.assert_not_called()


def test_run_due_raises_when_a_store_export_fails(monkeypatch) -> None:
    now = datetime(2026, 8, 17, 9, 0, tzinfo=MOSCOW_TIMEZONE)
    settings = replace(
        stock_sheet_export.default_settings(
            "rimili",
            datetime(2026, 8, 17, 8, 0, tzinfo=MOSCOW_TIMEZONE),
        ),
        schedule_kind="weekly",
        weekday=0,
        run_time="09:00",
    )
    monkeypatch.setattr(stock_sheet_export, "list_settings", lambda: [settings])
    monkeypatch.setattr(
        stock_sheet_export,
        "run_store",
        mock.Mock(side_effect=RuntimeError("Google unavailable")),
    )

    with pytest.raises(stock_sheet_export.StockSheetExportError, match="rimili"):
        stock_sheet_export.run_due(now)


def test_export_store_uses_separate_spreadsheet_for_each_marketplace() -> None:
    settings = stock_sheet_export.default_settings("rimili")
    settings = replace(
        settings,
        spreadsheets=tuple(
            stock_sheet_export.MarketplaceSpreadsheet(
                marketplace=marketplace,
                spreadsheet_url=f"https://docs.google.com/spreadsheets/d/{marketplace.lower().replace(' ', '-')}-id/edit",
            )
            for marketplace in stock_sheet_export.repository.MARKETPLACES
        ),
    )
    with (
        mock.patch.object(stock_sheet_export, "get_settings", return_value=settings),
        mock.patch.object(stock_sheet_export, "list_settings", return_value=[settings]),
        mock.patch.object(stock_sheet_export, "_google_service", return_value=object()),
        mock.patch.object(stock_sheet_export.db, "get_catalog_items", return_value=[]),
        mock.patch.object(stock_sheet_export, "_metric_values", return_value={}),
        mock.patch.object(
            stock_sheet_export,
            "_write_marketplace",
            side_effect=lambda _service, sheet_id, _settings, marketplace, _catalog, _values: {
                "marketplace": marketplace,
                "spreadsheet_id": sheet_id,
            },
        ) as writer,
    ):
        report = stock_sheet_export.export_store("rimili")

    assert report["spreadsheet_ids"] == {
        "WB": "wb-id",
        "OZON": "ozon-id",
        "YANDEX MARKET": "yandex-market-id",
    }
    assert [call.args[1] for call in writer.call_args_list] == [
        "wb-id",
        "ozon-id",
        "yandex-market-id",
    ]


def test_export_store_can_run_only_fbs_orders_with_stock_sheet_disabled() -> None:
    settings = stock_sheet_export.default_settings("rimili")
    settings = replace(
        settings,
        targets=tuple(
            replace(
                target,
                sheet_name=(
                    "WB FBS заказы"
                    if target.marketplace == "WB" and target.metric == "fbs_orders"
                    else ""
                    if target.marketplace == "WB"
                    else target.sheet_name
                ),
            )
            for target in settings.targets
        ),
    )
    stock_writer = mock.Mock()
    with (
        mock.patch.object(stock_sheet_export, "get_settings", return_value=settings),
        mock.patch.object(stock_sheet_export, "list_settings", return_value=[settings]),
        mock.patch.object(stock_sheet_export, "_google_service", return_value=object()),
        mock.patch.object(stock_sheet_export, "_combined_stock_snapshot") as stock_loader,
        mock.patch.object(stock_sheet_export, "_write_marketplace", stock_writer),
        mock.patch.object(
            stock_sheet_export,
            "_combined_fbs_order_totals",
            return_value={"123": 4},
        ) as order_loader,
        mock.patch.object(
            stock_sheet_export,
            "_write_fbs_orders",
            return_value={"marketplace": "WB", "rows": 1, "updated_cells": 4},
        ) as order_writer,
    ):
        report = stock_sheet_export.export_store(
            "rimili",
            marketplace="WB",
            export_kind="fbs_orders",
        )

    stock_loader.assert_not_called()
    stock_writer.assert_not_called()
    order_loader.assert_called_once_with(("rimili",), "WB", now=None)
    order_writer.assert_called_once()
    assert report["spreadsheet_ids"] == {
        "WB": stock_sheet_export._spreadsheet_id(stock_sheet_export.RIMILI_SPREADSHEET_URL)
    }
    assert report["marketplaces"][0]["stocks"] == {"skipped": True}


def test_export_store_rejects_requested_export_without_a_sheet() -> None:
    settings = stock_sheet_export.default_settings("rimili")
    settings = replace(
        settings,
        targets=tuple(
            replace(target, sheet_name="")
            if target.marketplace == "WB" and target.metric in stock_sheet_export.repository.STOCK_METRICS
            else target
            for target in settings.targets
        ),
    )
    with (
        mock.patch.object(stock_sheet_export, "get_settings", return_value=settings),
        mock.patch.object(stock_sheet_export, "list_settings", return_value=[settings]),
        pytest.raises(stock_sheet_export.StockSheetExportError, match="Лист стоков для WB"),
    ):
        stock_sheet_export.export_store("rimili", marketplace="WB", export_kind="stocks")


def test_shared_destination_combines_stores_and_sums_duplicate_articles() -> None:
    rockkiddo = stock_sheet_export.default_settings("rockkiddo")
    toyka = stock_sheet_export.default_settings("toyka")
    shared_spreadsheets = tuple(
        stock_sheet_export.MarketplaceSpreadsheet(
            marketplace=marketplace,
            spreadsheet_url=f"https://docs.google.com/spreadsheets/d/shared-{marketplace.lower().replace(' ', '-')}/edit",
        )
        for marketplace in stock_sheet_export.repository.MARKETPLACES
    )
    rockkiddo = replace(rockkiddo, spreadsheets=shared_spreadsheets)
    toyka = replace(toyka, spreadsheets=shared_spreadsheets)

    catalogs = {
        "rockkiddo": [
            {"article": "COMMON", "barcode": "100", "name": "Общий товар"},
            {"article": "ROCK", "barcode": "101", "name": "Товар Rockkiddo"},
        ],
        "toyka": [
            {"article": "common", "barcode": "100", "name": "Общий товар"},
            {"article": "TOY", "barcode": "102", "name": "Товар Toyka"},
        ],
    }

    def metric_values(store_slug, _marketplace, catalog, _metrics, **_kwargs):
        quantities = {
            "rockkiddo": {"COMMON": (1, 2, 3), "ROCK": (4, 5, 6)},
            "toyka": {"common": (10, 20, 30), "TOY": (40, 50, 60)},
        }
        return {
            metric: {item["article"]: quantities[store_slug][item["article"]][index] for item in catalog}
            for index, metric in enumerate(stock_sheet_export.repository.STOCK_METRICS)
        }

    with (
        mock.patch.object(stock_sheet_export, "get_settings", return_value=rockkiddo),
        mock.patch.object(stock_sheet_export, "list_settings", return_value=[rockkiddo, toyka]),
        mock.patch.object(stock_sheet_export, "_google_service", return_value=object()),
        mock.patch.object(
            stock_sheet_export.db,
            "get_catalog_items",
            side_effect=lambda store_slug, _marketplace: catalogs[store_slug],
        ),
        mock.patch.object(stock_sheet_export, "_metric_values", side_effect=metric_values),
        mock.patch.object(
            stock_sheet_export,
            "_write_marketplace",
            side_effect=lambda _service, _sheet_id, _settings, marketplace, catalog, values: {
                "marketplace": marketplace,
                "catalog": catalog,
                "values": values,
                "updated_cells": 0,
            },
        ),
    ):
        report = stock_sheet_export.export_store("rockkiddo")

    wb_report = report["marketplaces"][0]
    assert wb_report["store_slugs"] == ("rockkiddo", "toyka")
    assert [item["article"] for item in wb_report["catalog"]] == ["COMMON", "ROCK", "TOY"]
    assert wb_report["values"] == {
        "ff_stock": {"COMMON": 11, "ROCK": 4, "TOY": 40},
        "fbs_stock": {"COMMON": 22, "ROCK": 5, "TOY": 50},
        "fbo_stock": {"COMMON": 33, "ROCK": 6, "TOY": 60},
    }


def test_rockkiddo_destination_includes_toyka_without_a_toyka_target() -> None:
    rockkiddo = stock_sheet_export.default_settings("rockkiddo")
    toyka = stock_sheet_export.default_settings("toyka")
    rockkiddo = replace(
        rockkiddo,
        spreadsheets=tuple(
            stock_sheet_export.MarketplaceSpreadsheet(
                marketplace=marketplace,
                spreadsheet_url=f"https://docs.google.com/spreadsheets/d/rockkiddo-{marketplace.lower().replace(' ', '-')}/edit",
            )
            for marketplace in stock_sheet_export.repository.MARKETPLACES
        ),
        targets=tuple(
            replace(target, sheet_name="Rockkiddo FBS")
            if target.marketplace == "WB" and target.metric == "fbs_orders"
            else target
            for target in rockkiddo.targets
        ),
    )

    assert stock_sheet_export._stores_for_destination(
        rockkiddo,
        "WB",
        [rockkiddo, toyka],
        "fbs_orders",
    ) == ("rockkiddo", "toyka")


def test_size_variants_are_aggregated_for_base_article_row() -> None:
    catalog = [
        {"article": "123 / S", "barcode": "barcode-s"},
        {"article": "123 / M", "barcode": "barcode-m"},
    ]
    with (
        mock.patch.object(stock_sheet_export.db, "get_ff_available_totals", return_value={}),
        mock.patch.object(stock_sheet_export.db, "get_mp_stock_totals", return_value={}),
        mock.patch.object(
            stock_sheet_export,
            "_wb_fbs_order_totals",
            return_value={"barcode-s": 2, "barcode-m": 3},
        ),
    ):
        values = stock_sheet_export._metric_values("rimili", "WB", catalog, ("fbs_orders",))

    aliases, _ = stock_sheet_export._catalog_aliases(catalog)
    assert aliases["123"] == {"123 / S", "123 / M"}
    assert sum(values["fbs_orders"][article] for article in aliases["123"]) == 5


def test_ozon_metric_values_dispatch_weekly_fbs_orders() -> None:
    catalog = [{"article": "OZ-1", "barcode": "ozon-barcode"}]
    with mock.patch.object(
        stock_sheet_export,
        "_ozon_fbs_order_totals",
        return_value={"ozon-barcode": 4},
    ):
        values = stock_sheet_export._metric_values("rimili", "OZON", catalog, ("fbs_orders",))

    assert values == {"fbs_orders": {"OZ-1": 4}}


def test_yandex_metric_values_keep_catalog_rows_and_include_fbo() -> None:
    catalog = [{"article": "YA-1", "barcode": "ya-barcode"}]
    with (
        mock.patch.object(stock_sheet_export.db, "get_ff_available_totals", return_value={"YA-1": 4}),
        mock.patch.object(stock_sheet_export.db, "get_mp_stock_totals", return_value={"YA-1": 5}),
    ):
        values = stock_sheet_export._metric_values(
            "rimili", "YANDEX MARKET", catalog, ("ff_stock", "fbs_stock", "fbo_stock")
        )
    assert values == {
        "ff_stock": {"YA-1": 4},
        "fbs_stock": {"YA-1": 5},
        "fbo_stock": {"YA-1": 5},
    }


def test_duplicate_configured_header_is_rejected() -> None:
    with pytest.raises(stock_sheet_export.StockSheetExportError, match="несколько раз"):
        stock_sheet_export._find_header([["Артикул", "Артикул"]], "Артикул", "WB")
