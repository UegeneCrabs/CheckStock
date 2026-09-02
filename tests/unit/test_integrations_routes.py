import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app import auth, db, sync_settings
from app.dto.identity import Role
from app.main import create_app
from app.ozon import tokens as ozon_tokens
from app.sync_tracking import run_tracked, set_next_run
from app.wb import tokens as wb_tokens
from app.web.routers import integrations
from app.yandex import tokens as yandex_tokens


def _configure_secret_paths(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    wb_path = tmp_path / "wb.json"
    ozon_path = tmp_path / "ozon.json"
    yandex_path = tmp_path / "yandex.json"
    wb_path.write_text(json.dumps({"rimili": "existing-wb-secret"}), encoding="utf-8")
    ozon_path.write_text(
        json.dumps({"rimili": {"client_id": "12345678", "api_key": "existing-ozon-secret"}}),
        encoding="utf-8",
    )
    yandex_path.write_text(
        json.dumps(
            {
                "rimili": {
                    "api_key": "existing-yandex-secret",
                    "business_id": 99,
                    "campaigns": [{"id": 101, "scheme": "fbs"}],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(wb_tokens, "TOKENS_PATH", wb_path)
    monkeypatch.setattr(ozon_tokens, "TOKENS_PATH", ozon_path)
    monkeypatch.setattr(yandex_tokens, "SECRETS_PATH", yandex_path)
    wb_tokens.reload_tokens()
    ozon_tokens.reload_tokens()
    return wb_path, ozon_path, yandex_path


def test_superadmin_page_never_renders_saved_secrets(container, user_factory, monkeypatch, tmp_path) -> None:
    _configure_secret_paths(monkeypatch, tmp_path)
    application = create_app(container)
    user = user_factory()
    with (
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.get("/admin/integrations")

    assert response.status_code == 200
    assert "API-ключи маркетплейсов" in response.text
    assert 'class="topbar"' not in response.text
    assert 'class="integration-page-head"' not in response.text
    assert "Управление подключениями всех магазинов" not in response.text
    assert "Воронка продаж WB" in response.text
    assert "Закрытие воронки WB" in response.text
    assert "Реклама WB" in response.text
    assert response.text.count("Каждые 15 мин.") >= 2
    assert "Каждый час" not in response.text
    assert "Продажи и реклама" not in response.text
    assert "РНП-аналитика" not in response.text
    assert 'data-sync-history="catalog_sync"' in response.text
    assert 'data-sync-setting-toggle' in response.text
    assert 'data-sync-targets-toggle="stock_sync"' in response.text
    assert "FTP — себестоимость WB" in response.text
    assert "FTP — себестоимость Ozon" in response.text
    assert "Процент выкупа WB" in response.text
    assert "Каждые 4 ч." in response.text
    assert 'data-sync-run="wb_funnel_weekly_metrics_sync"' in response.text
    assert 'data-sync-run="ftp_wb_export"' in response.text
    assert 'data-sync-run="ftp_ozon_export"' in response.text
    assert 'data-marketplace="OZON"' in response.text
    assert 'id="integration-history-dialog"' in response.text
    assert "••••5678" in response.text
    assert "existing-wb-secret" not in response.text
    assert "existing-ozon-secret" not in response.text
    assert "existing-yandex-secret" not in response.text


def test_superadmin_can_replace_and_delete_credentials(
    container, user_factory, monkeypatch, tmp_path
) -> None:
    wb_path, ozon_path, yandex_path = _configure_secret_paths(monkeypatch, tmp_path)
    application = create_app(container)
    user = user_factory()
    with (
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        wb_response = client.put(
            "/api/admin/integrations/rimili/wb",
            json={"api_key": "new-wb-secret"},
        )
        ozon_response = client.put(
            "/api/admin/integrations/rimili/ozon",
            json={"api_key": "new-ozon-secret", "client_id": ""},
        )
        yandex_response = client.delete("/api/admin/integrations/rimili/yandex")

    assert wb_response.status_code == 200
    assert ozon_response.status_code == 200
    assert yandex_response.status_code == 200
    assert json.loads(wb_path.read_text(encoding="utf-8"))["rimili"] == "new-wb-secret"
    assert json.loads(ozon_path.read_text(encoding="utf-8"))["rimili"] == {
        "client_id": "12345678",
        "api_key": "new-ozon-secret",
    }
    yandex_entry = json.loads(yandex_path.read_text(encoding="utf-8"))["rimili"]
    assert "api_key" not in yandex_entry
    assert yandex_entry["business_id"] == 99
    assert yandex_entry["campaigns"] == [{"id": 101, "scheme": "fbs"}]


def test_regular_admin_cannot_view_or_change_credentials(
    container, user_factory, monkeypatch, tmp_path
) -> None:
    _configure_secret_paths(monkeypatch, tmp_path)
    application = create_app(container)
    user = user_factory(role=Role.ADMIN)
    with (
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        assert client.get("/admin/integrations").status_code == 403
        assert (
            client.get("/api/admin/integrations/sync-jobs/catalog_sync/history").status_code
            == 403
        )
        assert (
            client.put(
                "/api/admin/integrations/rimili/wb",
                json={"api_key": "must-not-be-saved"},
            ).status_code
            == 403
        )
        assert client.delete("/api/admin/integrations/rimili/wb").status_code == 403
        assert (
            client.put(
                "/api/admin/integrations/sync-jobs/stock_sync/settings",
                json={"enabled": False},
            ).status_code
            == 403
        )


def test_superadmin_can_configure_export_by_store_and_marketplace(
    container, user_factory
) -> None:
    application = create_app(container)
    user = user_factory()
    with (
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.put(
            "/api/admin/integrations/sync-jobs/stock_sync/settings",
            json={
                "enabled": False,
                "store_slug": "rimili",
                "marketplace": "OZON",
            },
        )

    assert response.status_code == 200, response.text
    config = response.json()["configuration"]
    assert config["summary"] == "Частично: 20 из 21"
    assert "rimili" not in sync_settings.enabled_stores("stock_sync", "OZON")
    assert "rimili" in sync_settings.enabled_stores("stock_sync", "WB")
    stored = db.list_sync_job_settings("stock_sync")
    assert stored == [
        {
            "name": "stock_sync",
            "store_slug": "rimili",
            "marketplace": "OZON",
            "enabled": 0,
            "updated_at": stored[0]["updated_at"],
        }
    ]


def test_export_global_switch_preserves_target_choices(container, user_factory) -> None:
    sync_settings.save_setting(
        "stock_sync",
        enabled=False,
        store_slug="rimili",
        marketplace="OZON",
    )
    application = create_app(container)
    user = user_factory()
    with (
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        disabled = client.put(
            "/api/admin/integrations/sync-jobs/stock_sync/settings",
            json={"enabled": False},
        )
        enabled = client.put(
            "/api/admin/integrations/sync-jobs/stock_sync/settings",
            json={"enabled": True},
        )

    assert disabled.status_code == 200
    assert disabled.json()["configuration"]["summary"] == "Выключена"
    assert enabled.status_code == 200
    assert enabled.json()["configuration"]["summary"] == "Частично: 20 из 21"
    assert "rimili" not in sync_settings.enabled_stores("stock_sync", "OZON")


def test_marketplace_master_switch_disables_all_its_cabinets(container, user_factory) -> None:
    application = create_app(container)
    user = user_factory()
    with (
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.put(
            "/api/admin/integrations/sync-jobs/stock_sync/settings",
            json={"enabled": False, "marketplace": "OZON"},
        )

    assert response.status_code == 200, response.text
    config = response.json()["configuration"]
    assert config["summary"] == "Частично: 14 из 21"
    assert sync_settings.enabled_stores("stock_sync", "OZON") == ()
    assert sync_settings.enabled_stores("stock_sync", "WB") == tuple(sync_settings.STORES)


def test_export_setting_rejects_invalid_target(container, user_factory) -> None:
    application = create_app(container)
    user = user_factory()
    with (
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.put(
            "/api/admin/integrations/sync-jobs/stock_sync/settings",
            json={"enabled": False, "store_slug": "unknown", "marketplace": "WB"},
        )

    assert response.status_code == 400
    assert response.json()["error"] == "Недопустимая настройка магазина или маркетплейса"


def test_tracked_run_records_trigger_status_and_next_run(database_path) -> None:
    result = run_tracked("catalog_sync", "manual", lambda: {"ok": True})
    set_next_run("catalog_sync", 3600)

    assert result == {"ok": True}
    state = {item["name"]: item for item in db.list_sync_job_states()}["catalog_sync"]
    assert state["last_trigger"] == "manual"
    assert state["status"] == "success"
    assert state["last_finished_at"]
    next_run = datetime.fromisoformat(state["next_run_at"])
    assert datetime.now(UTC) + timedelta(minutes=59) < next_run
    history = db.list_sync_job_runs("catalog_sync")
    assert len(history) == 1
    assert history[0]["status"] == "success"
    assert history[0]["trigger"] == "manual"

    partial = run_tracked(
        "stock_sync",
        "scheduled",
        lambda: {"rimili": {"ok": False, "error": "Ozon API: лимит запросов"}},
    )
    assert partial["rimili"]["ok"] is False
    failed_history = db.list_sync_job_runs("stock_sync")
    assert failed_history[0]["status"] == "error"
    assert failed_history[0]["error"] == "rimili: Ozon API: лимит запросов"


def test_sync_history_exposes_exact_error_to_superadmin(
    container, user_factory, database_path
) -> None:
    def fail() -> None:
        raise RuntimeError("WB ответил 403: доступ к ценам запрещён")

    with pytest.raises(RuntimeError, match="доступ к ценам запрещён"):
        run_tracked("unit_economics_1c_sync", "scheduled", fail)

    application = create_app(container)
    user = user_factory()
    with (
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.get(
            "/api/admin/integrations/sync-jobs/unit_economics_1c_sync/history"
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["retention_days"] == 30
    assert payload["job"]["title"] == "Цены для юнит-экономики 1С"
    assert payload["runs"][0]["status"] == "error"
    assert payload["runs"][0]["trigger"] == "scheduled"
    assert payload["runs"][0]["error"] == (
        "RuntimeError: WB ответил 403: доступ к ценам запрещён"
    )


def test_superadmin_can_run_ftp_export_from_integrations(
    container, user_factory, database_path, monkeypatch
) -> None:
    run = mock.Mock(
        return_value={
            "ok": True,
            "platform": "wb",
            "filename": "data.json",
            "items": 42,
        }
    )
    monkeypatch.setattr(integrations.ftp_export, "run_platform", run)
    monkeypatch.setattr(integrations.ftp_export, "is_running", lambda platform: False)
    monkeypatch.setattr(
        integrations.ftp_export_schedule,
        "next_delay_seconds",
        lambda job_name: 3600,
    )
    application = create_app(container)
    user = user_factory()
    with (
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.post("/api/admin/integrations/sync-jobs/ftp_wb_export/run")

    assert response.status_code == 200, response.text
    assert response.json()["result"]["items"] == 42
    run.assert_called_once_with("wb")
    state = {item["name"]: item for item in db.list_sync_job_states()}["ftp_wb_export"]
    assert state["last_trigger"] == "manual"
    assert state["status"] == "success"
    assert state["next_run_at"]


def test_superadmin_can_refresh_wb_buyout_from_integrations(
    container, user_factory, database_path, monkeypatch
) -> None:
    sync_settings.save_setting(
        "wb_funnel_weekly_metrics_sync",
        enabled=False,
        store_slug="toyka",
    )

    def refresh(store_slugs: tuple[str, ...]) -> dict[str, dict]:
        return {
            store_slug: {"store": store_slug, "status": "success", "records": 10}
            for store_slug in store_slugs
        }

    run = mock.Mock(side_effect=refresh)
    monkeypatch.setattr(
        integrations.wb_funnel_orders,
        "sync_weekly_metrics_all",
        run,
    )
    application = create_app(container)
    user = user_factory()
    with (
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.post(
            "/api/admin/integrations/sync-jobs/wb_funnel_weekly_metrics_sync/run"
        )

    assert response.status_code == 200, response.text
    assert response.json()["message"] == "Процент выкупа обновлён: 6 из 6 магазинов"
    selected_stores = run.call_args.args[0]
    assert "toyka" not in selected_stores
    assert selected_stores == tuple(store for store in sync_settings.STORES if store != "toyka")
    state = {
        item["name"]: item for item in db.list_sync_job_states()
    }["wb_funnel_weekly_metrics_sync"]
    assert state["last_trigger"] == "manual"
    assert state["status"] == "success"
    assert state["last_finished_at"]


def test_manual_run_rejects_non_ftp_job(container, user_factory) -> None:
    application = create_app(container)
    user = user_factory()
    with (
        mock.patch.object(application.state.container.identity, "user_for_token", return_value=user),
        TestClient(application, raise_server_exceptions=False) as client,
    ):
        client.cookies.set(auth.SESSION_COOKIE, "session")
        response = client.post("/api/admin/integrations/sync-jobs/catalog_sync/run")

    assert response.status_code == 404
    assert response.json()["error"] == "Ручной запуск этой выгрузки недоступен"
