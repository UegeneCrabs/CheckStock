import json
import math
from collections import defaultdict

from app.dto.yandex_economics import EconomicsValues
from app.repositories import yandex_economics as repository
from app.repositories import yandex_storefront
from app.yandex import api, tokens
from app.yandex.economics import context, effective, tariff_signature
from app.yandex.unit_economics_sync import resolve_business_id

DELIVERY_TYPES = {
    "DELIVERY_TO_CUSTOMER",
    "CROSSREGIONAL_DELIVERY",
    "EXPRESS_DELIVERY",
    "SORTING",
    "MIDDLE_MILE",
}


def catalog_values(row):
    offer, mapping = row.get("offer") or {}, row.get("mapping") or {}
    dimensions = offer.get("weightDimensions") or {}
    return EconomicsValues.model_validate(
        {
            "category_id": mapping.get("marketCategoryId") or offer.get("marketCategoryId"),
            "category_name": mapping.get("marketCategoryName") or offer.get("category"),
            **{key: dimensions.get(key) for key in ("length", "width", "height", "weight")},
        }
    ).model_dump(exclude_none=True)


def refresh_catalog(store):
    key = tokens.get_api_key(store)
    business = resolve_business_id(store, key)
    rows = api.get_catalog(key, business)
    parsed = [(str((row.get("offer") or {}).get("offerId") or ""), catalog_values(row)) for row in rows]
    for article, values in parsed:
        if article:
            repository.save_source(store, article, "catalog", values)
    return len(parsed)


def refresh_seller_prices(store):
    from app.repositories.yandex_assortment import active_articles
    from app.yandex.storefront_prices import number

    key = tokens.get_api_key(store)
    business = resolve_business_id(store, key)
    articles = sorted(active_articles(store))
    known = yandex_storefront.get_prices(store)
    count = 0
    for start in range(0, len(articles), 200):
        data = api.request(
            f"/v2/businesses/{business}/offer-prices",
            key,
            payload={"offerIds": articles[start : start + 200]},
            params={"limit": 200},
        )
        for row in data.get("offers") or []:
            article = str(row.get("offerId") or "")
            price = number(row.get("price"))
            if article not in articles or price is None:
                continue
            target = {"store_slug": store, "article": article}
            if article not in known:
                yandex_storefront.save_target(target)
            yandex_storefront.seller_price(target, price)
            count += 1
    return count


def quote_request(store, values, scheme):
    required = ("seller_price", "category_id", "length", "width", "height", "weight")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise ValueError("Для тарифа нужны цена, категория, размеры и вес: " + ", ".join(missing))
    campaigns = [
        row
        for row in tokens.get_campaigns(store)
        if row["scheme_key"] == ("fbo" if scheme == "FBY" else "fbs")
    ]
    campaign_id = values.get("campaign_id")
    if campaign_id is not None and campaign_id not in {row["id"] for row in campaigns}:
        raise ValueError("Выбранная кампания не принадлежит этому кабинету и схеме.")
    if campaign_id is None and len(campaigns) == 1:
        campaign_id = campaigns[0]["id"]
    elif campaign_id is None and len(campaigns) > 1:
        raise ValueError("Для этой схемы несколько магазинов. Укажите кампанию для расчёта тарифа.")
    parameters = {
        "frequency": values.get("frequency", "WEEKLY"),
        "paymentDelayWeeks": values.get("payment_delay_weeks", 0),
        "currency": "RUR",
    }
    if parameters["frequency"] != "WEEKLY" and parameters["paymentDelayWeeks"]:
        raise ValueError("Отсрочка выплат доступна только при еженедельном графике.")
    parameters.update({"campaignId": campaign_id} if campaign_id else {"sellingProgram": scheme})
    offer = {
        "categoryId": values["category_id"],
        "price": values["seller_price"],
        "quantity": 1,
        **{key: values[key] for key in ("length", "width", "height", "weight")},
    }
    return parameters, offer


def parse_quote(values, scheme, parameters, row):
    if not isinstance(row.get("tariffs"), list):
        raise ValueError("Маркет вернул неполный расчёт тарифа.")
    tariffs = row["tariffs"]
    amounts = {}
    for row in tariffs:
        value = float(row["amount"])
        if not math.isfinite(value) or value < 0 or row.get("currency", "RUR") != "RUR":
            raise ValueError("Некорректная сумма или валюта тарифа.")
        amounts[row["type"]] = amounts.get(row["type"], 0) + value
    unknown = set(amounts) - DELIVERY_TYPES - {"FEE", "AGENCY_COMMISSION", "PAYMENT_TRANSFER", "ITEM_BOOKING"}
    if unknown or "FEE" not in amounts:
        raise ValueError("Неизвестный или неполный набор услуг Маркета.")
    price = values["seller_price"]
    components = {
        "commission_percent": amounts["FEE"] / price * 100,
        "payment_acceptance": amounts.get("AGENCY_COMMISSION", 0),
        "payment_transfer_percent": amounts.get("PAYMENT_TRANSFER", 0) / price * 100,
        "delivery_cost": sum(amounts.get(kind, 0) for kind in DELIVERY_TYPES),
        "tariff_extra": amounts.get("ITEM_BOOKING", 0),
    }
    EconomicsValues.model_validate({key: value for key, value in components.items() if key != "tariff_extra"})
    return {
        "components": components,
        "services": tariffs,
        "signature": tariff_signature(values, scheme),
        "campaign_id": parameters.get("campaignId"),
        "parameters": parameters,
        "approximate": True,
    }


def quote(store, article, scheme, *, scenario=None, persist=True):
    values = effective(store, article, scheme, scenario=scenario)["values"]
    parameters, offer = quote_request(store, values, scheme)
    data = api.request(
        "/v2/tariffs/calculate",
        tokens.get_api_key(store),
        payload={"parameters": parameters, "offers": [offer]},
    )
    rows = data.get("offers") or []
    if len(rows) != 1:
        raise ValueError("Маркет вернул неполный расчёт тарифа.")
    result = parse_quote(values, scheme, parameters, rows[0])
    if persist:
        repository.save_source(store, article, "tariff:" + scheme, result)
    return result


def refresh_store(store):
    """Batch quotes (API preserves request order), updating only source values."""
    from app.repositories.yandex_assortment import active_articles

    refresh_catalog(store)
    refresh_seller_prices(store)
    cache, groups, errors = context(store), defaultdict(list), {}
    for article in sorted(active_articles(store)):
        for scheme in ("FBY", "FBS"):
            values = effective(store, article, scheme, state_cache=cache)["values"]
            try:
                parameters, offer = quote_request(store, values, scheme)
            except ValueError as error:
                errors[article + ":" + scheme] = str(error)
                continue
            groups[json.dumps(parameters, sort_keys=True)].append((article, scheme, values, offer))
    count = 0
    for parameters_json, items in groups.items():
        parameters = json.loads(parameters_json)
        for start in range(0, len(items), 200):
            batch = items[start : start + 200]
            response = api.request(
                "/v2/tariffs/calculate",
                tokens.get_api_key(store),
                payload={"parameters": parameters, "offers": [item[3] for item in batch]},
            )
            rows = response.get("offers") or []
            if len(rows) != len(batch):
                raise ValueError("Маркет вернул неполный пакет тарифов.")
            for (article, scheme, values, _offer), row in zip(batch, rows, strict=True):
                try:
                    result = parse_quote(values, scheme, parameters, row)
                    repository.save_source(store, article, "tariff:" + scheme, result)
                    count += 1
                except ValueError as error:
                    errors[article + ":" + scheme] = str(error)
    return {"updated": count, "missing": errors}
