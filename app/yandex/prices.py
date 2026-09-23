"""Change the business default seller price; never write storefront estimates."""

import logging
import time
from decimal import ROUND_HALF_UP, Decimal

from app.repositories import yandex_storefront
from app.yandex import api, economics, tokens
from app.yandex.accounts import resolve_business_id

logger = logging.getLogger(__name__)
POLL_ATTEMPTS = 60
POLL_SECONDS = 5
PRICE_FIELDS = ("value", "currencyId", "discountBase", "minimumForBestseller")


def read_price(key, business, article):
    result = api.request(
        f"/v2/businesses/{business}/offer-prices",
        key,
        payload={"offerIds": [article]},
        params={"limit": 1},
    )
    for offer in result.get("offers") or []:
        if str(offer.get("offerId")) == article:
            price = offer.get("price") or {}
            if price.get("currencyId") != "RUR":
                raise ValueError("Отправка поддерживается только для цен в рублях")
            if not yandex_storefront.positive(price.get("value")):
                break
            return {name: price[name] for name in (*PRICE_FIELDS, "updatedAt") if price.get(name) is not None}
    raise ValueError("Маркет не вернул цену этого SKU. Обновите каталог и проверьте артикул")


def preview(store, article, value):
    key = tokens.get_api_key(store)
    business = resolve_business_id(store, key)
    previous = read_price(key, business, article)
    value = float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if value == previous["value"]:
        raise ValueError("Эта цена уже установлена в Яндекс Маркете")
    price = {name: previous[name] for name in PRICE_FIELDS if name in previous}
    price["value"] = value
    base = price.get("discountBase")
    if base and not Decimal(str(base)) * Decimal("0.01") <= Decimal(str(value)) <= Decimal(
        str(base)
    ) * Decimal("0.95"):
        raise ValueError(
            "Новая цена несовместима с зачёркнутой ценой Маркета. "
            f"При зачёркнутой цене {base:g} ₽ допустимо от {base * 0.01:g} до {base * 0.95:g} ₽. "
            "Сначала измените зачёркнутую цену в кабинете Маркета"
        )
    return {
        "store_slug": store,
        "article": article,
        "business_id": business,
        "previous_price": previous,
        "price": price,
    }


def in_quarantine(key, business, article):
    result = api.request(
        f"/v2/businesses/{business}/price-quarantine",
        key,
        payload={"offerIds": [article]},
        params={"limit": 1},
    )
    if not isinstance(result.get("offers"), list):
        raise ValueError("Не удалось проверить карантин цены Яндекс Маркета")
    return any(str(offer.get("offerId")) == article for offer in result["offers"])


def save_confirmed_price(plan):
    store, article = plan["store_slug"], plan["article"]
    target = {"store_slug": store, "article": article}
    # Do not replace a saved storefront identity or its observed buyer/Pay prices.
    if article not in yandex_storefront.get_prices(store):
        yandex_storefront.save_target(target)
    yandex_storefront.seller_price(target, plan["price"]["value"])
    try:
        economics.capture_today((store,), only_article=article)
    except Exception:
        logger.exception("yandex_price_history_refresh_failed store=%s article=%s", store, article)
        return "Цена обновлена. Историю экономики пока не удалось пересчитать"
    return None


def apply(plan, on_sent):
    store, article, business = plan["store_slug"], plan["article"], plan["business_id"]
    key = tokens.get_api_key(store)
    if resolve_business_id(store, key) != business:
        raise ValueError("Привязка кабинета изменилась. Рассчитайте и подтвердите цену заново")
    if read_price(key, business, article) != plan["previous_price"]:
        raise ValueError("Цена или её параметры уже изменились в Маркете. Подтвердите новую цену заново")
    # Keep both discountBase and minimumForBestseller: omitting them removes existing settings.
    try:
        result = api.request(
            f"/v2/businesses/{business}/offer-prices/updates",
            key,
            payload={"offers": [{"offerId": article, "price": plan["price"]}]},
        )
    except api.YandexApiError as error:
        if error.retryable or error.status is None:
            raise ValueError(
                "Не удалось подтвердить ответ Маркета на отправку. "
                "Перед повторной отправкой проверьте цену в кабинете Маркета"
            ) from error
        raise
    if result.get("status") != "OK":
        raise ValueError("Маркет не подтвердил приём цены. Проверьте её в кабинете перед повторной отправкой")
    on_sent()
    for _ in range(POLL_ATTEMPTS):
        time.sleep(POLL_SECONDS)
        try:
            current = read_price(key, business, article)
            quarantined = in_quarantine(key, business, article)
        except api.YandexApiError as error:
            if error.retryable:
                continue
            raise ValueError(
                "Цена отправлена, но проверить её применение не удалось. " + str(error)
            ) from error
        if quarantined:
            return {
                "status": "quarantined",
                "error": "Маркет поместил цену в карантин. Проверьте и подтвердите её в кабинете Маркета",
            }
        if all(current.get(name) == plan["price"].get(name) for name in PRICE_FIELDS):
            try:
                warning = save_confirmed_price(plan)
            except Exception as error:
                logger.exception("yandex_price_local_sync_failed store=%s article=%s", store, article)
                raise ValueError("Цена применена в Маркете, но обновить данные сайта не удалось") from error
            return {"status": "success", "seller_price": current["value"], "warning": warning}
    return {
        "status": "unconfirmed",
        "error": "Маркет принял запрос, но применение цены пока не подтверждено. "
        "Проверьте цену в кабинете Маркета перед повторной отправкой",
    }
