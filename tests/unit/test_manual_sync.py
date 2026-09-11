import subprocess
import sys
import time
from contextlib import contextmanager
from threading import Event
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app import auth, background, db, manual_sync, sync_locks, sync_settings, sync_tracking
from app.dto.identity import Role
from app.main import create_app
from app.sync_catalog import job_definitions


def wait_finished(name):
    deadline = time.monotonic() + 5
    while sync_locks.is_running(name):
        assert time.monotonic() < deadline
        time.sleep(0.01)


@contextmanager
def admin_client(container, user):
    app = create_app(container)
    with mock.patch.object(app.state.container.identity, "user_for_token", return_value=user), TestClient(app) as client:
        client.cookies.set(auth.SESSION_COOKIE, "session")
        yield client


def test_every_registered_job_has_a_button_and_manual_entrypoint(container, user_factory):
    with admin_client(container, user_factory()) as client:
        page = client.get("/admin/integrations").text
    for definition in job_definitions():
        assert definition.manual_run
        assert page.count(f'data-sync-run="{definition.name}"') == 1
        assert callable(manual_sync.callback_for(definition.name))


@pytest.mark.parametrize("name", [job.name for job in job_definitions()])
def test_every_button_starts_a_tracked_background_run(container, user_factory, monkeypatch, name):
    callback = mock.Mock(return_value={"ok": True})
    monkeypatch.setattr(manual_sync, "callback_for", lambda job: callback)
    with admin_client(container, user_factory()) as client:
        response = client.post(f"/api/admin/integrations/sync-jobs/{name}/run")
        assert response.status_code == 202
        wait_finished(name)
        states = client.get("/api/admin/integrations/sync-jobs").json()["states"]
    state = next(row for row in states if row["name"] == name)
    assert state["status"] == "success"
    assert state["last_trigger"] == "manual"
    assert not state["running"]
    assert db.list_sync_job_runs(name)[0]["id"] == response.json()["run_id"]
    callback.assert_called_once_with()


def test_queued_run_does_not_block_http_and_cannot_overlap_scheduled_or_manual(container, user_factory, monkeypatch):
    started, release = Event(), Event()

    def slow():
        started.set()
        assert release.wait(5)
        return {"rimili": {"ok": False, "error": "API temporary failure"}}

    monkeypatch.setattr(manual_sync, "callback_for", lambda name: slow)
    name = "yandex_advertising_sync"
    try:
        with admin_client(container, user_factory()) as client:
            response = client.post(f"/api/admin/integrations/sync-jobs/{name}/run")
            assert response.status_code == 202
            assert started.wait(1)
            assert client.post(f"/api/admin/integrations/sync-jobs/{name}/run").status_code == 409
            with pytest.raises(sync_locks.SyncJobBusyError):
                sync_tracking.run_tracked(name, "scheduled", lambda: pytest.fail("Overlapping run"))
            states = client.get("/api/admin/integrations/sync-jobs").json()["states"]
            assert next(row for row in states if row["name"] == name)["running"]
    finally:
        release.set()
        wait_finished(name)
    runs = db.list_sync_job_runs(name)
    assert len(runs) == 1
    assert runs[0]["status"] == "error"
    assert "API temporary failure" in runs[0]["error"]
    sync_tracking.run_tracked(name, "manual", lambda: {"ok": True})
    assert db.list_sync_job_runs(name)[0]["status"] == "success"


def test_manual_run_ignores_auto_switch_but_keeps_store_and_marketplace_selection(database_path):
    name = "catalog_sync"
    sync_settings.save_setting(name, enabled=False)
    sync_settings.save_setting(name, enabled=False, marketplace="OZON")
    sync_settings.save_setting(name, enabled=False, store_slug="tris", marketplace="WB")
    before = db.list_sync_job_settings(name)
    with (mock.patch.object(background.wb_catalog, "sync_all", return_value={}) as wb,
          mock.patch.object(background.ozon_catalog, "sync_all", return_value={}) as ozon,
          mock.patch.object(background.ya_catalog, "sync_all", return_value={}) as yandex):
        manual_sync.callback_for(name)()
    assert "tris" not in wb.call_args.args[0]
    ozon.assert_called_once_with(())
    assert "tris" in yandex.call_args.args[0]
    assert db.list_sync_job_settings(name) == before
    assert sync_settings.enabled_stores(name, "WB") == ()


def test_manual_source_and_reference_loaders_bypass_due_checks(database_path):
    with (mock.patch.object(background.unit_reference_sync, "sync_all", return_value={}) as refs,
          mock.patch.object(background.unit_economics_1c.price_sync, "sync_stores", return_value={}) as prices,
          mock.patch.object(background.token_watch, "refresh_token_info") as tokens,
          mock.patch.object(background.stock_sheet_export, "run_store", return_value={}) as sheets):
        for name in ("unit_economics_1c_reference_sync", "unit_economics_1c_sync", "wb_token_check", "stock_sheet_export"):
            manual_sync.callback_for(name)()
    refs.assert_called_once_with(force=True)
    assert len(prices.call_args.args[0]) == len(sync_settings.STORES)
    assert len(tokens.call_args.args[0]) == len(sync_settings.STORES)
    assert sheets.call_count == len(sync_settings.STORES)


