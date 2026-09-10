"""Read Yandex reports into a separate cache without changing the existing ledgers."""

import json
import logging
import math
import time
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from threading import Lock

from app import sales
from app.config import settings
from app.domain import MOSCOW_TIMEZONE
from app.repositories import unit_economics_yandex as repository
from app.stores import STORES
from app.yandex import api, tokens

logger = logging.getLogger(__name__)
SYNC_INTERVAL_SECONDS = {"orders": 15 * 60, "reputation": 24 * 60 * 60, "advertising": 15 * 60}
RECENT_ORDER_DAYS = 7
SOURCES = ("orders", "reputation", "advertising")
_REPORT_LOCKS: dict[int, Lock] = {}
_REPORT_LOCKS_GUARD = Lock()


def _number(value: object) -> float:
    try:
        result = float(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError) as error:
        raise ValueError("В отчёте ЯМ отсутствует числовой показатель") from error
    if not math.isfinite(result) or result < 0:
        raise ValueError("В отчёте ЯМ некорректный числовой показатель")
    return result


def resolve_business_id(store_slug: str, api_key: str) -> int:
    configured = tokens.get_business_id(store_slug)
    if configured:
        return configured
    campaign_ids = {row["id"] for row in tokens.get_campaigns(store_slug)}
    ids = {
        int((row.get("business") or {}).get("id") or row.get("businessId") or 0)
        for row in api.get_campaigns(api_key)
        if not campaign_ids or row.get("id") in campaign_ids
    } - {0}
    if len(ids) != 1:
        raise ValueError("Укажите однозначный business_id ЯМ в настройках кабинета")
    return ids.pop()


def _report_rows(value: object, identifier: str):
    if isinstance(value, dict):
        if identifier in value:
            yield value
        else:
            for child in value.values():
                yield from _report_rows(child, identifier)
    elif isinstance(value, list):
        for child in value:
            yield from _report_rows(child, identifier)


def download_report(url: str, sheet: str, identifier: str) -> list[dict]:
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("ЯМ не вернул HTTPS-ссылку на отчёт")
    with urllib.request.urlopen(url, timeout=settings.rnp_report_download_timeout_seconds) as response:
        content = response.read()
    rows = []
    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = [name for name in archive.namelist() if name.rsplit("/", 1)[-1] == sheet + ".json"]
        if len(names) != 1:
            raise ValueError(f"В отчёте ЯМ не найден лист {sheet}")
        data = json.loads(archive.read(names[0]).decode("utf-8-sig"))
        rows.extend(_report_rows(data, identifier))
        if (
            not rows
            and data not in ([], {})
            and not (isinstance(data, dict) and any(value == [] for value in data.values()))
        ):
            raise ValueError(f"Неизвестная структура отчёта ЯМ: {sheet}")
    return rows


def load_report(api_key: str, report: str, payload: dict, sheet: str, identifier: str) -> list[dict]:
    """Serialize report generation per business while allowing orders to sync independently."""
    business_id = int(payload["businessId"])
    with _REPORT_LOCKS_GUARD:
        report_lock = _REPORT_LOCKS.setdefault(business_id, Lock())
    with report_lock:
        return _load_report(api_key, report, payload, sheet, identifier)


def _load_report(api_key: str, report: str, payload: dict, sheet: str, identifier: str) -> list[dict]:
    generated = api.request(
        f"/v2/reports/{report}/generate", api_key, payload=payload, params={"format": "JSON"}
    )
    report_id = generated.get("reportId")
    if not report_id:
        raise ValueError("ЯМ не вернул идентификатор отчёта")
    for attempt in range(settings.rnp_report_poll_attempts):
        info = api.request(f"/v2/reports/info/{report_id}", api_key, method="GET")
        if info.get("status") == "DONE":
            return download_report(str(info.get("file") or ""), sheet, identifier)
        if info.get("status") == "FAILED":
            raise RuntimeError("ЯМ не смог сформировать отчёт")
        if attempt + 1 < settings.rnp_report_poll_attempts:
            time.sleep(settings.rnp_report_poll_interval_seconds)
    raise TimeoutError("Отчёт ЯМ ещё формируется")


def load_reputation(api_key: str, business_id: int) -> list[dict]:
    rows = load_report(api_key, "goods-feedback", {"businessId": business_id}, "paid_opinion_models", "sku")
    result = {}
    for row in rows:
        sku = str(row["sku"]).strip()
        rating = _number(row["rating"]) if row.get("rating") not in (None, "", "—", "-") else None
        if rating is not None and rating > 5:
            raise ValueError("Некорректный рейтинг ЯМ")
        count = int(_number(row["currentOpinionCount"]))
        result[sku] = {"sku": sku, "rating": rating, "reviews_count": count}
    return list(result.values())


