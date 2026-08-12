import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.partner.market.yandex.ru"
REQUEST_TIMEOUT = settings.yandex_request_timeout_seconds

MAX_ATTEMPTS = settings.yandex_request_attempts
RETRY_BACKOFF_SECONDS = settings.yandex_retry_backoff_seconds


PAGE_SIZE = 200


SCHEME_FBY = "fby"
SCHEME_FBS = "fbs"
SCHEME_DBS = "dbs"
SCHEME_EXPRESS = "express"


CAMPAIGN_SCHEMES = {
    "FBY": SCHEME_FBY,
    "FBS": SCHEME_FBS,
    "DBS": SCHEME_DBS,
    "EXPRESS": SCHEME_EXPRESS,
}


AVAILABLE_STOCK_TYPE = "AVAILABLE"

_FRIENDLY_BY_STATUS = {
    400: "неверный запрос к Яндекс Маркету (400)",
    401: "Маркет не принял ключ (401) — проверьте Api-Key",
    403: "нет доступа (403) — у ключа не хватает прав или он отозван",
    404: "метод Маркета не найден (404) — возможно, изменилась версия API",
    420: "превышено ограничение на обращения (420) — сработал лимит запросов",
    500: "внутренняя ошибка на стороне Маркета (500)",
    502: "Маркет недоступен (502)",
    503: "Маркет недоступен (503)",
    504: "Маркет не ответил вовремя (504)",
}


class YandexApiError(Exception):
    def __init__(self, status: int | None, detail: str = ""):
        self.status = status
        self.detail = detail
        super().__init__(self.friendly)

    @property
    def friendly(self) -> str:
        base = _FRIENDLY_BY_STATUS.get(self.status)
        if base:
            return f"{base} ({self.detail})" if self.detail else base
        if self.status:
            return f"Маркет вернул ошибку {self.status}: {self.detail or 'без описания'}"
        return self.detail or "неизвестная ошибка при обращении к Яндекс Маркету"


def _parse_error_body(raw: str) -> str:

    try:
        data = json.loads(raw)
    except ValueError:
        return raw[:200]

    if not isinstance(data, dict):
        return raw[:200]

    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        parts = []
        for item in errors[:3]:
            if isinstance(item, dict):
                code = str(item.get("code") or "").strip()
                message = str(item.get("message") or "").strip()
                parts.append(f"{code}: {message}" if code and message else (message or code))
        if parts:
            return "; ".join(p for p in parts if p)

    return str(data.get("message") or raw[:200])


def _request(
    path: str, api_key: str, payload: dict | None = None, params: dict | None = None, method: str = "POST"
) -> dict:

    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})

    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    last_error: YandexApiError | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Api-Key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                raw = response.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except ValueError as e:
                raise YandexApiError(None, f"Маркет вернул не-JSON ответ: {e}") from e

            if isinstance(data, dict) and data.get("status") == "ERROR":
                raise YandexApiError(None, _parse_error_body(raw))

            result = data.get("result") if isinstance(data, dict) else None
            return result if isinstance(result, dict) else (data if isinstance(data, dict) else {})

        except urllib.error.HTTPError as e:
            detail = _parse_error_body(e.read().decode("utf-8", errors="replace"))
            last_error = YandexApiError(e.code, detail)

            if e.code in (420, 429) or 500 <= e.code < 600:
                if attempt < MAX_ATTEMPTS:
                    pause = RETRY_BACKOFF_SECONDS * attempt
                    logger.warning("Яндекс %s: %s, повтор через %s с", path, last_error.friendly, pause)
                    time.sleep(pause)
                    continue
            raise last_error from e

        except TimeoutError as e:
            last_error = YandexApiError(None, "Маркет не ответил за отведённое время")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise last_error from e

        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise YandexApiError(None, "Маркет не ответил за отведённое время") from e
            raise YandexApiError(None, f"сеть: {reason}") from e

    raise last_error or YandexApiError(None, "не удалось выполнить запрос к Маркету")


def request(
    path: str, api_key: str, payload: dict | None = None, params: dict | None = None, method: str = "POST"
) -> dict:
    return _request(path, api_key, payload, params, method)


def get_campaigns(api_key: str) -> list[dict]:

    campaigns: list[dict] = []
    page = 1

    while True:
        data = _request("/v2/campaigns", api_key, params={"page": page, "pageSize": 50}, method="GET")
        chunk = data.get("campaigns") or []
        campaigns.extend(chunk)

        pager = data.get("pager") or {}
        total_pages = int(pager.get("pagesCount") or 1)
        if page >= total_pages or not chunk:
            return campaigns
        page += 1


