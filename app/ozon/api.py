import json
import logging
import random
import socket
import threading
import time
import urllib.error
import urllib.request

from app.config import settings

logger = logging.getLogger(__name__)


_current = threading.local()


def set_store_context(label: str) -> None:

    _current.store = label


def clear_store_context() -> None:
    _current.store = ""


def _store_label() -> str:
    label = getattr(_current, "store", "")
    return f"[{label}] " if label else ""


BASE_URL = "https://api-seller.ozon.ru"
REQUEST_TIMEOUT = settings.ozon_request_timeout_seconds


MAX_ATTEMPTS = settings.ozon_request_attempts
RETRY_BACKOFF_SECONDS = settings.ozon_retry_backoff_seconds


PATH_MAX_ATTEMPTS = {
    "/v1/analytics/stocks": 2,
}


THROTTLED_PATHS = {
    "/v1/analytics/stocks": 1.5,
}


THROTTLE_MAX_INTERVAL = 20.0
THROTTLE_GROWTH = 2.0

THROTTLE_RELAX_AFTER = 5
THROTTLE_RELAX_FACTOR = 0.8

_throttle_lock = threading.Lock()
_last_call_at: dict[str, float] = {}
_interval: dict[str, float] = {}
_calm_streak: dict[str, int] = {}


def _throttle(path: str) -> None:

    if path not in THROTTLED_PATHS:
        return

    with _throttle_lock:
        min_interval = _interval.setdefault(path, THROTTLED_PATHS[path])
        now = time.monotonic()
        wait = min_interval - (now - _last_call_at.get(path, 0.0))
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _last_call_at[path] = now


def _note_rate_limit(path: str) -> float:

    if path not in THROTTLED_PATHS:
        return 0.0

    with _throttle_lock:
        current = _interval.get(path, THROTTLED_PATHS[path])
        updated = min(current * THROTTLE_GROWTH, THROTTLE_MAX_INTERVAL)
        _interval[path] = updated
        _calm_streak[path] = 0

    if updated > current:
        logger.info(
            "%sOzon %s: интервал между запросами увеличен до %.1f с",
            _store_label(),
            path,
            updated,
        )
    return updated


def _note_success(path: str) -> None:

    if path not in THROTTLED_PATHS:
        return

    with _throttle_lock:
        base = THROTTLED_PATHS[path]
        current = _interval.get(path, base)
        if current <= base:
            return

        streak = _calm_streak.get(path, 0) + 1
        if streak < THROTTLE_RELAX_AFTER:
            _calm_streak[path] = streak
            return

        _calm_streak[path] = 0
        _interval[path] = max(base, current * THROTTLE_RELAX_FACTOR)


def _retry_after(headers) -> float | None:

    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return None


def _backoff_pause(attempt: int) -> float:

    return RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 1.5)


PAGE_SIZE = 1000


SCHEME_FBO = "fbo"
SCHEME_FBS = "fbs"
SCHEME_RFBS = "rfbs"

_FRIENDLY_BY_STATUS = {
    400: "неверный запрос к Ozon (400)",
    401: "Ozon не принял ключ (401) — проверьте Client-Id и Api-Key",
    403: "нет доступа (403) — у ключа не хватает прав или он отозван",
    404: "метод Ozon не найден (404) — возможно, изменилась версия API",
    409: "конфликт запроса (409)",
    429: "слишком часто обращаемся к Ozon (429) — сработал лимит запросов",
    500: "внутренняя ошибка на стороне Ozon (500)",
    502: "Ozon недоступен (502)",
    503: "Ozon недоступен (503)",
    504: "Ozon не ответил вовремя (504)",
}


class OzonApiError(Exception):
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
            return f"Ozon вернул ошибку {self.status}: {self.detail or 'без описания'}"
        return self.detail or "неизвестная ошибка при обращении к Ozon"


_GRPC_CODES = {
    2: "внутренний сбой на стороне Ozon",
    4: "Ozon не уложился в свой таймаут",
    8: "исчерпан лимит запросов",
    13: "внутренняя ошибка Ozon",
    14: "сервис Ozon временно недоступен",
}


def _parse_error_body(raw: str) -> str:

    try:
        data = json.loads(raw)
    except ValueError:
        return raw[:200]

    if not isinstance(data, dict):
        return raw[:200]

    message = str(data.get("message") or data.get("error") or "").strip()
    code = data.get("code")
    hint = _GRPC_CODES.get(code) if isinstance(code, int) else None

    if message and hint and message != hint:
        return f"{message}; код {code}: {hint}"
    if hint:
        return f"код {code}: {hint}"
    return message or raw[:200]


