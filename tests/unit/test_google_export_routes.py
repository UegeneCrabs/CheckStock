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
        data[f"{prefix}_sheet_name"] = f"Остатки {marketplace}"
        data[f"{prefix}_fbs_orders_sheet_name"] = f"Заказы {marketplace}"
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
    assert "Доступ к таблице" in page.text
    assert 'class="topbar"' not in page.text
    assert 'class="export-page-head"' not in page.text
    assert "Яндекс Маркет" in page.text
    assert "TOYKA добавляется автоматически" in page.text
    assert response.status_code == 200
    saved = stock_sheet_export.get_settings("rimili")
    assert saved.schedule_kind == "daily"
    assert saved.spreadsheet_url_for("WB").endswith("/wb-sheet-id/edit")
    assert saved.spreadsheet_url_for("OZON").endswith("/ozon-sheet-id/edit")
    assert saved.target("WB", "ff_stock").key_column_name == "АРТИКУЛ"
    assert saved.target("WB", "fbo_stock").value_column_name == "ТЕКУЩИЙ СТОК В ПРОДАЖЕ FBO"
    assert saved.target("OZON", "fbs_stock").sheet_name == "Остатки OZON"
    assert saved.target("YANDEX MARKET", "fbs_orders").sheet_name == "Заказы YANDEX MARKET"


def test_saved_sheet_is_used_for_all_stock_columns_after_reload(container, user_factory) -> None:
    application = create_app(container)
    user = user_factory()
    data = _form_data()
    data["wb_sheet_name"] = "Полный остаток WB"

    with (
        mock.patch("app.background._jobs", return_value=()),
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.post("/admin/google-export/rimili", data=data)
        reloaded_page = client.get("/admin/google-export")

    assert response.status_code == 200
    assert reloaded_page.status_code == 200
    saved = stock_sheet_export.get_settings("rimili")
    wb_targets = [target for target in saved.targets if target.marketplace == "WB"]
    assert [target.metric for target in wb_targets] == list(repository.METRICS)
    assert {
        target.sheet_name for target in wb_targets if target.metric in repository.STOCK_METRICS
    } == {"Полный остаток WB"}
    assert saved.target("WB", "fbs_orders").sheet_name == "Заказы WB"
    assert 'name="wb_sheet_name" value="Полный остаток WB"' in reloaded_page.text


def test_fbs_order_sheet_can_be_left_empty(container, user_factory) -> None:
    application = create_app(container)
    user = user_factory()
    data = _form_data()
    data["yandex_fbs_orders_sheet_name"] = ""

    with (
        mock.patch("app.background._jobs", return_value=()),
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.post("/admin/google-export/rimili", data=data)

    assert response.status_code == 200
    saved = stock_sheet_export.get_settings("rimili")
    assert saved.target("YANDEX MARKET", "fbs_orders").sheet_name == ""


def test_stock_sheet_can_be_left_empty(container, user_factory) -> None:
    application = create_app(container)
    user = user_factory()
    data = _form_data()
    data["ozon_sheet_name"] = ""

    with (
        mock.patch("app.background._jobs", return_value=()),
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.post("/admin/google-export/rimili", data=data)
        reloaded_page = client.get("/admin/google-export")

    assert response.status_code == 200
    assert stock_sheet_export.get_settings("rimili").target("OZON", "ff_stock").sheet_name == ""
    assert 'name="ozon_sheet_name" value=""' in reloaded_page.text
    assert 'data-marketplace="OZON" data-export-kind="stocks"' in reloaded_page.text
    assert 'data-marketplace="OZON" data-export-kind="fbs_orders"' in reloaded_page.text


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


def test_manual_export_endpoint_can_run_one_marketplace_export(container, user_factory) -> None:
    application = create_app(container)
    user = user_factory()
    runner = mock.Mock(return_value={"updated": 12})
    with (
        mock.patch("app.background._jobs", return_value=()),
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        mock.patch.object(stock_sheet_export, "run_store", runner),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.post(
            "/admin/google-export/rimili/run",
            data={"marketplace": "OZON", "export_kind": "fbs_orders"},
        )

    assert response.status_code == 200
    runner.assert_called_once_with(
        "rimili",
        marketplace="OZON",
        export_kind="fbs_orders",
    )


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
