from unittest import mock

from fastapi.testclient import TestClient

from app import auth, stock_sheet_export
from app.dto.identity import Role
from app.main import create_app
from app.repositories import stock_sheet_export as repository
from app.web.routers import google_export


def _form_data() -> dict[str, str | list[str]]:
    data: dict[str, str | list[str]] = {
        "enabled": "1",
        "schedule_kind": "daily",
        "weekday": "6",
        "run_time": "01:00",
    }
    for marketplace in repository.MARKETPLACES:
        prefix = google_export.MARKETPLACE_FORM_PREFIXES[marketplace]
        data[f"{prefix}_spreadsheet_url"] = f"https://docs.google.com/spreadsheets/d/{prefix}-sheet-id/edit"
        metrics = repository.allowed_metrics(marketplace)
        data[f"{prefix}_target_metric"] = list(metrics)
        data[f"{prefix}_target_sheet"] = [marketplace] * len(metrics)
        data[f"{prefix}_target_key"] = [f"Артикул {marketplace}"] * len(metrics)
        data[f"{prefix}_target_value"] = [stock_sheet_export.METRIC_LABELS[metric] for metric in metrics]
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
    assert "Выгрузка остатков и заказов" in page.text
    assert "Яндекс Маркет" in page.text
    assert response.status_code == 200
    saved = stock_sheet_export.get_settings("rimili")
    assert saved.schedule_kind == "daily"
    assert saved.spreadsheet_url_for("WB").endswith("/wb-sheet-id/edit")
    assert saved.spreadsheet_url_for("OZON").endswith("/ozon-sheet-id/edit")
    assert saved.target("WB", "ff_stock").key_column_name == "Артикул WB"
    assert saved.target("WB", "fbo_stock").value_column_name == "Текущий сток в продаже FBO"
    assert saved.target("OZON", "fbs_orders").value_column_name == "Заказы по ФБС"


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
