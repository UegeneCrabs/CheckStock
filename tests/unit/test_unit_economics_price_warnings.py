from unittest import mock

from app.web.routers import unit_economics


def test_partial_price_sync_does_not_report_broken_api_key() -> None:
    with mock.patch.object(
        unit_economics.db,
        "list_unit_economics_1c_price_sync_states",
        return_value=[
            {
                "store_slug": "toyka",
                "status": "partial",
                "rows_saved": 47,
                "error": "нет доступа к части цен; использованы доступные данные",
            }
        ],
    ):
        warnings = unit_economics._unit_economics_1c_price_warnings(("toyka",))

    assert warnings == []


def test_failed_price_sync_keeps_api_key_warning() -> None:
    with mock.patch.object(
        unit_economics.db,
        "list_unit_economics_1c_price_sync_states",
        return_value=[
            {
                "store_slug": "toyka",
                "status": "error",
                "rows_saved": 0,
                "error": "доступ запрещён — токен не подходит для этого метода",
            }
        ],
    ):
        warnings = unit_economics._unit_economics_1c_price_warnings(("toyka",))

    assert warnings == [
        {
            "store_slug": "toyka",
            "store_name": "TOYKA",
            "status": "error",
            "message": "доступ запрещён — токен не подходит для этого метода",
        }
    ]
