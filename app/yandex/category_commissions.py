"""Read weekly product categories and placement fees; preserve failures explicitly."""

import math
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.repositories import yandex_economics as repository
from app.yandex import api, tokens
from app.yandex.accounts import resolve_business_id
from app.yandex.categories import category_paths, week_start

JOB = "yandex_category_commissions_sync"
SOURCE = "category_commission:"
SCHEMES = ("FBY", "FBS")
DIMENSIONS = ("length", "width", "height", "weight")


def positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def offer_for(row, category):
    if not category.get("leaf"):
        raise ValueError("Категория не является конечной в дереве Маркета")
    offer = row.get("offer") or {}
    price = offer.get("basicPrice") or {}
    if price.get("currencyId", "RUR") not in ("RUR", "RUB"):
        raise ValueError("Цена товара указана не в рублях")
    dimensions = offer.get("weightDimensions") or {}
    values = {
        "price": positive(price.get("value")),
        **{key: positive(dimensions.get(key)) for key in DIMENSIONS},
    }
    missing = [key for key, value in values.items() if value is None]
    if missing:
        labels = {"price": "цена", "length": "длина", "width": "ширина", "height": "высота", "weight": "вес"}
        raise ValueError("В API нет данных: " + ", ".join(labels[key] for key in missing))
    return {"categoryId": category["category_id"], **values, "quantity": 1}


def parse_fee(offer, response):
    echoed = response.get("offer") or {}
    if any(Decimal(str(echoed.get(key, -1))) != Decimal(str(value)) for key, value in offer.items()):
        raise ValueError("Ответ тарифа относится к другим параметрам товара")
    fees = [row for row in response.get("tariffs") or [] if row.get("type") == "FEE"]
    if len(fees) != 1:
        raise ValueError("Маркет не вернул однозначную комиссию размещения")
    fee = fees[0]
    amount = float(fee["amount"])
    if fee.get("currency", "RUR") != "RUR" or not math.isfinite(amount) or amount < 0:
        raise ValueError("Некорректная сумма комиссии Маркета")
    parameters = {row["name"]: row["value"] for row in fee.get("parameters") or []}
    percent = amount / offer["price"] * 100
    proportional = False
    if parameters.get("valueType") == "relative":
        nominal = float(parameters["value"])
        if math.isfinite(nominal) and abs(offer["price"] * nominal / 100 - amount) <= 0.011:
            percent, proportional = nominal, True
    if not math.isfinite(percent) or not 0 <= percent <= 100:
        raise ValueError("Некорректный процент комиссии Маркета")
    return {
        "commission_percent": round(percent, 8),
        "amount": amount,
        "offer": offer,
        "parameters": parameters,
        "proportional": proportional,
    }


def commission_value(record, values, now=None):
    """Use a quote only for its category, campaign, dimensions and supported price range."""
    try:
        timestamp = datetime.fromisoformat(record["checked_at"])
        age = (now or datetime.now(UTC)) - timestamp
        if record.get("status") != "ok" or not timedelta(0) <= age < timedelta(days=7):
            return None
        if record["category_id"] != values.get("category_id"):
            return None
        quotes = record["quotes"]
        campaign = values.get("campaign_id")
        if campaign is not None:
            quotes = [quote for quote in quotes if quote["campaign_id"] == campaign]
        rates = []
        for quote in quotes:
            offer, parameters = quote["offer"], quote["parameters"]
            if any(positive(values.get(key)) != positive(offer[key]) for key in DIMENSIONS):
                return None
            price = positive(values.get("seller_price"))
            if price is None:
                return None
            if price != offer["price"]:
                if not quote["proportional"] or parameters.get("priceDependence") != "NOT_DEPENDED":
                    return None
                if price < float(parameters.get("priceFrom", 0)):
                    return None
                if parameters.get("priceTo") is not None and price >= float(parameters["priceTo"]):
                    return None
            rates.append(quote["commission_percent"])
        return rates[0] if rates and len(set(rates)) == 1 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def store_campaigns(store, key):
    rows = api.get_campaigns(key)
    business = resolve_business_id(store, key, campaigns=rows)
    configured = {row["id"] for row in tokens.get_campaigns(store)}
    own = [row for row in rows if api.normalize_campaign(row)["business_id"] == business]
    if configured - {row["id"] for row in own}:
        raise ValueError("В настройках указаны кампании другого кабинета")
    return [
        {"id": row["id"], "name": row.get("domain") or str(row["id"]), "scheme": row["placementType"]}
        for row in own
        if row.get("placementType") in SCHEMES
        and row.get("apiAvailability", "AVAILABLE") == "AVAILABLE"
        and (not configured or row["id"] in configured)
    ], business


