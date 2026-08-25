import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import settings

logger = logging.getLogger(__name__)

FBS_BASE = "https://marketplace-api.wildberries.ru"
SUPPLIES_BASE = "https://supplies-api.wildberries.ru"
ANALYTICS_BASE = "https://seller-analytics-api.wildberries.ru"
CONTENT_BASE = "https://content-api.wildberries.ru"
STATISTICS_BASE = "https://statistics-api.wildberries.ru"
DISCOUNTS_PRICES_BASE = "https://discounts-prices-api.wildberries.ru"
STOREFRONT_CARDS_BASE = "https://card.wb.ru/cards/v4/detail"
STOREFRONT_DEFAULT_PAYMENT_URL = (
    "https://static-basket-01.wbbasket.ru/vol1/global-payment/default-payment.json"
)


CARDS_PAGE_LIMIT = 100


FBS_SKUS_PER_REQUEST = 1000
FBS_ORDERS_PER_REQUEST = 1000

REQUEST_TIMEOUT = settings.wb_request_timeout_seconds

FBO_STOCK_PAGE_LIMIT = 250_000
FBO_STOCK_PAGE_INTERVAL_SECONDS = 20.0


REQUEST_ATTEMPTS = settings.wb_request_attempts
RETRY_BACKOFF_SECONDS = settings.wb_retry_backoff_seconds

_FRIENDLY_BY_STATUS = {
    400: "WB не принял запрос — неверные параметры",
    401: "нет доступа — проверьте категории у токена в личном кабинете WB",
    403: "доступ запрещён — токен не подходит для этого метода",
    404: "метод недоступен на стороне WB (возможно, отключён/устарел)",
    409: "конфликт на стороне WB — запрос не обработан, попробуйте ещё раз",
    413: "запрос слишком большой для WB — нужно уменьшить число товаров за один раз",
    422: "WB не смог обработать параметры запроса",
    429: "WB ограничил частоту запросов — попробуйте синхронизацию чуть позже",
    498: "витрина WB временно отклонила запрос",
    500: "внутренняя ошибка WB, попробуйте позже",
    503: "сервис WB временно недоступен",
}


class WBApiError(Exception):
    def __init__(self, status: int | None, title: str = "", detail: str = ""):
        self.status = status
        self.title = title
        self.detail = detail
        super().__init__(self.friendly)

    @property
    def friendly(self) -> str:
        known = _FRIENDLY_BY_STATUS.get(self.status) if self.status else None
        if known:
            tail = f" ({self.detail})" if self.detail and self.detail not in known else ""
            return known + tail
        if self.status:
            tail = f" — {self.detail}" if self.detail else ""
            return f"ошибка {self.status}{tail}"
        return self.detail or self.title or "неизвестная ошибка соединения"


def _parse_error_body(raw: str) -> tuple[str, str]:
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return "", raw[:200]
    return payload.get("title", ""), payload.get("detail", "")


def _retry_after_seconds(http_error: urllib.error.HTTPError, attempt: int) -> float:

    headers = getattr(http_error, "headers", None)
    if headers:
        for key in ("X-Ratelimit-Retry", "X-Ratelimit-Reset", "Retry-After"):
            val = headers.get(key)
            if val:
                try:
                    return max(float(val), 0.5)
                except (TypeError, ValueError):
                    pass
    return RETRY_BACKOFF_SECONDS * attempt


