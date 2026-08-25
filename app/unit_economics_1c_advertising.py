import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

from app import db
from app.domain import MOSCOW_TIMEZONE
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

logger = logging.getLogger(__name__)

MARKETPLACE = "WB"
DEFAULT_PERIOD_DAYS = 7
SUPPORTED_CAMPAIGN_STATUSES = {7, 9, 11}
STATS_BATCH_SIZE = 50
STATS_BATCH_PAUSE_SECONDS = 20.1
CAMPAIGNS_URL = "https://advert-api.wildberries.ru/adv/v1/promotion/count"
STATS_URL = "https://advert-api.wildberries.ru/adv/v3/fullstats"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _today() -> date:
    return datetime.now(MOSCOW_TIMEZONE).date()


def _items(value: object) -> list:
    return value if isinstance(value, list) else []


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _integer(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _number(value: object) -> float:
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _friendly_error(error: Exception) -> str:
    if isinstance(error, wb_api.WBApiError):
        return error.friendly
    return f"{type(error).__name__}: {error}"


def _campaign_ids(token: str, date_from: date | None = None) -> list[str]:
    response = _mapping(wb_api.request("GET", CAMPAIGNS_URL, token))
    result: list[str] = []
    seen: set[str] = set()
    for raw_group in _items(response.get("adverts")):
        group = _mapping(raw_group)
        status = _integer(group.get("status"))
        if status not in SUPPORTED_CAMPAIGN_STATUSES:
            continue
        for raw_campaign in _items(group.get("advert_list")):
            campaign = _mapping(raw_campaign)
            changed_day = _day(campaign.get("changeTime"))
            if (
                status in {7, 11}
                and date_from is not None
                and changed_day
                and changed_day < date_from.isoformat()
            ):
                continue
            campaign_id = str(_integer(campaign.get("advertId") or campaign.get("advert_id")))
            if campaign_id != "0" and campaign_id not in seen:
                seen.add(campaign_id)
                result.append(campaign_id)
    return result


def _day(value: object) -> str | None:
    raw = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _flatten_stats(response: object, date_from: date, date_to: date) -> list[dict]:
    rows: list[dict] = []
    for raw_campaign in _items(response):
        campaign = _mapping(raw_campaign)
        for raw_day in _items(campaign.get("days")):
            day_row = _mapping(raw_day)
            day_value = _day(day_row.get("date"))
            if day_value is None or day_value < date_from.isoformat() or day_value > date_to.isoformat():
                continue
            for raw_app in _items(day_row.get("apps")):
                app_row = _mapping(raw_app)
                for raw_nm in _items(app_row.get("nm") or app_row.get("nms")):
                    nm = _mapping(raw_nm)
                    nm_id = str(_integer(nm.get("nmId") or nm.get("nmID")))
                    if nm_id == "0":
                        continue
                    rows.append(
                        {
                            "nm_id": nm_id,
                            "day": day_value,
                            "spend": _number(nm.get("sum") if nm.get("sum") is not None else nm.get("spend")),
                            "impressions": _integer(
                                nm.get("views") if nm.get("views") is not None else nm.get("impressions")
                            ),
                            "clicks": _integer(nm.get("clicks")),
                        }
                    )
    return rows


def _load_daily_rows(token: str, date_from: date, date_to: date) -> tuple[list[dict], int]:
    campaign_ids = _campaign_ids(token, date_from)
    grouped: dict[tuple[str, str], dict[str, float | int]] = defaultdict(
        lambda: {"spend": 0.0, "impressions": 0, "clicks": 0}
    )
    for offset in range(0, len(campaign_ids), STATS_BATCH_SIZE):
        if offset:
            time.sleep(STATS_BATCH_PAUSE_SECONDS)
        batch = campaign_ids[offset : offset + STATS_BATCH_SIZE]
        response = wb_api.request(
            "GET",
            STATS_URL,
            token,
            params={
                "ids": ",".join(batch),
                "beginDate": date_from.isoformat(),
                "endDate": date_to.isoformat(),
            },
        )
        for row in _flatten_stats(response, date_from, date_to):
            item = grouped[(row["nm_id"], row["day"])]
            item["spend"] += row["spend"]
            item["impressions"] += row["impressions"]
            item["clicks"] += row["clicks"]
    rows = [
        {
            "nm_id": nm_id,
            "day": day_value,
            "spend": round(float(values["spend"]), 2),
            "impressions": int(values["impressions"]),
            "clicks": int(values["clicks"]),
        }
        for (nm_id, day_value), values in sorted(grouped.items())
    ]
    return rows, len(campaign_ids)


def _record_state(
    store_slug: str,
    *,
    status: str,
    date_from: date,
    date_to: date,
    attempted_at: str,
    rows_saved: int,
    campaigns_count: int,
    error: str | None,
) -> None:
    db.record_unit_economics_1c_advertising_sync_state(
        store_slug,
        status=status,
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        attempted_at=attempted_at,
        rows_saved=rows_saved,
        campaigns_count=campaigns_count,
        error=error,
    )
    db.record_sync_health(
        store_slug,
        MARKETPLACE,
        "unit_economics_1c_advertising",
        status == "ok",
        error,
        attempted_at,
    )


def sync_store(
    store_slug: str,
    date_to: date | None = None,
    period_days: int = DEFAULT_PERIOD_DAYS,
) -> dict:
    if store_slug not in STORES:
        raise ValueError("Неизвестный кабинет")
    date_to = date_to or _today()
    date_from = date_to - timedelta(days=max(1, period_days) - 1)
    attempted_at = _now()
    if not wb_tokens.has_token(store_slug):
        error = "нет WB-токена для кабинета"
        _record_state(
            store_slug,
            status="error",
            date_from=date_from,
            date_to=date_to,
            attempted_at=attempted_at,
            rows_saved=0,
            campaigns_count=0,
            error=error,
        )
        return {"ok": False, "status": "error", "rows": 0, "error": error}

    try:
        token = wb_tokens.get_token(store_slug)
        daily_rows, campaigns_count = _load_daily_rows(token, date_from, date_to)
        excluded_nm_ids = db.get_excluded_nm_ids(store_slug, MARKETPLACE)
        daily_rows = [row for row in daily_rows if row["nm_id"] not in excluded_nm_ids]
        rows = [
            {
                "store_slug": store_slug,
                "marketplace": MARKETPLACE,
                "synced_at": attempted_at,
                **row,
            }
            for row in daily_rows
        ]
        rows_saved = db.replace_unit_economics_1c_daily_advertising(
            store_slug,
            date_from.isoformat(),
            date_to.isoformat(),
            rows,
        )
        _record_state(
            store_slug,
            status="ok",
            date_from=date_from,
            date_to=date_to,
            attempted_at=attempted_at,
            rows_saved=rows_saved,
            campaigns_count=campaigns_count,
            error=None,
        )
        return {
            "ok": True,
            "status": "ok",
            "rows": rows_saved,
            "campaigns": campaigns_count,
            "period_from": date_from.isoformat(),
            "period_to": date_to.isoformat(),
        }
    except Exception as error:
        message = _friendly_error(error)[:1500]
        _record_state(
            store_slug,
            status="error",
            date_from=date_from,
            date_to=date_to,
            attempted_at=attempted_at,
            rows_saved=0,
            campaigns_count=0,
            error=message,
        )
        logger.warning("Реклама юнитки 1С %s не обновлена: %s", store_slug, message)
        return {"ok": False, "status": "error", "rows": 0, "error": message}


def sync_stores(store_slugs: tuple[str, ...], date_to: date | None = None) -> dict[str, dict]:
    stores = tuple(store_slug for store_slug in store_slugs if store_slug in STORES)
    if not stores:
        return {}
    with ThreadPoolExecutor(max_workers=min(4, len(stores))) as executor:
        results = executor.map(lambda store_slug: sync_store(store_slug, date_to), stores)
        return dict(zip(stores, results, strict=True))


def sync_all() -> dict[str, dict]:
    return sync_stores(tuple(STORES))
