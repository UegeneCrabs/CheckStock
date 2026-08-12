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
ANALYTICS_BASE = "https://seller-analytics-api.wildberries.ru"
CONTENT_BASE = "https://content-api.wildberries.ru"
STATISTICS_BASE = "https://statistics-api.wildberries.ru"
PRICES_BASE = "https://discounts-prices-api.wildberries.ru"
COMMON_BASE = "https://common-api.wildberries.ru"


CARDS_PAGE_LIMIT = 100


FBS_SKUS_PER_REQUEST = 1000

REQUEST_TIMEOUT = settings.wb_request_timeout_seconds


NON_WAREHOUSE_LABELS = {
    "В пути до получателей",
    "В пути возвраты на склад WB",
    "Всего находится на складах",
}


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


def _create_warehouse_remains_task(token: str) -> str:
    data = _request(
        "GET",
        f"{ANALYTICS_BASE}/api/v1/warehouse_remains",
        token,
        params={"groupByNm": "true", "groupByBarcode": "true"},
    )
    return _expect(data, "data", "taskId", context="создание задачи на отчёт FBO")


def _get_warehouse_remains_status(token: str, task_id: str) -> str:
    data = _request("GET", f"{ANALYTICS_BASE}/api/v1/warehouse_remains/tasks/{task_id}/status", token)
    return _expect(data, "data", "status", context="статус отчёта FBO")


def _download_warehouse_remains(token: str, task_id: str) -> list[dict]:
    return _request("GET", f"{ANALYTICS_BASE}/api/v1/warehouse_remains/tasks/{task_id}/download", token) or []


def get_fbo_stock_by_warehouse(
    token: str, poll_interval: float = 5.0, max_wait: float = 120.0
) -> dict[tuple[str, str], int]:

    task_id = _create_warehouse_remains_task(token)

    waited = 0.0
    status = "processing"
    while status not in ("done", "error", "purged", "canceled") and waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval
        status = _get_warehouse_remains_status(token, task_id)

    if status != "done":
        raise WBApiError(None, detail=f"отчёт по остаткам не собрался за {max_wait:.0f} с (статус: {status})")

    rows = _download_warehouse_remains(token, task_id)
    if not isinstance(rows, list):
        raise WBApiError(None, detail=f"неожиданный формат отчёта FBO: {rows!r}"[:300])

    by_warehouse: dict[tuple[str, str], int] = {}
    try:
        for row in rows:
            barcode = str(row.get("barcode", ""))
            if not barcode:
                continue
            for wh in row.get("warehouses", []):
                name = wh.get("warehouseName", "")
                if name in NON_WAREHOUSE_LABELS:
                    continue
                qty = wh.get("quantity", 0)
                by_warehouse[(barcode, name)] = by_warehouse.get((barcode, name), 0) + qty
    except (AttributeError, TypeError) as e:
        raise WBApiError(None, detail=f"неожиданная строка в отчёте FBO: {e}") from e

    return by_warehouse


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


def get_products_with_prices(token: str, nm_ids: list[int]) -> list[dict]:

    unique = list(dict.fromkeys(int(nm_id) for nm_id in nm_ids if int(nm_id) > 0))
    result: list[dict] = []

    for start in range(0, len(unique), 1000):
        data = _request(
            "POST",
            f"{PRICES_BASE}/api/v2/list/goods/filter",
            token,
            json_body={"nmList": unique[start : start + 1000]},
        )
        rows = _expect(data or {}, "data", "listGoods", context="цены товаров")
        if not isinstance(rows, list):
            raise WBApiError(None, detail=f"неожиданный формат цен WB: {rows!r}"[:300])
        result.extend(rows)

    return result


def get_category_commissions(token: str) -> list[dict]:

    data = _request(
        "GET",
        f"{COMMON_BASE}/api/v1/tariffs/commission",
        token,
        params={"locale": "ru"},
    )
    rows = (data or {}).get("report") or []
    if not isinstance(rows, list):
        raise WBApiError(None, detail=f"неожиданный формат комиссий WB: {rows!r}"[:300])
    return rows


ORDERS_PAGE_SIZE = 80000


ORDERS_PAGE_PAUSE_SECONDS = 61


def _get_statistics_rows(
    token: str, path: str, date_from: str, label: str, max_pages: int = 10
) -> list[dict]:

    rows_all: list[dict] = []
    cursor = date_from
    seen: set[str] = set()

    for page in range(max_pages):
        if page:
            time.sleep(ORDERS_PAGE_PAUSE_SECONDS)

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