def _request(method: str, url: str, token: str, params: dict | None = None, json_body=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    data = None
    headers = {"Authorization": token}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw_body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            title, detail = _parse_error_body(raw)
            if e.code == 429 and attempt < REQUEST_ATTEMPTS:
                wait = _retry_after_seconds(e, attempt)
                logger.warning(
                    "WB 429 (попытка %s/%s), ждём %.1fс: %s",
                    attempt,
                    REQUEST_ATTEMPTS,
                    wait,
                    url,
                )
                time.sleep(wait)
                continue
            raise WBApiError(e.code, title, detail) from e
        except TimeoutError as e:
            raise WBApiError(None, detail=f"WB не ответил за {REQUEST_TIMEOUT}с (таймаут)") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise WBApiError(None, detail=f"WB не ответил за {REQUEST_TIMEOUT}с (таймаут)") from e
            raise WBApiError(None, detail=f"сеть: {e.reason}") from e

        if not raw_body:
            return None
        try:
            return json.loads(raw_body)
        except (ValueError, TypeError) as e:
            raise WBApiError(
                None, detail=f"WB вернул ответ, который не удалось разобрать как JSON: {raw_body[:200]!r}"
            ) from e


def request(method: str, url: str, token: str, params: dict | None = None, json_body=None):
    return _request(method, url, token, params, json_body)


def _public_request(url: str, params: dict | None = None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {
        "Accept": "application/json",
        "Referer": "https://www.wildberries.ru/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
        ),
    }
    retryable_statuses = {403, 429, 498}
    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw_body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            title, detail = _parse_error_body(raw)
            retryable = error.code in retryable_statuses or error.code >= 500
            if retryable and attempt < REQUEST_ATTEMPTS:
                wait = _retry_after_seconds(error, attempt)
                logger.warning(
                    "Витрина WB %s (попытка %s/%s), ждём %.1fс: %s",
                    error.code,
                    attempt,
                    REQUEST_ATTEMPTS,
                    wait,
                    url,
                )
                time.sleep(wait)
                continue
            raise WBApiError(error.code, title, detail) from error
        except TimeoutError as error:
            raise WBApiError(
                None, detail=f"витрина WB не ответила за {REQUEST_TIMEOUT}с (таймаут)"
            ) from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, (socket.timeout, TimeoutError)):
                raise WBApiError(
                    None, detail=f"витрина WB не ответила за {REQUEST_TIMEOUT}с (таймаут)"
                ) from error
            raise WBApiError(None, detail=f"сеть: {error.reason}") from error

        try:
            return json.loads(raw_body)
        except (ValueError, TypeError) as error:
            raise WBApiError(
                None,
                detail=(
                    f"витрина WB вернула ответ, который не удалось разобрать как JSON: {raw_body[:200]!r}"
                ),
            ) from error

    raise WBApiError(None, detail="витрина WB не ответила")


def _expect(value, *keys, context: str):

    cur = value
    try:
        for key in keys:
            cur = cur[key]
        return cur
    except (KeyError, TypeError, IndexError) as e:
        raise WBApiError(None, detail=f"неожиданный формат ответа от WB ({context}): {value!r}"[:300]) from e


def get_own_warehouses(token: str) -> list[dict]:

    data = _request("GET", f"{FBS_BASE}/api/v3/warehouses", token)
    if data is None:
        return []
    if not isinstance(data, list):
        raise WBApiError(None, detail=f"неожиданный формат ответа от WB (список складов): {data!r}"[:300])
    return data


def get_fbs_stock(token: str, warehouse_id: int, barcodes: list[str]) -> dict[str, int]:

    unique: list[str] = []
    seen: set[str] = set()
    for barcode in barcodes:
        code = str(barcode or "").strip()
        if code and code not in seen:
            seen.add(code)
            unique.append(code)

    if not unique:
        return {}

    result: dict[str, int] = {}
    for start in range(0, len(unique), FBS_SKUS_PER_REQUEST):
        chunk = unique[start : start + FBS_SKUS_PER_REQUEST]
        data = _request(
            "POST",
            f"{FBS_BASE}/api/v3/stocks/{warehouse_id}",
            token,
            json_body={"skus": chunk},
        )
        stocks = (data or {}).get("stocks", [])
        try:
            for item in stocks:
                result[str(item["sku"])] = item.get("amount", 0)
        except (AttributeError, KeyError, TypeError) as e:
            raise WBApiError(None, detail=f"неожиданный формат остатков FBS: {stocks!r}"[:300]) from e

    return result


