"""Resolve selected catalog offers and read ordinary storefront prices, excluding Pay."""

import json
import math
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from app import db
from app.repositories import yandex_assortment
from app.repositories import yandex_storefront as repository
from app.yandex import api, tokens
from app.yandex.unit_economics_sync import resolve_business_id


def number(value) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (ValueError, TypeError):
        return None


def card_id(url: str) -> str | None:
    parsed = urlsplit(url)
    match = re.fullmatch(r"/card/[^/]+/(\d+)/?", parsed.path)
    if parsed.scheme == "https" and parsed.hostname == "market.yandex.ru" and match:
        return match[1]
    return None


def showcase_url(offer: dict, business_id: int) -> str | None:
    for entry in offer.get("showcaseUrls") or []:
        if isinstance(entry, dict):
            if entry.get("showcaseType") != "B2C":
                continue
            url = entry.get("showcaseUrl") or ""
        else:
            continue
        if card_id(url):
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)

            query["businessId"] = [str(business_id)]
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), ""))
    return None


def load_mappings(key: str, business_id: int, articles: list[str]) -> dict[str, dict]:
    result = {}
    for offset in range(0, len(articles), 200):
        wanted = articles[offset:offset + 200]
        page_token, seen = "", set()
        while True:
            data = api.request(f"/v2/businesses/{business_id}/offer-mappings", key,
                               payload={"offerIds": wanted}, params={"limit": 200, "page_token": page_token})
            for row in data.get("offerMappings") or []:
                article = str((row.get("offer") or {}).get("offerId") or "")
                if article in wanted:
                    result[article] = row
            page_token = (data.get("paging") or {}).get("nextPageToken") or ""
            if not page_token:
                break
            if page_token in seen:
                raise ValueError("Повтор страницы offer-mappings")
            seen.add(page_token)
    return result


def prepare(selection: set[tuple[str, str]] | None = None) -> tuple[list[dict], list[dict]]:
    selected = selection if selection is not None else yandex_assortment.load_active_products()
    targets, skipped = [], []
    for store in sorted({slug for slug, _ in selected}):
        articles = sorted(article for slug, article in selected if slug == store)
        catalog = {str(row["article"]): row for row in db.get_catalog_items(store, "YANDEX MARKET")}
        mappings, business_id, error = {}, None, None
        if not tokens.has_credentials(store):
            error = "Не настроен ключ Яндекс Маркета для магазина"
        else:
            try:
                key = tokens.get_api_key(store)
                business_id = resolve_business_id(store, key)
                mappings = load_mappings(key, business_id, articles)
                for article in articles:
                    if article not in catalog and article in mappings:
                        product = api.normalize_catalog_item(mappings[article])
                        if product["article"] == article:
                            repository.add_missing_catalog_item(store, product)
                catalog = {str(row["article"]): row for row in db.get_catalog_items(store, "YANDEX MARKET")}
            except Exception as exc:
                error = "Не удалось получить ссылки Маркета: " + type(exc).__name__
        for article in articles:
            target = {"store_slug": store, "article": article}
            row = mappings.get(article) or {}
            mapping = row.get("mapping") or {}
            url = showcase_url(row, business_id) if business_id else None
            reason = error or ("Товар отсутствует в каталоге БД" if article not in catalog else None)
            if not reason and not url:
                reason = "Маркет не вернул ссылку B2C на карточку"
            if reason:
                result = {**target, "status": "not_configured" if error else "not_mapped", "message": reason}
                repository.record(target, result)
                skipped.append(result)
                continue
            target.update({
                "name": catalog[article].get("name"), "business_id": business_id,
                "market_sku": str(mapping.get("marketSku") or catalog[article].get("mp_sku") or ""),
                "card_id": card_id(url), "url": url,
            })
            repository.save_target(target)
            targets.append(target)

        store_targets = {t["article"]: t for t in targets if t["store_slug"] == store}
        if store_targets:
            try:
                values = {}
                articles_to_price = list(store_targets)
                for offset in range(0, len(articles_to_price), 200):
                    prices = api.request(f"/v2/businesses/{business_id}/offer-prices", key,
                                         payload={"offerIds": articles_to_price[offset:offset + 200]}, params={"limit": 200})
                    values.update({str(p.get("offerId")): number(p.get("price")) for p in prices.get("offers") or []})
                for article, target in store_targets.items():
                    repository.seller_price(target, values.get(article))
            except Exception:
                pass
    repository.refresh_assortment()
    return targets, skipped


class MarketHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tag = None
        self.buffer = []
        self.documents = []
        self.text = []
        self.ignored_tag = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if (tag == "noframes" and attrs.get("data-apiary") == "patch") or (
            tag == "script" and attrs.get("type") in ("application/json", "application/ld+json")
        ):
            self.tag, self.buffer = tag, []
        elif tag in ("script", "style"):
            self.ignored_tag = tag

    def handle_data(self, data):
        if self.tag:
            self.buffer.append(data)
        elif not self.ignored_tag:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == self.ignored_tag:
            self.ignored_tag = None
        if tag == self.tag:
            try:
                self.documents.append(json.loads("".join(self.buffer)))
            except (ValueError, TypeError):
                pass
            self.tag, self.buffer = None, []


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def parse_prices(html: str, url: str, target: dict) -> dict:
    if "/showcaptcha" in urlsplit(url).path:
        return {"status": "captcha", "buyer_price": None}
    if card_id(url) != target["card_id"]:
        return {"status": "wrong_page", "buyer_price": None}
    parsed = MarketHTML()
    parsed.feed(html)
    candidates = {}
    for document in parsed.documents:
        for obj in objects(document):
            collection = obj.get("offerAnalytics")
            if isinstance(collection, dict):
                candidates.update(collection)
    matches = []
    for key, offer in candidates.items():
        if not isinstance(offer, dict):
            continue
        if str(offer.get("businessId")) != str(target["business_id"]):
            continue
        osku = str(offer.get("oskuId") or "")
        sku = str(offer.get("marketSku") or offer.get("skuId") or "")
        if not osku and not sku:
            continue
        if (osku and osku != target["card_id"]) or (not osku and sku != target["market_sku"]):
            continue
        prices = offer.get("prices") or {}
        if not isinstance(prices, dict) or not isinstance(prices.get("price"), dict):
            continue
        value = number(prices["price"])
        currency = prices["price"].get("currency", "RUR")
        if value is not None and currency in ("RUR", "RUB"):
            matches.append({"status": "ok", "buyer_price": value,
                            "currency": currency, "offer_key": str(key)})
    if matches:
        if len({item["buyer_price"] for item in matches}) != 1:
            return {"status": "ambiguous", "buyer_price": None, "message": "Несколько цен выбранного продавца"}
        return matches[0]
    text = " ".join(parsed.text).lower()
    sold_out = any(t in text for t in (
        "нет в продаже", "продавец удалил этот товар", "товар закончился", "товар распродан", "разобрали в магазине",
    ))
    return {"status": "out_of_stock" if sold_out else "price_missing", "buyer_price": None,
            "message": "Товар недоступен на витрине выбранного продавца" if sold_out else "Цена выбранного продавца на странице не найдена"}
