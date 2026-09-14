"""Observed WB campaign membership and period statistics, separate from all-product ads.

The snapshot is replaced only after every metadata batch succeeds. It deliberately
has a bounded period: older product aggregates cannot reconstruct campaign CTR.
"""

import json
from datetime import UTC, datetime, timedelta

from app.repositories.core import WRITE_LOCK, get_connection
from app.wb import api as wb_api

DETAILS_URL = "https://advert-api.wildberries.ru/api/advert/v2/adverts"
MAX_STATUS_AGE = timedelta(days=1)


def _save(store, status, attempted_at, payload=None):
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            "INSERT INTO wb_advertising_campaign_snapshot (store_slug,status,attempted_at,payload_json) "
            "VALUES (?,?,?,?) ON CONFLICT(store_slug) DO UPDATE SET "
            "status=excluded.status,attempted_at=excluded.attempted_at,payload_json=excluded.payload_json",
            (
                store,
                status,
                attempted_at,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            ),
        )
        conn.commit()


def mark_unavailable(store, attempted_at):
    _save(store, "unavailable", attempted_at)


def refresh(store, token, date_from, date_to, campaign_ids, daily_rows, attempted_at):
    campaigns = []
    for offset in range(0, len(campaign_ids), 50):
        batch = campaign_ids[offset : offset + 50]
        response = wb_api.request("GET", DETAILS_URL, token, params={"ids": ",".join(batch)})
        if not isinstance(response, dict) or not isinstance(response.get("adverts"), list):
            raise ValueError("Invalid campaign metadata response")
        seen = set()
        for campaign in response["adverts"]:
            campaign_id = str(campaign["id"])
            if campaign_id not in batch or campaign_id in seen:
                raise ValueError("Unexpected campaign metadata")
            seen.add(campaign_id)
            if not isinstance(campaign.get("nm_settings"), list) or not isinstance(
                campaign.get("status"), int
            ):
                raise ValueError("Incomplete campaign membership or status")
            campaigns.append(
                {
                    "campaign_id": campaign_id,
                    "status": campaign["status"],
                    "articles": sorted({str(item["nm_id"]) for item in campaign["nm_settings"]}),
                }
            )
        if seen != set(batch):
            raise ValueError("Missing campaign metadata")
    _save(
        store,
        "ok",
        attempted_at,
        {
            "period_from": date_from.isoformat(),
            "period_to": date_to.isoformat(),
            "campaigns": campaigns,
            "daily_rows": daily_rows,
        },
    )


def report(query):
    with get_connection() as conn:
        record = conn.execute(
            "SELECT * FROM wb_advertising_campaign_snapshot WHERE store_slug=?", (query.store,)
        ).fetchone()
    context = {
        "available": False,
        "impressions_basis": "advertising_only",
        "status_basis": "latest_observed_snapshot",
        "max_status_age_hours": 24,
    }
    if record is None or record["status"] != "ok":
        return [], context
    context["status_observed_at"] = record["attempted_at"]
    observed = datetime.fromisoformat(record["attempted_at"])
    if observed.tzinfo is None or not timedelta(0) <= datetime.now(UTC) - observed <= MAX_STATUS_AGE:
        context["unavailable_reason"] = "campaign_status_stale"
        return [], context
    payload = json.loads(record["payload_json"])
    context.update({key: payload[key] for key in ("period_from", "period_to")})
    start, end = query.date_from.isoformat(), query.date_to.isoformat()
    if start < payload["period_from"] or end > payload["period_to"]:
        context["unavailable_reason"] = "requested_period_not_fully_loaded"
        return [], context
    context["available"] = True
    stats = {}
    for row in payload["daily_rows"]:
        if start <= row["day"] <= end:
            bucket = stats.setdefault(
                (row["campaign_id"], row["nm_id"]), {"spend": 0.0, "impressions": 0, "clicks": 0}
            )
            for key in bucket:
                if not row.get("metrics_complete", True) or bucket[key] is None or row.get(key) is None:
                    bucket[key] = None
                else:
                    bucket[key] += row[key]
    rows = []
    for campaign in payload["campaigns"]:
        if query.active_only and campaign["status"] != 9:
            continue
        for article in campaign["articles"]:
            values = stats.get((campaign["campaign_id"], article))
            row = {
                "article": article,
                "campaign_id": campaign["campaign_id"],
                "campaign_status": campaign["status"],
                "is_active": campaign["status"] == 9,
                "updated_at": record["attempted_at"],
                **(values or {"spend": None, "impressions": None, "clicks": None}),
            }
            row["ctr_percent"] = row["clicks"] / row["impressions"] * 100 if row["impressions"] else None
            rows.append(row)
    return rows, context