def load_advertising(api_key: str, business_id: int, start: date, end: date) -> list[dict]:
    payload = {"businessId": business_id, "dateFrom": start.isoformat(), "dateTo": end.isoformat()}
    boost = load_report(api_key, "boost-consolidated", payload, "business_boost_consolidated", "shopSku")
    shows = load_report(
        api_key,
        "shows-boost",
        {**payload, "attributionType": "CLICKS"},
        "business_shows_boost_consolidated_offers",
        "offerId",
    )
    grouped = defaultdict(lambda: {"spend": 0.0, "impressions": 0, "clicks": 0})
    for rows, identifier, spend, impressions, clicks in (
        (boost, "shopSku", "billedAmount", "showsWithFee", "clicksVendorWithFee"),
        (shows, "offerId", "cost", "shows", "clicks"),
    ):
        for row in rows:
            item = grouped[str(row[identifier]).strip()]
            item["spend"] += _number(row.get(spend))
            item["impressions"] += int(_number(row.get(impressions)))


            click_count = row[clicks]
            item["clicks"] += int(_number(0 if click_count is None else click_count))
    return [
        {"article": article, **values, "spend": round(values["spend"], 2)}
        for article, values in grouped.items()
    ]


def load_orders(store_slug: str, api_key: str, business_id: int, start: date, end: date) -> list[dict]:
    orders = api.get_business_orders(
        api_key, business_id, start.isoformat(), (end + timedelta(days=1)).isoformat()
    )
    lines = sales._normalize_yandex(store_slug, orders)
    unique_lines = {(line["order_key"], line["line_key"]): line for line in lines}
    grouped = defaultdict(
        lambda: dict.fromkeys(
            ("orders_count", "orders_amount", "cancel_count", "cancel_amount", "sold_count"), 0
        )
    )
    for line in unique_lines.values():
        day = str(line["ordered_at"])[:10]
        if not start.isoformat() <= day <= end.isoformat():
            continue
        item = grouped[(line["article"], day)]
        for field, source in (
            ("orders_count", "quantity"),
            ("orders_amount", "order_amount"),
            ("cancel_count", "cancelled_quantity"),
            ("cancel_amount", "cancelled_amount"),
            ("sold_count", "sold_quantity"),
        ):
            item[field] += line[source]
    return [{"article": article, "day": day, **values} for (article, day), values in grouped.items()]


def sync_store(store_slug: str, source: str, today: date | None = None) -> dict:
    """Refresh exactly one source without requesting or changing the other snapshots."""
    if store_slug not in STORES:
        raise ValueError("Неизвестный кабинет")
    if source not in SOURCES:
        raise ValueError("Неизвестный источник юнит-экономики ЯМ")
    if not tokens.has_credentials(store_slug):
        return {"ok": False, "status": "not_configured"}
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    now = datetime.now(UTC).isoformat()
    try:
        api_key = tokens.get_api_key(store_slug)
        business_id = resolve_business_id(store_slug, api_key)
        if source == "orders":
            start, end = today - timedelta(days=RECENT_ORDER_DAYS - 1), today
            rows = load_orders(store_slug, api_key, business_id, start, end)
        elif source == "reputation":
            start = end = today
            rows = load_reputation(api_key, business_id)
        else:
            start, end = today - timedelta(days=7), today - timedelta(days=1)
            rows = load_advertising(api_key, business_id, start, end)
        repository.save_snapshot(store_slug, source, rows, start.isoformat(), end.isoformat(), now)
        return {"ok": True, "source": source, "rows": len(rows)}
    except Exception as error:
        message = str(error)[:700]
        repository.record_error(store_slug, source, message, now)
        logger.warning("Юнит-экономика ЯМ %s/%s: %s", store_slug, source, message)
        return {"ok": False, "source": source, "error": message}


def sync_all(source: str, store_slugs: tuple[str, ...] | None = None) -> dict:
    if source not in SOURCES:
        raise ValueError("Неизвестный источник юнит-экономики ЯМ")
    return {
        slug: sync_store(slug, source)
        for slug in (tuple(STORES) if store_slugs is None else store_slugs)
        if tokens.has_credentials(slug)
    }
