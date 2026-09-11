"""Retired UI/API entry points must stay unavailable with existing user records."""

import asyncio
from pathlib import Path

import pytest

from app import auth, background
from app.dto.identity import Role, SectionAccessLevel, SectionName
from app.section_access import access_level, landing_path
from app.web.routers.agent_full import SPECS

RETIRED_PAGES = (
    "/admin/activity", "/admin/activity/data",
    "/sales", "/sales/ephemerides", "/sales/rnp", "/sales/decision-center",
    "/sales/orders.xlsx", "/supply", "/stock-2", "/stock-2/details/frozen",
)
RETIRED_API = (
    "/api/activity/heartbeat",
    "/api/sales", "/api/sales/wb-funnel-orders", "/api/rnp", "/api/rnp/sync",
    "/api/rnp/action", "/api/rnp/strategy", "/api/decision-center",
    "/api/decision-center/sync", "/api/decision-center/status",
    "/api/agent/v1/sales", "/api/agent/v1/funnel", "/api/agent/v1/rnp", "/api/agent/v1/decisions",
)


@pytest.mark.parametrize("path", RETIRED_PAGES + RETIRED_API)
def test_retired_urls_are_not_routes(client, monkeypatch, user_factory, path):
    monkeypatch.setattr(client.app.state.container.identity, "user_for_token", lambda _: user_factory())
    client.cookies.set(auth.SESSION_COOKIE, "retirement-test")
    assert client.get(path).status_code == 404
    if path.startswith("/api/") and not path.startswith("/api/agent/"):
        assert client.post(path, json={}).status_code == 404


def test_remaining_pages_have_no_retired_links_or_assets(client, monkeypatch, user_factory):
    monkeypatch.setattr(client.app.state.container.identity, "user_for_token", lambda _: user_factory())
    client.cookies.set(auth.SESSION_COOKIE, "retirement-test")
    for path in ("/stock", "/profile", "/admin", "/sales/unit-economics-1c"):
        response = client.get(path)
        assert response.status_code == 200, response.text[:500]
        for retired in RETIRED_PAGES:
            assert f'href="{retired}"' not in response.text
        for filename in ("decision-center", "rnp-dashboard", "sales-dashboard", "activity-tracker"):
            assert f"/static/{filename}." not in response.text
        for removed_control in ("theme-toggle", "notifications-trigger", "notifications-panel", "checkstock-theme"):
            assert removed_control not in response.text
    assert client.get("/static/activity-tracker.js").status_code == 404
    assert client.get("/", follow_redirects=False).headers["location"] == "/stock"
    assert not {"sales", "funnel", "rnp", "decisions"} & SPECS.keys()


def test_legacy_access_records_cannot_restore_retired_sections(user_factory):
    user = user_factory(role=Role.SUPERADMIN).model_copy(update={
        "section_access": {section: SectionAccessLevel.WRITE for section in SectionName},
    })
    for section in (SectionName.SALES, SectionName.DECISION_CENTER, SectionName.EPHEMERIDES,
                    SectionName.RNP, SectionName.SUPPLY, SectionName.STOCK_OVERVIEW):
        assert access_level(user, section) is SectionAccessLevel.NONE
    # Existing economy-only accounts still land in the permitted, live section.
    restricted = user.model_copy(update={"role": Role.USER, "section_access": {
        SectionName.STOCK: SectionAccessLevel.NONE,
        SectionName.UNIT_ECONOMICS_1C: SectionAccessLevel.READ,
    }})
    assert landing_path(restricted) == "/sales/unit-economics-1c"


def test_shared_data_jobs_remain_registered():
    names = {job.name for job in background._jobs(asyncio.Event())}
    assert {
        "catalog_sync", "stock_sync", "wb_advertising_sync", "wb_funnel_orders_sync",
        "wb_funnel_previous_day_close_00_msk", "wb_funnel_weekly_metrics_sync",
        "yandex_orders_sync", "yandex_reputation_sync", "yandex_advertising_sync",
        "yandex_buyout_sync", "yandex_orders_previous_day_close_00_msk",
        "marketplace_stock_sync_and_history_23_msk", "fulfillment_stock_history_00_msk",
        "unit_economics_1c_daily_margin_snapshot_00_msk", "stock_sheet_export",
        "unit_economics_1c_source_sync", "unit_economics_1c_reference_sync",
    } <= names
    assert not any("decision" in name or "rnp" in name for name in names)
    root = Path(__file__).resolve().parents[2]
    for name in ("decision_center", "rnp", "rnp_analytics"):
        assert not (root / "app" / f"{name}.py").exists()