def _request(path: str, client_id: str, api_key: str, payload: dict) -> dict:

    url = BASE_URL + path
    body = json.dumps(payload).encode("utf-8")

    last_error: OzonApiError | None = None
    max_attempts = PATH_MAX_ATTEMPTS.get(path, MAX_ATTEMPTS)

    for attempt in range(1, max_attempts + 1):
        _throttle(path)
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Client-Id": client_id,
                "Api-Key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                raw = response.read().decode("utf-8")
            _note_success(path)
            try:
                return json.loads(raw)
            except ValueError as e:
                raise OzonApiError(None, f"Ozon вернул не-JSON ответ: {e}") from e

        except urllib.error.HTTPError as e:
            detail = _parse_error_body(e.read().decode("utf-8", errors="replace"))
            last_error = OzonApiError(e.code, detail)

            if e.code == 429 or 500 <= e.code < 600:
                if e.code == 429:
                    _note_rate_limit(path)

                if attempt < max_attempts:
                    pause = _retry_after(getattr(e, "headers", None)) or _backoff_pause(attempt)
                    logger.warning(
                        "%sOzon %s: %s, повтор через %.1f с",
                        _store_label(),
                        path,
                        last_error.friendly,
                        pause,
                    )
                    time.sleep(pause)
                    continue
            raise last_error from e

        except TimeoutError as e:
            last_error = OzonApiError(None, "Ozon не ответил за отведённое время")
            if attempt < max_attempts:
                time.sleep(_backoff_pause(attempt))
                continue
            raise last_error from e

        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise OzonApiError(None, "Ozon не ответил за отведённое время") from e
            raise OzonApiError(None, f"сеть: {reason}") from e

    raise last_error or OzonApiError(None, "не удалось выполнить запрос к Ozon")


def request(path: str, client_id: str, api_key: str, payload: dict) -> dict:
    return _request(path, client_id, api_key, payload)


def get_own_warehouses(client_id: str, api_key: str) -> list[dict]:

    warehouses: list[dict] = []
    cursor = ""

    while True:
        payload = {"limit": 100}
        if cursor:
            payload["cursor"] = cursor

        data = _request("/v2/warehouse/list", client_id, api_key, payload)

        page = data.get("warehouses")
        if page is None:
            page = data.get("result")
        if page is None:
            raise OzonApiError(None, "неожиданный ответ на список складов")
        if not isinstance(page, list):
            raise OzonApiError(None, "неожиданный формат списка складов")

        warehouses.extend(page)

        cursor = data.get("cursor") or ""
        if not data.get("has_next") or not cursor:
            return warehouses


def get_fbo_stock_by_warehouse(client_id: str, api_key: str) -> list[dict]:

    rows: list[dict] = []
    offset = 0

    while True:
        data = _request(
            "/v2/analytics/stock_on_warehouses",
            client_id,
            api_key,
            {"limit": PAGE_SIZE, "offset": offset},
        )
        result = data.get("result") or {}
        page = result.get("rows")
        if page is None:
            raise OzonApiError(None, "неожиданный ответ на остатки FBO (нет result.rows)")

        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows

        offset += PAGE_SIZE
        if offset > 200_000:
            logger.warning("%sOzon: прервали обход остатков FBO на offset=%s", _store_label(), offset)
            return rows


def get_stock_analytics(client_id: str, api_key: str, skus: list[int]) -> list[dict]:

    if not skus:
        return []

    rows: list[dict] = []
    CHUNK = 100

    for start_idx in range(0, len(skus), CHUNK):
        chunk = [str(s) for s in skus[start_idx : start_idx + CHUNK]]
        data = _request("/v1/analytics/stocks", client_id, api_key, {"skus": chunk})

        page = data.get("items")
        if page is None:
            page = (data.get("result") or {}).get("items")
        if page is None:
            raise OzonApiError(None, f"неожиданный ответ на аналитику остатков (ключи: {sorted(data)[:6]})")
        rows.extend(page)

    return rows


def normalize_analytics_row(row: dict) -> dict:

    def num(key: str) -> int:
        try:
            return int(row.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "sku": row.get("sku"),
        "item_code": row.get("offer_id") or "",
        "name": row.get("name") or "",
        "warehouse_name": row.get("warehouse_name") or "",
        "cluster_name": row.get("cluster_name") or "",
        "available": num("available_stock_count"),
        "transit": num("transit_stock_count"),
        "expiring": num("expiring_stock_count"),
        "excess": num("excess_stock_count"),
        "defect": num("stock_defect_stock_count"),
        "returns": num("return_from_customer_stock_count"),
    }


def get_product_stocks(client_id: str, api_key: str) -> list[dict]:

    items: list[dict] = []
    cursor = ""

    while True:
        data = _request(
            "/v4/product/info/stocks",
            client_id,
            api_key,
            {"cursor": cursor, "limit": PAGE_SIZE, "filter": {"visibility": "ALL"}},
        )
        page = data.get("items")
        if page is None:
            raise OzonApiError(None, "неожиданный ответ на остатки товаров (нет items)")

        items.extend(page)
        cursor = data.get("cursor") or ""

        if not cursor or len(page) < PAGE_SIZE:
            return items