def get_fbs_orders(token: str, date_from: int, date_to: int) -> list[dict]:
    orders: list[dict] = []
    cursor = 0
    seen_cursors: set[int] = set()

    while cursor not in seen_cursors:
        seen_cursors.add(cursor)
        data = (
            _request(
                "GET",
                f"{FBS_BASE}/api/v3/orders",
                token,
                params={
                    "limit": FBS_ORDERS_PER_REQUEST,
                    "next": cursor,
                    "dateFrom": date_from,
                    "dateTo": date_to,
                },
            )
            or {}
        )
        page = data.get("orders")
        if not isinstance(page, list):
            raise WBApiError(None, detail=f"неожиданный формат FBS-заказов: {data!r}"[:300])
        orders.extend(page)
        next_cursor = data.get("next")
        if not page or next_cursor in (None, cursor):
            return orders
        try:
            cursor = int(next_cursor)
        except (TypeError, ValueError) as error:
            raise WBApiError(None, detail=f"неожиданный курсор FBS-заказов: {next_cursor!r}") from error

    return orders


def get_fbs_order_statuses(token: str, order_ids: list[int]) -> dict[int, dict]:
    unique = list(dict.fromkeys(int(order_id) for order_id in order_ids if int(order_id) > 0))
    result: dict[int, dict] = {}
    for start in range(0, len(unique), FBS_ORDERS_PER_REQUEST):
        chunk = unique[start : start + FBS_ORDERS_PER_REQUEST]
        data = (
            _request(
                "POST",
                f"{FBS_BASE}/api/v3/orders/status",
                token,
                json_body={"orders": chunk},
            )
            or {}
        )
        rows = data.get("orders")
        if not isinstance(rows, list):
            raise WBApiError(None, detail=f"неожиданный формат статусов FBS-заказов: {data!r}"[:300])
        for row in rows:
            try:
                result[int(row["id"])] = row
            except (KeyError, TypeError, ValueError) as error:
                raise WBApiError(
                    None, detail=f"неожиданная строка статуса FBS-заказа: {row!r}"[:300]
                ) from error
    return result


def get_fbw_supplies(
    token: str,
    *,
    status_ids: tuple[int, ...] = (2,),
    date_from: str | None = None,
    date_to: str | None = None,
    page_limit: int = 1000,
    max_pages: int = 10,
) -> list[dict]:
    """Return FBW supplies filtered by their current WB status."""

    limit = min(max(int(page_limit), 1), 1000)
    dates = []
    if date_from and date_to:
        dates.append({"from": date_from, "till": date_to, "type": "supplyDate"})
    supplies: list[dict] = []
    offset = 0
    for _ in range(max(int(max_pages), 1)):
        data = _request(
            "POST",
            f"{SUPPLIES_BASE}/api/v1/supplies",
            token,
            params={"limit": limit, "offset": offset},
            json_body={"dates": dates, "statusIDs": list(status_ids)},
        )
        if not isinstance(data, list):
            raise WBApiError(None, detail=f"неожиданный формат списка поставок FBW: {data!r}"[:300])
        page = [row for row in data if isinstance(row, dict)]
        if len(page) != len(data):
            raise WBApiError(None, detail="WB вернул некорректную строку в списке поставок FBW")
        supplies.extend(page)
        if len(page) < limit:
            return supplies
        offset += len(page)
    logger.warning("WB: список поставок FBW оборван после %s страниц", max_pages)
    return supplies


def _barcode_by_chrt_id(cards: list[dict]) -> dict[int, str]:
    result: dict[int, str] = {}
    try:
        for card in cards:
            for size in card.get("sizes") or []:
                skus = size.get("skus") or []
                if size.get("chrtID") and skus:
                    result[int(size["chrtID"])] = str(skus[0]).strip()
    except (AttributeError, TypeError, ValueError) as error:
        raise WBApiError(None, detail=f"неожиданный формат размеров карточек WB: {error}") from error
    return {chrt_id: barcode for chrt_id, barcode in result.items() if barcode}