def normalize_campaign(row: dict) -> dict:

    business = row.get("business") or {}
    placement = str(row.get("placementType") or "").upper()
    return {
        "campaign_id": row.get("id"),
        "business_id": business.get("id"),
        "business_name": business.get("name") or "",
        "domain": row.get("domain") or "",
        "placement": placement,
        "scheme": CAMPAIGN_SCHEMES.get(placement, placement.lower()),
    }


def get_business_orders(api_key: str, business_id: int, date_from: str, date_to: str) -> list[dict]:

    orders: list[dict] = []
    page_token = ""

    while True:
        data = _request(
            f"/v1/businesses/{business_id}/orders",
            api_key,
            payload={
                "dates": {
                    "creationDateFrom": date_from,
                    "creationDateTo": date_to,
                },
                "fake": False,
                "sourcePlatforms": ["MARKET"],
            },
            params={"limit": 50, "pageToken": page_token},
        )
        page = data.get("orders") or []
        if not isinstance(page, list):
            raise YandexApiError(None, f"неожиданный формат заказов Яндекса: {data!r}"[:300])
        orders.extend(page)

        paging = data.get("paging") or {}
        page_token = str(paging.get("nextPageToken") or data.get("nextPageToken") or "")
        if not page_token or not page:
            return orders

        if len(orders) > 500_000:
            logger.warning(
                "Яндекс: прервали обход заказов кабинета %s на %s строках", business_id, len(orders)
            )
            return orders


def get_fulfillment_warehouses(api_key: str) -> list[dict]:

    data = _request("/v2/warehouses", api_key, method="GET")
    return data.get("warehouses") or []


def get_catalog(api_key: str, business_id: int) -> list[dict]:

    items: list[dict] = []
    page_token = ""

    while True:
        data = _request(
            f"/v2/businesses/{business_id}/offer-mappings",
            api_key,
            payload={},
            params={"limit": PAGE_SIZE, "page_token": page_token},
        )
        chunk = data.get("offerMappings") or []
        items.extend(chunk)

        page_token = (data.get("paging") or {}).get("nextPageToken") or ""
        if not page_token or not chunk:
            return items

        if len(items) > 200_000:
            logger.warning("Яндекс: прервали обход каталога на %s позициях", len(items))
            return items


def normalize_catalog_item(row: dict) -> dict:

    offer = row.get("offer") or {}
    mapping = row.get("mapping") or {}

    barcodes = [str(b).strip() for b in (offer.get("barcodes") or []) if str(b or "").strip()]
    image_url = ""
    for picture in offer.get("pictures") or []:
        candidate = (
            picture
            if isinstance(picture, str)
            else (picture.get("url", "") if isinstance(picture, dict) else "")
        )
        candidate = str(candidate or "").strip()
        if candidate.startswith(("https://", "http://")):
            image_url = candidate
            break

    return {
        "article": str(offer.get("offerId") or "").strip(),
        "name": str(offer.get("name") or "").strip(),
        "barcode": barcodes[0] if barcodes else "",
        "image_url": image_url,
        "barcodes": barcodes,
        "market_sku": mapping.get("marketSku"),
        "archived": bool(offer.get("archived")),
        "updated_at": str(offer.get("updatedAt") or "").strip(),
    }


def get_stocks(api_key: str, campaign_id: int, archived: bool = False) -> list[dict]:

    rows: list[dict] = []
    page_token = ""

    while True:
        data = _request(
            f"/v2/campaigns/{campaign_id}/offers/stocks",
            api_key,
            payload={"archived": archived},
            params={"limit": PAGE_SIZE, "page_token": page_token},
        )

        warehouses = data.get("warehouses") or []
        for warehouse in warehouses:
            warehouse_id = warehouse.get("warehouseId")
            for offer in warehouse.get("offers") or []:
                rows.append(
                    {
                        "article": str(offer.get("offerId") or "").strip(),
                        "warehouse_id": warehouse_id,
                        "stocks": offer.get("stocks") or [],
                        "updated_at": offer.get("updatedAt") or "",
                    }
                )

        page_token = (data.get("paging") or {}).get("nextPageToken") or ""
        if not page_token or not warehouses:
            return rows

        if len(rows) > 500_000:
            logger.warning("Яндекс: прервали обход остатков на %s строках", len(rows))
            return rows


def available_quantity(stocks: list[dict]) -> int:

    for stock in stocks:
        if str(stock.get("type") or "").upper() == AVAILABLE_STOCK_TYPE:
            try:
                return int(stock.get("count") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def stock_by_type(stocks: list[dict]) -> dict[str, int]:

    result: dict[str, int] = {}
    for stock in stocks:
        key = str(stock.get("type") or "?").upper()
        try:
            result[key] = result.get(key, 0) + int(stock.get("count") or 0)
        except (TypeError, ValueError):
            continue
    return result
