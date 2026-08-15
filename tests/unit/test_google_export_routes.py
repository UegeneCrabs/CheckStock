from unittest import mock

from fastapi.testclient import TestClient

from app import auth, stock_sheet_export
from app.dto.identity import Role
from app.main import create_app
from app.repositories import stock_sheet_export as repository
from app.web.routers import google_export


def _form_data() -> dict[str, str]:
    data = {
        "enabled": "1",
        "schedule_kind": "daily",
        "weekday": "6",
        "run_time": "01:00",
        "spreadsheet_url": stock_sheet_export.RIMILI_SPREADSHEET_URL,
        "wb_key_column": "Артикул WB",
        "ozon_key_column": "Артикул Ozon",
        "yandex_key_column": "Артикул Яндекс",
    }
    for marketplace in repository.MARKETPLACES:
        for metric in repository.METRICS:
            prefix = f"{google_export.MARKETPLACE_FORM_PREFIXES[marketplace]}_{metric}"
            data[f"{prefix}_sheet"] = marketplace
            data[f"{prefix}_column"] = stock_sheet_export.METRIC_LABELS[metric]
    return data


def test_superadmin_can_open_and_save_google_export_settings(container, user_factory) -> None:
    application = create_app(container)
    user = user_factory()
    with (
        mock.patch("app.background._jobs", return_value=()),
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        page = client.get("/admin/google-export")
        response = client.post("/admin/google-export/rimili", data=_form_data())

    assert page.status_code == 200
    assert "Выгрузка остатков и FBS-заказов" in page.text
    assert "Яндекс Маркет" in page.text
    assert response.status_code == 200
    saved = stock_sheet_export.get_settings("rimili")
    assert saved.schedule_kind == "daily"
    assert saved.target("WB", "ff_stock").key_column_name == "Артикул WB"


def test_manual_export_endpoint_reports_success(container, user_factory) -> None:
    application = create_app(container)
    user = user_factory()
    with (
        mock.patch("app.background._jobs", return_value=()),
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        mock.patch.object(stock_sheet_export, "run_store", return_value={"updated": 12}),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.post("/admin/google-export/rimili/run")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_regular_admin_cannot_access_google_export_settings(container, user_factory) -> None:
    application = create_app(container)
    user = user_factory(role=Role.ADMIN)
    with (
        mock.patch("app.background._jobs", return_value=()),
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        assert client.get("/admin/google-export").status_code == 403
        assert client.post("/admin/google-export/rimili", data=_form_data()).status_code == 403