INFO_CHUNK = 1000


def get_product_list(client_id: str, api_key: str) -> list[dict]:

    items: list[dict] = []
    last_id = ""

    while True:
        data = _request(
            "/v3/product/list",
            client_id,
            api_key,
            {"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": PAGE_SIZE},
        )
        result = data.get("result") or data
        page = result.get("items")
        if page is None:
            raise OzonApiError(None, f"неожиданный ответ на список товаров (ключи: {sorted(result)[:6]})")

        items.extend(page)
        last_id = result.get("last_id") or ""

        if not page or not last_id or len(page) < PAGE_SIZE:
            return items

        if len(items) > 200_000:
            logger.warning("%sOzon: прервали обход каталога на %s карточках", _store_label(), len(items))
            return items


def get_product_info(client_id: str, api_key: str, product_ids: list[int]) -> list[dict]:

    if not product_ids:
        return []

    items: list[dict] = []

    for start in range(0, len(product_ids), INFO_CHUNK):
        chunk = [int(pid) for pid in product_ids[start : start + INFO_CHUNK]]
        data = _request(
            "/v3/product/info/list",
            client_id,
            api_key,
            {"product_id": chunk, "offer_id": [], "sku": []},
        )
        result = data.get("result") or data
        page = result.get("items")
        if page is None:
            raise OzonApiError(None, f"неожиданный ответ на карточки товаров (ключи: {sorted(result)[:6]})")
        items.extend(page)

    return items


def _get_postings(path: str, client_id: str, api_key: str, since: str, to: str) -> list[dict]:

    rows: list[dict] = []
    offset = 0

    while True:
        data = _request(
            path,
            client_id,
            api_key,
            {
                "dir": "ASC",
                "filter": {"since": since, "to": to},
                "limit": PAGE_SIZE,
                "offset": offset,
                "with": {
                    "analytics_data": True,
                    "barcodes": True,
                    "financial_data": True,
                    "translit": False,
                },
            },
        )

        result = data["result"] if "result" in data else data
        page = result.get("postings") if isinstance(result, dict) else result
        if page is None and isinstance(result, dict):
            page = result.get("items")
        if not isinstance(page, list):
            raise OzonApiError(None, f"неожиданный формат отправлений Ozon: {result!r}"[:300])

        rows.extend(page)
        has_next = result.get("has_next") if isinstance(result, dict) else None
        if not page or has_next is False or len(page) < PAGE_SIZE:
            return rows

        offset += len(page)
        if offset > 500_000:
            logger.warning("%sOzon: прервали обход отправлений %s на offset=%s", _store_label(), path, offset)
            return rows


def get_fbo_postings(client_id: str, api_key: str, since: str, to: str) -> list[dict]:
    return _get_postings("/v2/posting/fbo/list", client_id, api_key, since, to)


def get_fbs_postings(client_id: str, api_key: str, since: str, to: str) -> list[dict]:
    return _get_postings("/v3/posting/fbs/list", client_id, api_key, since, to)


def normalize_product(row: dict) -> dict:

    barcodes: list[str] = []
    raw_barcodes = row.get("barcodes")
    if isinstance(raw_barcodes, list):
        barcodes = [str(b).strip() for b in raw_barcodes if str(b or "").strip()]
    single = str(row.get("barcode") or "").strip()
    if single and single not in barcodes:
        barcodes.insert(0, single)

    sku = row.get("sku")
    if not sku:
        for source in row.get("sources") or []:
            if source.get("sku"):
                sku = source["sku"]
                break

    try:
        sku_value = int(sku) if sku else None
    except (TypeError, ValueError):
        sku_value = None

    image_url = ""
    image_candidates = [row.get("primary_image"), row.get("primary_image_url")]
    image_candidates.extend(row.get("images") or [])
    for image in image_candidates:
        if isinstance(image, str):
            candidates = [image]
        elif isinstance(image, list):
            candidates = image
        elif isinstance(image, dict):
            candidates = [image.get("file_name"), image.get("url")]
        else:
            candidates = []

        image_url = next(
            (
                str(candidate or "").strip()
                for candidate in candidates
                if str(candidate or "").strip().startswith(("https://", "http://"))
            ),
            "",
        )
        if image_url:
            break

    return {
        "product_id": row.get("id") or row.get("product_id"),
        "offer_id": str(row.get("offer_id") or "").strip(),
        "sku": sku_value,
        "name": str(row.get("name") or "").strip(),
        "image_url": image_url,
        "barcodes": barcodes,
        "archived": bool(row.get("is_archived") or row.get("archived")),
        "updated_at": str(row.get("updated_at") or "").strip(),
    }
