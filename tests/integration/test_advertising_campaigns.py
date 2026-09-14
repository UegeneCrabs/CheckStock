from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pytest

from app.economics.wb import advertising, campaigns
from app.web.routers.agent_full import ReportQuery


def query(**extra):
    return ReportQuery(store="rimili", date_from=date(2026, 9, 9), date_to=date(2026, 9, 9), **extra)


def seed(monkeypatch, *, observed=None):
    observed = observed or datetime.now(UTC).isoformat()
    metadata = {
        "adverts": [
            {"id": 1, "status": 9, "nm_settings": [{"nm_id": 100}, {"nm_id": 200}, {"nm_id": 300}]},
            {"id": 2, "status": 11, "nm_settings": [{"nm_id": 100}]},
        ]
    }
    monkeypatch.setattr(campaigns.wb_api, "request", Mock(return_value=metadata))
    rows = [
        {
            "campaign_id": "1",
            "nm_id": "100",
            "day": "2026-09-09",
            "spend": 10,
            "impressions": 100,
            "clicks": 2,
        },
        {
            "campaign_id": "1",
            "nm_id": "100",
            "day": "2026-09-09",
            "spend": 20,
            "impressions": 100,
            "clicks": 4,
        },
        {
            "campaign_id": "2",
            "nm_id": "100",
            "day": "2026-09-09",
            "spend": 99,
            "impressions": 100,
            "clicks": 90,
        },
        {"campaign_id": "1", "nm_id": "300", "day": "2026-09-09", "spend": 0, "impressions": 0, "clicks": 0},
        # Removed from the campaign: historical statistics do not prove current membership.
        {
            "campaign_id": "1",
            "nm_id": "999",
            "day": "2026-09-09",
            "spend": 10,
            "impressions": 100,
            "clicks": 1,
        },
    ]
    campaigns.refresh("rimili", "test", date(2026, 9, 8), date(2026, 9, 9), ["1", "2"], rows, observed)


def test_current_membership_and_campaign_ctr_are_not_all_product_ctr(database_path, monkeypatch):
    seed(monkeypatch)
    rows, context = campaigns.report(query())
    assert context["available"] is True
    assert {r["article"] for r in rows} == {"100", "200", "300"}
    first = next(r for r in rows if r["article"] == "100")
    assert first["ctr_percent"] == 3
    assert first["spend"] == 30
    assert next(r for r in rows if r["article"] == "200")["ctr_percent"] is None
    assert next(r for r in rows if r["article"] == "300")["ctr_percent"] is None
    all_rows, _ = campaigns.report(query(active_only=False))
    assert len(all_rows) == 4
    assert next(r for r in all_rows if r["campaign_id"] == "2")["ctr_percent"] == 90
    assert campaigns.report(query().model_copy(update={"store": "tris"}))[1]["available"] is False


def test_stale_failed_or_uncovered_snapshots_are_not_negative_results(database_path, monkeypatch):
    seed(monkeypatch, observed=(datetime.now(UTC) - timedelta(days=2)).isoformat())
    assert campaigns.report(query())[1]["unavailable_reason"] == "campaign_status_stale"
    seed(monkeypatch)
    wider = query().model_copy(update={"date_from": date(2026, 9, 7)})
    assert campaigns.report(wider)[1]["unavailable_reason"] == "requested_period_not_fully_loaded"
    campaigns.mark_unavailable("rimili", datetime.now(UTC).isoformat())
    assert campaigns.report(query())[0] == []
    assert campaigns.report(query())[1]["available"] is False


def test_partial_metadata_cannot_be_published(database_path, monkeypatch):
    campaigns.mark_unavailable("rimili", datetime.now(UTC).isoformat())
    monkeypatch.setattr(campaigns.wb_api, "request", Mock(return_value={"adverts": []}))
    with pytest.raises(ValueError, match="Missing campaign"):
        campaigns.refresh(
            "rimili", "test", date(2026, 9, 8), date(2026, 9, 9), ["1"], [], datetime.now(UTC).isoformat()
        )
    assert campaigns.report(query())[1]["available"] is False


def test_sync_keeps_campaign_stats_and_existing_product_aggregation(database_path, monkeypatch):
    def request(method, url, token, params=None):
        if url == advertising.CAMPAIGNS_URL:
            return {"adverts": [{"status": 9, "advert_list": [{"advertId": 1}]}]}
        if url == campaigns.DETAILS_URL:
            return {"adverts": [{"id": 1, "status": 9, "nm_settings": [{"nm_id": 100}]}]}
        assert url == advertising.STATS_URL
        return [
            {
                "advertId": 1,
                "days": [
                    {
                        "date": "2026-09-09",
                        "apps": [{"nm": [{"nmId": 100, "sum": 10, "views": 100, "clicks": 2}]}],
                    }
                ],
            }
        ]

    monkeypatch.setattr(advertising.wb_tokens, "has_token", lambda _: True)
    monkeypatch.setattr(advertising.wb_tokens, "get_token", lambda _: "test")
    monkeypatch.setattr(advertising.wb_api, "request", request)
    result = advertising.sync_store("rimili", date(2026, 9, 9))
    assert result["ok"]
    assert campaigns.report(query())[0][0]["ctr_percent"] == 2
    rows = advertising.db.get_unit_economics_1c_daily_advertising(("rimili",), "2026-09-09", "2026-09-09")
    assert rows[0]["impressions"] == 100