def test_manual_price_and_novelty_call_existing_collectors_without_forcing_newness(database_path):
    with (mock.patch.object(background.ya_product_novelty, "sync_all", return_value={}) as novelty,
          mock.patch.object(manual_sync, "_storefront_now", return_value={}) as prices):
        manual_sync.callback_for("yandex_product_novelty_sync")()
        manual_sync.callback_for("yandex_storefront_prices_sync")()
    novelty.assert_called_once_with(tuple(sync_settings.STORES))
    prices.assert_called_once_with()


def test_result_tracking_keeps_partial_errors_but_does_not_treat_store_names_as_errors(database_path):
    good = {"report": {"store_slugs": ["rimili", "tris"], "marketplaces": [{"ok": True}]}}
    sync_tracking.run_tracked("sheets", "manual", lambda: good)
    assert db.list_sync_job_runs("sheets")[0]["status"] == "success"
    with mock.patch.object(background.advertising_sync, "sync_stores", return_value={"tris": {"ok": False, "error": "denied"}}):
        sync_tracking.run_tracked("wb_advertising_sync", "manual", manual_sync.callback_for("wb_advertising_sync"))
    assert "tris: denied" == db.list_sync_job_runs("wb_advertising_sync")[0]["error"]


def test_buttons_reject_unselected_targets_and_unauthorized_user(container, user_factory, monkeypatch):
    queue = mock.Mock()
    monkeypatch.setattr("app.web.routers.integrations.queue_tracked", queue)
    sync_settings.save_setting("yandex_orders_sync", marketplace="YANDEX MARKET", enabled=False)
    with admin_client(container, user_factory()) as client:
        response = client.post("/api/admin/integrations/sync-jobs/yandex_orders_sync/run")
        assert response.status_code == 409
    with admin_client(container, user_factory(role=Role.USER)) as client:
        assert client.post("/api/admin/integrations/sync-jobs/stock_sync/run").status_code == 403
        assert client.get("/api/admin/integrations/sync-jobs").status_code == 403
    queue.assert_not_called()


def test_failed_queue_submission_releases_lock_and_is_visible_in_history(database_path):
    with mock.patch.object(sync_tracking.Thread, "start", side_effect=RuntimeError("cannot start thread")):
        with pytest.raises(RuntimeError, match="cannot start thread"):
            sync_tracking.queue_tracked("catalog_sync", lambda: None)
    assert not sync_locks.is_running("catalog_sync")
    assert db.list_sync_job_runs("catalog_sync")[0]["status"] == "error"


def test_job_lock_is_shared_with_another_process(database_path):
    code = "from pathlib import Path; from app.repositories import core; from app import sync_locks; import sys; core.DB_PATH=Path(sys.argv[1]); print(int(sync_locks.is_running('shared')))"
    with sync_locks.hold("shared"):
        result = subprocess.run([sys.executable, "-c", code, str(database_path)], capture_output=True, text=True, check=True)
        assert result.stdout.strip() == "1"
    result = subprocess.run([sys.executable, "-c", code, str(database_path)], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "0"


def test_postgres_lock_is_held_until_worker_finishes_and_busy_connections_are_closed(monkeypatch):
    database = mock.Mock(dialect_name="postgresql")
    connection = database.connect.return_value
    connection.execute.return_value.fetchone.return_value = {"acquired": True}
    monkeypatch.setattr(sync_locks, "database_for_path", lambda path: database)
    handle = sync_locks.acquire("catalog_sync")
    key = connection.execute.call_args.args[1][0]
    assert -(2**63) <= key < 2**63
    connection.close.assert_not_called()
    handle.close()
    connection.execute.assert_called_with("SELECT pg_advisory_unlock(?)", (key,))
    connection.close.assert_called_once_with()
    connection.reset_mock()
    connection.execute.return_value.fetchone.return_value = {"acquired": False}
    with pytest.raises(sync_locks.SyncJobBusyError):
        sync_locks.acquire("catalog_sync")
    connection.close.assert_called_once_with()


def test_storefront_manual_run_uses_existing_parser_with_only_selected_stores(database_path, monkeypatch, tmp_path):
    from playwright import sync_api

    from scripts import parse_yandex_storefront_prices as collector

    monkeypatch.setattr(collector, "ROOT", tmp_path)
    monkeypatch.setattr(manual_sync.yandex_assortment, "load_active_products", lambda: {("rimili", "A"), ("tris", "B")})
    sync_settings.save_setting(collector.JOB, enabled=False)
    sync_settings.save_setting(collector.JOB, enabled=False, store_slug="tris", marketplace="YANDEX MARKET")
    playwright = mock.MagicMock()
    context = playwright.chromium.launch_persistent_context.return_value
    page = mock.Mock()
    context.pages = [page]
    manager = mock.MagicMock()
    manager.__enter__.return_value = playwright
    monkeypatch.setattr(sync_api, "sync_playwright", lambda: manager)
    with mock.patch.object(collector, "run_once", return_value={"ok": True, "status": "complete"}) as run:
        result = manual_sync.callback_for(collector.JOB)()
    assert result["ok"]
    browser, args = run.call_args.args
    assert browser.page is page
    assert args.article == ["rimili:A"]
    assert not args.loop
    assert args.headless
    assert not args.prepare_only
    assert playwright.chromium.launch_persistent_context.call_args.kwargs["headless"]
    context.close.assert_called_once_with()