def get_fbo_stock_by_warehouse(token: str) -> dict[tuple[str, str], int]:
    cards = get_cards_list(token)
    barcode_by_chrt_id = _barcode_by_chrt_id(cards)
    if not barcode_by_chrt_id:
        raise WBApiError(None, detail="не удалось сопоставить размеры карточек WB с баркодами")
    nm_ids = list(dict.fromkeys(int(card["nmID"]) for card in cards if str(card.get("nmID") or "").isdigit()))

    by_warehouse: dict[tuple[str, str], int] = {}
    offset = 0
    while True:
        body = {
            "nmIds": nm_ids if len(nm_ids) <= 1000 else [],
            "chrtIds": [],
            "limit": FBO_STOCK_PAGE_LIMIT,
            "offset": offset,
        }
        data = (
            _request(
                "POST",
                f"{ANALYTICS_BASE}/api/analytics/v1/stocks-report/wb-warehouses",
                token,
                json_body=body,
            )
            or {}
        )
        rows = _expect(data, "data", "items", context="остатки FBO по складам")
        if not isinstance(rows, list):
            raise WBApiError(None, detail=f"неожиданный формат остатков FBO: {rows!r}"[:300])

        try:
            for row in rows:
                barcode = barcode_by_chrt_id.get(int(row.get("chrtId") or 0))
                warehouse = str(row.get("warehouseName") or "").strip()
                if not barcode or not warehouse:
                    continue
                quantity = int(row.get("quantity") or 0)
                key = (barcode, warehouse)
                by_warehouse[key] = by_warehouse.get(key, 0) + quantity
        except (AttributeError, TypeError, ValueError) as error:
            raise WBApiError(None, detail=f"неожиданная строка остатков FBO: {error}") from error

        if len(rows) < FBO_STOCK_PAGE_LIMIT:
            return by_warehouse
        offset += len(rows)
        time.sleep(FBO_STOCK_PAGE_INTERVAL_SECONDS)


