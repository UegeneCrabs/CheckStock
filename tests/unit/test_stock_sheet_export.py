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
    def __init__(self):
        self.value_api = _FakeValues()

    def get(self, **kwargs):
        assert kwargs["spreadsheetId"] == "sheet-id"
        return _Execute({"sheets": [{"properties": {"title": "WB"}}]})

    def values(self):
        return self.value_api


class _FakeService:
    def __init__(self):
        self.sheets = _FakeSpreadsheets()

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
        {"id": 1, "article": "A", "skus": ["barcode-a"]},
        {"id": 2, "article": "A"},
        {"id": 3, "article": "B"},
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
        ) == {"barcode-a": 1, "B": 1}


def test_wb_completed_week_uses_the_seven_finished_moscow_days() -> None:
    assert stock_sheet_export.wb_completed_week(datetime(2026, 8, 16, 1, tzinfo=MOSCOW_TIMEZONE)) == (
        date(2026, 8, 9),
        date(2026, 8, 16),
    )


def test_ozon_week_uses_the_same_moscow_days_as_wb() -> None:
    assert stock_sheet_export._ozon_rfc3339_bounds(date(2026, 8, 9), date(2026, 8, 16)) == (
        "2026-08-08T21:00:00Z",
        "2026-08-15T21:00:00Z",
    )


def test_ozon_order_totals_exclude_terminal_statuses_and_deduplicate() -> None:
    postings = [
        {
            "posting_number": "posting-1",
            "status": "awaiting_packaging",
            "products": [{"offer_id": "A-1", "quantity": 1}],
        },
        {
            "posting_number": "posting-1",
            "status": "awaiting_deliver",
            "products": [{"offer_id": "A-1", "quantity": 3}],
        },
        {
            "posting_number": "posting-2",
            "status": "delivered",
            "products": [{"offer_id": "B-2", "quantity": 4}],
        },
        {
            "posting_number": "posting-3",
            "status": "CANCELLED",
            "products": [{"offer_id": "C-3", "quantity": 5}],
        },
        {
            "posting_number": "posting-4",
            "status": "not_accepted",
            "products": [{"offer_id": "D-4", "quantity": 6}],
        },
    ]
    now = datetime(2026, 8, 16, 1, tzinfo=MOSCOW_TIMEZONE)
    with (
        mock.patch.object(
            stock_sheet_export.ozon_tokens,
            "get_credentials",
            return_value=("client", "key"),
        ),
        mock.patch.object(
            stock_sheet_export.ozon_api,
            "get_fbs_postings_v4",
            return_value=postings,
        ) as loader,
    ):
        result = stock_sheet_export._ozon_fbs_order_totals("rimili", now)

    assert result == {"A-1": 3}
    loader.assert_called_once_with(
        "client",
        "key",
        "2026-08-08T21:00:00Z",
        "2026-08-15T21:00:00Z",
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
