"""Fresh customer-transit snapshots, loaded concurrently per marketplace account."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from app.core.stores import STORES
from app.exports.stock_sheet import StockSheetExportError
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens
from app.yandex import api as yandex_api
from app.yandex import tokens as yandex_tokens
from app.yandex.accounts import resolve_business_id


@dataclass
class TransitSnapshot:
    to_customer: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    from_customer: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _quantity(value) -> int:
    # Missing or malformed counters must not masquerade as a confirmed zero.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("API не вернул целое количество товара")
    if isinstance(value, str) and not value.isdecimal():
        raise ValueError("API вернул некорректное количество товара")
    number = int(value)
    if number < 0:
        raise ValueError("API вернул отрицательное количество товара")
    return number


def _article(value) -> str:
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise ValueError("API не вернул артикул товара")
    return str(value).strip()


def _rows(value) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("API вернул неполный список товаров FBO")
    return value


def _wb(store: str) -> TransitSnapshot:
    result = TransitSnapshot()
    rows = wb_api.get_warehouse_remains(wb_tokens.get_token(store))
    seen = set()
    for row in rows:
        article = _article(row.get("nmId"))
        if article in seen:
            raise ValueError("WB повторил артикул в сгруппированном отчёте")
        seen.add(article)
        counters = {}
        for warehouse in _rows(row.get("warehouses")):
            name = str(warehouse.get("warehouseName") or "").strip().casefold()
            if name in {"в пути до получателей", "в пути возвраты на склад wb"}:
                if name in counters:
                    raise ValueError("WB повторил счётчик товаров в пути")
                counters[name] = _quantity(warehouse.get("quantity"))
        # Zero counters can be absent from the sparse warehouses array.
        result.to_customer[article] = counters.get("в пути до получателей", 0)
        result.from_customer[article] = counters.get("в пути возвраты на склад wb", 0)
    return result


def _ozon(store: str) -> TransitSnapshot:
    client_id, api_key = ozon_tokens.get_credentials(store)
    sku_articles = {}
    for product in ozon_api.get_product_stocks(client_id, api_key):
        article = _article(product.get("offer_id"))
        for stock in _rows(product.get("stocks")):
            if str(stock.get("type")).lower() != "fbo":
                continue
            sku = _quantity(stock.get("sku"))
            if not sku or (sku in sku_articles and sku_articles[sku] != article):
                raise ValueError("Ozon вернул неоднозначный SKU товара")
            sku_articles[sku] = article
    result = TransitSnapshot()
    seen = set()
    for row in _rows(ozon_api.get_stock_analytics(client_id, api_key, sorted(sku_articles))):
        sku = _quantity(row.get("sku"))
        article = _article(row.get("offer_id"))
        if sku_articles.get(sku) != article:
            raise ValueError("Ozon вернул неизвестный SKU или другой артикул в аналитике FBO")
        identity = (sku, _quantity(row.get("warehouse_id")))
        if identity in seen:
            raise ValueError("Ozon повторил товар на складе в аналитике FBO")
        seen.add(identity)
        if "FBS_RETURN" in (row.get("item_tags") or []):
            continue
        result.to_customer[article] += _quantity(row.get("outbound_pending_delivery"))
        result.from_customer[article] += _quantity(row.get("return_from_customer_stock_count"))
    return result


def _yandex(store: str) -> TransitSnapshot:
    key = yandex_tokens.get_api_key(store)
    campaigns = yandex_api.get_campaigns(key)
    business_id = resolve_business_id(store, key, campaigns=campaigns)
    own = [yandex_api.normalize_campaign(row) for row in campaigns]
    own = [row for row in own if row["business_id"] == business_id]
    if not own:
        raise ValueError("Яндекс не подтвердил доступ к магазинам кабинета")
    campaign_ids = sorted({int(row["campaign_id"]) for row in own if row["scheme"] == "fby"})
    result = TransitSnapshot()
    if not campaign_ids:
        return result
    for order in yandex_api.get_fby_delivery_orders(key, business_id, campaign_ids):
        if order.get("programType") != "FBY" or order.get("campaignId") not in campaign_ids:
            raise ValueError("Яндекс вернул заказ из другого магазина или схемы")
        if order.get("fake") or order.get("status") not in {"DELIVERY", "PICKUP"}:
            continue
        for item in _rows(order.get("items")):
            article = _article(item.get("offerId"))
            count = _quantity(item.get("count"))
            if item.get("itemStatuses"):
                statuses = _rows(item["itemStatuses"])
                if any(
                    row.get("status")
                    not in {
                        "CREATED",
                        "SHIPPED",
                        "CANCELLED",
                        "DELIVERED_TO_BUYER",
                        "LOST",
                        "REJECTED",
                        "RETURNED",
                    }
                    for row in statuses
                ):
                    raise ValueError("Яндекс вернул неизвестный статус единицы заказа")
                count = sum(_quantity(row.get("count")) for row in statuses if row.get("status") == "SHIPPED")
                if count > _quantity(item["count"]):
                    raise ValueError("Яндекс вернул противоречивое количество единиц заказа")
            result.to_customer[article] += count
    for campaign_id in campaign_ids:
        for row in yandex_api.get_fby_customer_returns(key, campaign_id):
            if row.get("fastReturn") or row.get("shipmentStatus") not in {"RECEIVED", "IN_TRANSIT"}:
                continue
            for item in _rows(row.get("items")):
                article = _article(item.get("shopSku"))
                count = _quantity(item.get("count"))
                if item.get("instances"):
                    instances = _rows(item["instances"])
                    if len(instances) != count:
                        raise ValueError("Яндекс вернул неполные логистические позиции возврата")
                    if any(
                        unit.get("status")
                        not in {
                            "CREATED",
                            "RECEIVED",
                            "IN_TRANSIT",
                            "READY_FOR_PICKUP",
                            "PICKED",
                            "RECEIVED_ON_FULFILLMENT",
                            "CANCELLED",
                            "LOST",
                            "UTILIZED",
                            "PREPARED_FOR_UTILIZATION",
                            "EXPROPRIATED",
                            "NOT_IN_DEMAND",
                        }
                        for unit in instances
                    ):
                        raise ValueError("Яндекс вернул неизвестный статус позиции возврата")
                    count = sum(unit.get("status") in {"RECEIVED", "IN_TRANSIT"} for unit in instances)
                result.from_customer[article] += count
    return result


def load(marketplace: str) -> dict[str, TransitSnapshot]:
    loader = {"WB": _wb, "OZON": _ozon, "YANDEX MARKET": _yandex}[marketplace]
    snapshots = {}
    # Rate limits remain scoped to the token/client in each marketplace's API client.
    with ThreadPoolExecutor(max_workers=len(STORES), thread_name_prefix="fbo-export") as executor:
        futures = {executor.submit(loader, store): store for store in STORES}
        for future in as_completed(futures):
            store = futures[future]
            try:
                snapshots[store] = future.result()
            except Exception as error:
                raise StockSheetExportError(
                    f"{STORES[store].name} / {marketplace} / FBO в пути: {error}"
                ) from error
    return {store: snapshots[store] for store in STORES}