def get_cards_list(token: str, page_limit: int = CARDS_PAGE_LIMIT) -> list[dict]:

    cards: list[dict] = []
    cursor: dict = {"limit": page_limit}
    seen_nm: set[int] = set()

    while True:
        data = _request(
            "POST",
            f"{CONTENT_BASE}/content/v2/get/cards/list",
            token,
            json_body={"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}},
        )
        page = (data or {}).get("cards") or []
        if not isinstance(page, list):
            raise WBApiError(None, detail=f"неожиданный формат каталога WB: {page!r}"[:300])

        for card in page:
            nm_id = card.get("nmID")

            if nm_id in seen_nm:
                continue
            seen_nm.add(nm_id)
            cards.append(card)

        next_cursor = (data or {}).get("cursor") or {}
        total = next_cursor.get("total", 0)
        if not page or total < page_limit:
            return cards

        cursor = {
            "limit": page_limit,
            "updatedAt": next_cursor.get("updatedAt"),
            "nmID": next_cursor.get("nmID"),
        }
        if not cursor["updatedAt"] or not cursor["nmID"]:
            return cards


def normalize_card(card: dict) -> dict:

    sizes = []
    for size in card.get("sizes") or []:
        skus = [str(sku).strip() for sku in (size.get("skus") or []) if str(sku).strip()]
        if not skus:
            continue
        sizes.append(
            {
                "tech_size": str(size.get("techSize") or "").strip(),
                "barcode": skus[0],
                "extra_barcodes": skus[1:],
            }
        )

    image_url = ""
    for photo in card.get("photos") or []:
        if isinstance(photo, str) and photo.startswith(("https://", "http://")):
            image_url = photo
            break
        if isinstance(photo, dict):
            image_url = next(
                (
                    str(photo.get(key) or "").strip()
                    for key in ("c246x328", "big", "square", "tm")
                    if str(photo.get(key) or "").strip().startswith(("https://", "http://"))
                ),
                "",
            )
            if image_url:
                break

    return {
        "nm_id": str(card.get("nmID") or "").strip(),
        "vendor_code": str(card.get("vendorCode") or "").strip(),
        "title": str(card.get("title") or "").strip(),
        "subject_id": card.get("subjectID"),
        "subject_name": str(card.get("subjectName") or "").strip(),
        "image_url": image_url,
        "updated_at": str(card.get("updatedAt") or "").strip(),
        "sizes": sizes,
    }


ORDERS_PAGE_SIZE = 80000


ORDERS_PAGE_PAUSE_SECONDS = 61
PRICES_PAGE_LIMIT = 1000
PRICES_PAGE_PAUSE_SECONDS = 0.65
STOREFRONT_BATCH_PAUSE_SECONDS = 1.0
STOREFRONT_MAX_BATCH_SIZE = 1_000


def _get_statistics_rows(
    token: str, path: str, date_from: str, label: str, max_pages: int = 10
) -> list[dict]:

    rows_all: list[dict] = []
    cursor = date_from
    seen: set[str] = set()

    for page in range(max_pages):
        if page:
            logger.info(
                "WB API: перед следующей страницей «%s» ждём %.0f с",
                label,
                ORDERS_PAGE_PAUSE_SECONDS,
            )
            time.sleep(ORDERS_PAGE_PAUSE_SECONDS)

        logger.info(
            "WB API: загружаем «%s», страница %s/%s, dateFrom=%s",
            label,
            page + 1,
            max_pages,
            cursor,
        )
        rows = (
            _request(
                "GET",
                f"{STATISTICS_BASE}{path}",
                token,
                params={"dateFrom": cursor, "flag": 0},
            )
            or []
        )

        if not isinstance(rows, list):
            raise WBApiError(None, detail=f"неожиданный формат {label} WB: {rows!r}"[:300])
        logger.info("WB API: «%s», страница %s — получено строк: %s", label, page + 1, len(rows))

        for index, row in enumerate(rows):
            key = str(row.get("saleID") or row.get("srid") or "")
            if not key:
                key = f"{row.get('lastChangeDate')}:{row.get('gNumber')}:{index}"
            if key in seen:
                continue
            seen.add(key)
            rows_all.append(row)

        if len(rows) < ORDERS_PAGE_SIZE:
            return rows_all

        next_cursor = max((str(r.get("lastChangeDate") or "") for r in rows), default="")
        if not next_cursor or next_cursor == cursor:
            return rows_all
        cursor = next_cursor

    logger.warning("WB: %s оборваны на %s страницах — данных больше, чем ожидалось", label, max_pages)
    return rows_all


def get_orders(token: str, date_from: str, max_pages: int = 10) -> list[dict]:

    return _get_statistics_rows(
        token,
        "/api/v1/supplier/orders",
        date_from,
        "заказы",
        max_pages,
    )


def get_sales(token: str, date_from: str, max_pages: int = 10) -> list[dict]:

    return _get_statistics_rows(
        token,
        "/api/v1/supplier/sales",
        date_from,
        "продажи",
        max_pages,
    )


def get_goods_prices(token: str, page_limit: int = PRICES_PAGE_LIMIT) -> list[dict]:
    limit = min(max(int(page_limit), 1), PRICES_PAGE_LIMIT)
    offset = 0
    goods: list[dict] = []
    while True:
        if offset:
            time.sleep(PRICES_PAGE_PAUSE_SECONDS)
        payload = _request(
            "GET",
            f"{DISCOUNTS_PRICES_BASE}/api/v2/list/goods/filter",
            token,
            params={"limit": limit, "offset": offset},
        )
        if not isinstance(payload, dict):
            raise WBApiError(None, detail=f"неожиданный формат цен WB: {payload!r}"[:300])
        if payload.get("error"):
            message = str(payload.get("errorText") or "WB не вернул цены товаров")
            raise WBApiError(None, detail=message)
        data = payload.get("data") or {}
        rows = data.get("listGoods") if isinstance(data, dict) else None
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise WBApiError(None, detail=f"неожиданный список цен WB: {rows!r}"[:300])
        goods.extend(row for row in rows if isinstance(row, dict))
        if len(rows) < limit:
            return goods
        offset += len(rows)


def get_goods_prices_by_nm_ids(token: str, nm_ids: list[int] | tuple[int, ...]) -> list[dict]:
    unique = list(dict.fromkeys(int(nm_id) for nm_id in nm_ids))
    if not unique:
        return []
    if len(unique) > PRICES_PAGE_LIMIT:
        raise ValueError(f"За один запрос можно получить не более {PRICES_PAGE_LIMIT} товаров")
    payload = _request(
        "POST",
        f"{DISCOUNTS_PRICES_BASE}/api/v2/list/goods/filter",
        token,
        json_body={"nmList": unique},
    )
    if not isinstance(payload, dict):
        raise WBApiError(None, detail=f"неожиданный формат цен WB: {payload!r}"[:300])
    if payload.get("error"):
        message = str(payload.get("errorText") or "WB не вернул цены товаров")
        raise WBApiError(None, detail=message)
    data = payload.get("data") or {}
    rows = data.get("listGoods") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise WBApiError(None, detail=f"неожиданный список цен WB: {rows!r}"[:300])
    return [row for row in rows if isinstance(row, dict)]


def upload_goods_prices_and_discounts(token: str, goods: list[dict]) -> dict:
    if not goods:
        raise ValueError("Нет цен для отправки")
    if len(goods) > PRICES_PAGE_LIMIT:
        raise ValueError(f"За один запрос можно изменить не более {PRICES_PAGE_LIMIT} товаров")
    payload = _request(
        "POST",
        f"{DISCOUNTS_PRICES_BASE}/api/v2/upload/task",
        token,
        json_body={"data": goods},
    )
    if not isinstance(payload, dict):
        raise WBApiError(None, detail=f"неожиданный ответ загрузки цен WB: {payload!r}"[:300])
    if payload.get("error"):
        message = str(payload.get("errorText") or "WB не принял цены")
        raise WBApiError(None, detail=message)
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("id") is None:
        raise WBApiError(None, detail=f"в ответе WB нет ID загрузки: {payload!r}"[:300])
    return data


def get_price_upload_status(token: str, upload_id: int) -> dict | None:
    payload = _request(
        "GET",
        f"{DISCOUNTS_PRICES_BASE}/api/v2/history/tasks",
        token,
        params={"uploadID": int(upload_id)},
    )
    if not isinstance(payload, dict):
        raise WBApiError(None, detail=f"неожиданный статус загрузки цен WB: {payload!r}"[:300])
    data = payload.get("data")
    if data is None:
        return None
    if payload.get("error"):
        message = str(payload.get("errorText") or "WB не вернул статус загрузки цен")
        raise WBApiError(None, detail=message)
    if not isinstance(data, dict):
        raise WBApiError(None, detail=f"неожиданные данные статуса цен WB: {data!r}"[:300])
    return data


def get_price_upload_details(token: str, upload_id: int) -> list[dict]:
    payload = _request(
        "GET",
        f"{DISCOUNTS_PRICES_BASE}/api/v2/history/goods/task",
        token,
        params={"uploadID": int(upload_id), "limit": PRICES_PAGE_LIMIT, "offset": 0},
    )
    if not isinstance(payload, dict):
        raise WBApiError(None, detail=f"неожиданные детали загрузки цен WB: {payload!r}"[:300])
    if payload.get("error"):
        message = str(payload.get("errorText") or "WB не вернул детали загрузки цен")
        raise WBApiError(None, detail=message)
    data = payload.get("data") or {}
    rows = data.get("historyGoods") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise WBApiError(None, detail=f"неожиданный список обработки цен WB: {rows!r}"[:300])
    return [row for row in rows if isinstance(row, dict)]


def get_storefront_products(
    nm_ids: list[str] | tuple[str, ...] | set[str],
    *,
    batch_size: int | None = None,
) -> dict:
    """Return public WB card data in caller-controlled batches without a seller token.

    A failed batch is reported instead of aborting the remaining batches so the
    caller can use an order-price fallback only for affected products. The
    regular scheduler relies on the configured safe batch size; a manual probe
    may explicitly request a larger batch.
    """

    unique: list[str] = []
    seen: set[str] = set()
    for value in nm_ids:
        nm_id = str(value or "").strip()
        if not nm_id.isdigit() or int(nm_id) <= 0 or nm_id in seen:
            continue
        seen.add(nm_id)
        unique.append(nm_id)
    if not unique:
        return {"products": [], "failed_nm_ids": [], "errors": []}

    limit = min(
        max(int(batch_size or settings.wb_storefront_batch_size), 1),
        STOREFRONT_MAX_BATCH_SIZE,
    )
    total_batches = (len(unique) + limit - 1) // limit
    products_by_nm: dict[str, dict] = {}
    failed_nm_ids: set[str] = set()
    errors: list[str] = []
    for start in range(0, len(unique), limit):
        if start:
            logger.info(
                "Витрина WB: перед следующей пачкой ждём %.1f с",
                STOREFRONT_BATCH_PAUSE_SECONDS,
            )
            time.sleep(STOREFRONT_BATCH_PAUSE_SECONDS)
        chunk = unique[start : start + limit]
        batch_number = start // limit + 1
        logger.info(
            "Витрина WB: запрашиваем пачку %s/%s, товаров=%s, артикулы=%s…%s",
            batch_number,
            total_batches,
            len(chunk),
            chunk[0],
            chunk[-1],
        )
        started = time.monotonic()
        try:
            payload = _public_request(
                STOREFRONT_CARDS_BASE,
                params={
                    "appType": 1,
                    "curr": "rub",
                    "dest": settings.wb_storefront_dest,
                    "lang": "ru",
                    "spp": 30,
                    "nm": ";".join(chunk),
                },
            )
            if not isinstance(payload, dict):
                raise WBApiError(None, detail="витрина WB вернула неожиданный формат карточек")
            rows = payload.get("products")
            if rows is None and isinstance(payload.get("data"), dict):
                rows = payload["data"].get("products")
            if not isinstance(rows, list):
                raise WBApiError(None, detail="витрина WB не вернула список товаров")
        except Exception as error:
            failed_nm_ids.update(chunk)
            message = (
                f"товары {chunk[0]}–{chunk[-1]}: {error.friendly if isinstance(error, WBApiError) else error}"
            )
            errors.append(message)
            logger.warning(
                "Витрина WB: пачка %s/%s не загружена за %.1f с: %s",
                batch_number,
                total_batches,
                time.monotonic() - started,
                message,
            )
            continue

        returned: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            nm_id = str(row.get("id") or row.get("nmID") or row.get("nmId") or "").strip()
            if nm_id in seen:
                products_by_nm[nm_id] = row
                returned.add(nm_id)
        failed_nm_ids.update(set(chunk) - returned)
        logger.info(
            "Витрина WB: пачка %s/%s готова за %.1f с, получено=%s, пропущено=%s",
            batch_number,
            total_batches,
            time.monotonic() - started,
            len(returned),
            len(set(chunk) - returned),
        )

    return {
        "products": list(products_by_nm.values()),
        "failed_nm_ids": sorted(failed_nm_ids, key=int),
        "errors": errors,
    }


def get_default_wallet_discount_percent() -> float:
    """Return the public WB Wallet discount used for an unauthenticated buyer."""

    payload = _public_request(STOREFRONT_DEFAULT_PAYMENT_URL)
    if not isinstance(payload, dict) or payload.get("state") != 0:
        raise WBApiError(None, detail="WB не вернул настройку скидки кошелька")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise WBApiError(None, detail="WB вернул неожиданный формат скидки кошелька")
    payment = next(
        (row for row in rows if isinstance(row, dict) and row.get("wctype_id") == 1),
        None,
    )
    try:
        percent = float(payment.get("discount_value")) if payment else None
    except (TypeError, ValueError) as error:
        raise WBApiError(None, detail="WB вернул некорректную скидку кошелька") from error
    if percent is None or not 0 <= percent < 100:
        raise WBApiError(None, detail="WB вернул некорректную скидку кошелька")
    return percent
