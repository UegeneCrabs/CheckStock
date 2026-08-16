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

    def get(self, *, spreadsheetId, range):
        assert spreadsheetId == "sheet-id"
        if range.endswith("!1:25"):
            return _Execute(
                {
                    "values": [
                        [
                            "ARTICLE",
                            "Доступно ФФ для распределения",
                            "Текущий сток в продаже FBS",
                            "Текущий сток в продаже FBO",
                            "Заказы по ФБС",
                        ]
                    ]
                }
            )
        if range.endswith("!A2:A"):
            return _Execute({"values": [["A-1"], ["46002"], ["Не наш товар"]]})
        raise AssertionError(range)

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
    assert settings.spreadsheet_url == stock_sheet_export.RIMILI_SPREADSHEET_URL
    assert settings.target("WB", "fbs_orders").value_column_name == "Заказы по ФБС"
    assert settings.target("OZON", "fbs_orders").value_column_name == "Заказы по ФБС"
    assert settings.target("YANDEX MARKET", "fbs_stock").sheet_name == "YANDEX MARKET"


def test_schedule_due_uses_each_store_last_attempt() -> None:
    now = datetime(2026, 8, 14, 12, 0, tzinfo=MOSCOW_TIMEZONE)
    settings = stock_sheet_export.default_settings("rimili", now)
    assert stock_sheet_export.is_due(settings, now)

    completed = settings.__class__(
        **{
            **{field: getattr(settings, field) for field in settings.__dataclass_fields__},
            "last_attempt_at": "2026-08-14T01:05:00+03:00",
        }
    )
    assert not stock_sheet_export.is_due(completed, now)


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


def test_writer_finds_headers_in_first_25_rows_and_updates_catalog_rows() -> None:
    settings = stock_sheet_export.default_settings("rimili")
    service = _FakeService()
    catalog = [
        {"article": "A-1", "barcode": "46001"},
        {"article": "A-2", "barcode": "46002"},
    ]
    values = {
        "ff_stock": {"A-1": 3, "A-2": 4},
        "fbs_stock": {"A-1": 5, "A-2": 6},
        "fbo_stock": {"A-1": 7, "A-2": 8},
        "fbs_orders": {"A-1": 9, "A-2": 10},
    }

    report = stock_sheet_export._write_marketplace(
        service,
        "sheet-id",
        settings,
        "WB",
        catalog,
        values,
    )

    assert report["updated_cells"] == 8
    assert {update["range"] for update in service.sheets.value_api.updates} == {
        "'WB'!B2:B2",
        "'WB'!B3:B3",
        "'WB'!C2:C2",
        "'WB'!C3:C3",
        "'WB'!D2:D2",
        "'WB'!D3:D3",
        "'WB'!E2:E2",
        "'WB'!E3:E3",
    }
    assert [update["values"][0][0] for update in service.sheets.value_api.updates] == [
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
    ]


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