def refresh_store(store, tree, saved, *, request_interval=0.65):
    key = tokens.get_api_key(store)
    campaigns, business = store_campaigns(store, key)
    rows = api.get_catalog(key, business)
    stamp = datetime.now(UTC).isoformat()
    categories, offers, missing = {}, {}, {}
    for row in rows:
        offer, mapping = row.get("offer") or {}, row.get("mapping") or {}
        article = str(offer.get("offerId") or "").strip()
        if not article or article in categories:
            raise ValueError("Маркет вернул пустой или повторяющийся артикул каталога")
        category_id = mapping.get("marketCategoryId") or offer.get("marketCategoryId")
        category = tree.get(category_id)
        categories[article] = {**(category or {"category_id": category_id, "path": []}), "checked_at": stamp}
        try:
            if category is None:
                raise ValueError("Категория товара отсутствует в дереве Маркета")
            offers[article] = offer_for(row, category)
        except ValueError as error:
            missing[article] = str(error)
    quotes, failures = defaultdict(list), {}
    batch_items = list(offers.items())
    for campaign in campaigns:
        for start in range(0, len(batch_items), 200):
            batch = batch_items[start : start + 200]
            try:
                response = api.request(
                    "/v2/tariffs/calculate",
                    key,
                    payload={
                        "parameters": {"campaignId": campaign["id"]},
                        "offers": [offer for _, offer in batch],
                    },
                )
                results = response.get("offers") or []
                if len(results) != len(batch):
                    raise ValueError("Маркет вернул неполный пакет комиссий")
                for (article, offer), result in zip(batch, results, strict=True):
                    try:
                        fee = parse_fee(offer, result)
                        quotes[article, campaign["scheme"]].append(
                            {**fee, "campaign_id": campaign["id"], "campaign_name": campaign["name"]}
                        )
                    except (ValueError, KeyError, TypeError) as error:
                        failures[article, campaign["scheme"]] = str(error)
            except (api.YandexApiError, ValueError) as error:
                for article, _ in batch:
                    failures[article, campaign["scheme"]] = str(error)
            if request_interval:
                time.sleep(request_interval)
    entries, updated, unavailable = [], 0, 0
    for article, category in categories.items():
        entries.append((article, "category", category))
        for scheme in SCHEMES:
            source = SOURCE + scheme
            previous = saved.get((article, source), {}).get("values", {})
            applicable = [campaign for campaign in campaigns if campaign["scheme"] == scheme]
            error = missing.get(article) or failures.get((article, scheme))
            if not applicable:
                payload = {
                    "status": "not_applicable",
                    "category_id": category["category_id"],
                    "checked_at": stamp,
                    "quotes": [],
                }
            elif error:
                payload = {
                    **previous,
                    "status": "missing" if article in missing else "error",
                    "error": error,
                    "attempted_at": stamp,
                }
                unavailable += 1
            else:
                product_quotes = quotes[article, scheme]
                if len(product_quotes) != len(applicable):
                    raise ValueError("Не все кампании проверены для комиссии товара")
                rates = {quote["commission_percent"] for quote in product_quotes}
                payload = {
                    "status": "ok",
                    "category_id": category["category_id"],
                    "checked_at": stamp,
                    "quotes": product_quotes,
                    "commission_percent": next(iter(rates)) if len(rates) == 1 else None,
                    "needs_campaign": len(rates) > 1,
                }
                updated += 1
            entries.append((article, source, payload))
    report = {
        "ok": not failures,
        "products": len(categories),
        "updated": updated,
        "missing_products": len(missing),
        "unavailable_quotes": unavailable,
        "campaigns": len(campaigns),
    }
    if failures:
        report["error"] = f"Не удалось обновить {len(failures)} комиссий; прежние значения сохранены"
    entries.append(("", JOB, {**report, "checked_at": stamp}))
    repository.save_sources(store, entries, updated_at=stamp)
    return report


def sync_all(store_slugs=None, *, force=False):
    stores = tokens.stores_with_credentials() if store_slugs is None else store_slugs
    report, pending = {}, {}
    for store in stores:
        if not tokens.has_credentials(store):
            continue
        saved = repository.sources(store)
        meta = saved.get(("", JOB), {}).get("values", {})
        try:
            checked = datetime.fromisoformat(meta["checked_at"])
            current = meta.get("ok") and checked >= week_start()
        except (KeyError, TypeError, ValueError):
            current = False
        if not force and current:
            report[store] = {"ok": True, "skipped": "Категории и комиссии уже проверены на этой неделе"}
        else:
            pending[store] = saved
    if not pending:
        return report
    tree = None
    for store, saved in pending.items():
        try:
            if tree is None:
                tree = category_paths(
                    api.request("/v2/categories/tree", tokens.get_api_key(store), payload={"language": "RU"})
                )
            report[store] = refresh_store(store, tree, saved)
        except Exception as error:
            report[store] = {"ok": False, "error": str(error)[:700]}
    return report
