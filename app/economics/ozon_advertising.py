"""Seven completed days of Ozon product advertising from Performance API."""

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from app import db
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.ozon import api as ozon_api
from app.ozon import performance, performance_reports, tokens
from app.repositories import ozon_advertising as repository

JOB_NAME = "ozon_unit_economics_advertising_sync"
SCOPE = "ozon_advertising"
logger = logging.getLogger(__name__)


def _period() -> tuple[date, date]:
    end = datetime.now(MOSCOW_TIMEZONE).date() - timedelta(days=1)
    return end - timedelta(days=6), end


def _bounds(start: date, end: date) -> tuple[str, str]:
    first = datetime.combine(start, time.min, tzinfo=MOSCOW_TIMEZONE)
    last = datetime.combine(end + timedelta(days=1), time.min, tzinfo=MOSCOW_TIMEZONE)
    return first.isoformat(), last.isoformat()


def _merge(target: dict[str, dict], rows: dict[str, dict]) -> None:
    for sku, row in rows.items():
        metric = target.setdefault(sku, {
            "spend": 0.0, "click_spend": 0.0, "impressions": 0, "clicks": 0,
            "article": "",
        })
        for key in ("spend", "click_spend", "impressions", "clicks"):
            metric[key] += row[key]
        if row.get("article"):
            metric["article"] = row["article"]


def _raw_product_metrics(token: str, start: date, end: date) -> dict[str, dict]:
    campaigns = performance.list_campaigns(token)
    cpc_ids = [
        str(item["id"])
        for item in campaigns
        if item.get("id") and str(item.get("advObjectType")) == "SKU"
        and str(item.get("paymentType")) == "CPC"
    ]
    result: dict[str, dict] = {}
    for offset in range(0, len(cpc_ids), 10):
        raw = performance.report(
            token, "POST", "/api/client/statistics",
            payload={
                "campaigns": cpc_ids[offset : offset + 10],
                "dateFrom": start.isoformat(),
                "dateTo": end.isoformat(),
                "groupBy": "NO_GROUP_BY",
            },
        )
        _merge(result, performance_reports.parse_product_report(raw, "cpc"))

    first, last = _bounds(start, end)
    selected = performance.report(
        token, "POST", "/api/client/statistic/products/generate",
        payload={"from": first, "to": last},
    )
    _merge(result, performance_reports.parse_product_report(selected, "cpo"))
    all_products = performance.report(
        token, "GET", "/api/client/statistics/all_sku_promo/orders/generate",
        params={"timeBounds.from": first, "timeBounds.to": last},
    )
    _merge(result, performance_reports.parse_product_report(all_products, "all_promo"))
    return result


def _sku_to_article(store_slug: str, skus: set[str]) -> dict[str, str]:
    catalog = db.get_catalog_items(store_slug, "OZON")
    mapping = {
        str(row["mp_sku"]): str(row["article"])
        for row in catalog if row.get("mp_sku")
    }
    if skus.difference(mapping):
        client_id, api_key = tokens.get_credentials(store_slug)
        for row in ozon_api.get_product_stocks(client_id, api_key):
            article = str(row.get("offer_id") or "").strip()
            if article:
                for stock in row.get("stocks") or []:
                    sku = str(stock.get("sku") or "")
                    if sku:
                        mapping[sku] = article
    return mapping


def sync_store(store_slug: str, start: date | None = None, end: date | None = None) -> dict:
    start, end = (start, end) if start and end else _period()
    attempted_at = datetime.now(UTC).isoformat()
    try:
        client_id, secret = tokens.get_performance_credentials(store_slug)
        token = performance.authorize(client_id, secret)
        raw_metrics = _raw_product_metrics(token, start, end)
        by_sku = _sku_to_article(store_slug, set(raw_metrics)) if raw_metrics else {}
        valid_articles = {str(item["article"]) for item in db.get_catalog_items(store_slug, "OZON")}
        by_article = defaultdict(lambda: {
            "spend": 0.0, "click_spend": 0.0, "impressions": 0, "clicks": 0,
        })
        unmatched = []
        for sku, metric in raw_metrics.items():
            article = by_sku.get(sku) or str(metric.get("article") or "")
            if article not in valid_articles:
                unmatched.append(sku)
                continue
            row = by_article[article]
            for key in ("spend", "click_spend", "impressions", "clicks"):
                row[key] += metric[key]
        if unmatched:
            raise performance.PerformanceApiError(
                f"Не удалось сопоставить с каталогом {len(unmatched)} рекламных SKU"
            )
        repository.save_snapshot(
            store_slug, start.isoformat(), end.isoformat(), dict(by_article), attempted_at
        )
        db.record_sync_health(store_slug, "OZON", SCOPE, True, None, attempted_at)
        return {"ok": True, "products": len(by_article), "period_from": start.isoformat(),
                "period_to": end.isoformat()}
    except Exception as error:
        message = f"{type(error).__name__}: {error}"[:1000]
        db.record_sync_health(store_slug, "OZON", SCOPE, False, message, attempted_at)
        logger.warning("Реклама Ozon %s не обновлена: %s", store_slug, message)
        return {"ok": False, "error": message}


def sync_all(store_slugs: tuple[str, ...] | None = None) -> dict[str, dict]:
    stores = tuple(STORES) if store_slugs is None else store_slugs
    return {
        slug: sync_store(slug) for slug in stores if tokens.has_credentials(slug)
    }


def load(
    store_slugs: tuple[str, ...], start: date, end: date
) -> tuple[dict[tuple[str, str], dict], dict[str, str]]:
    snapshots = repository.load_snapshots(store_slugs, start.isoformat(), end.isoformat())
    errors: dict[str, str] = {}
    products: dict[tuple[str, str], dict] = {}
    for slug in store_slugs:
        snapshot = snapshots.get(slug)
        if snapshot is None:
            errors[slug] = "Реклама Ozon: нет выгрузки Performance API за выбранный период"
            continue
        for article, values in snapshot["products"].items():
            products[(slug, article)] = values
    return products, errors
