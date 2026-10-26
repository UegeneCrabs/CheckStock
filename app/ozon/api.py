import json
import logging
import math
import random
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from app.config import settings
from app.core.constants import (
    HTTP,
    OZON_API,
    OZON_STOCK_ANALYTICS,
    HTTPStatus,
    OrderScheme,
    OzonErrorCode,
    SortDirection,
)

PAGE_SIZE = OZON_API.page_size
FBS_POSTINGS_V4_PAGE_SIZE = OZON_API.fbs_postings_v4_page_size
BASE_URL = OZON_API.base_url

SCHEME_FBO = OrderScheme.FBO.value
SCHEME_FBS = OrderScheme.FBS.value

logger = logging.getLogger(__name__)


_current = threading.local()


def set_store_context(label: str) -> None:

    _current.store = label


def clear_store_context() -> None:
    _current.store = ""


def _store_label() -> str:
    label = getattr(_current, "store", "")
    return f"[{label}] " if label else ""


REQUEST_TIMEOUT = settings.ozon_request_timeout_seconds


MAX_ATTEMPTS = settings.ozon_request_attempts
RETRY_BACKOFF_SECONDS = settings.ozon_retry_backoff_seconds


PATH_MAX_ATTEMPTS = {
    OZON_STOCK_ANALYTICS.path: OZON_STOCK_ANALYTICS.request_attempts,
    "/v1/supply-order/act/product/get": 2,
}


THROTTLED_PATHS = {
    OZON_STOCK_ANALYTICS.path: OZON_STOCK_ANALYTICS.minimum_interval_seconds,
    "/v2/review/list": 1.0,
    "supply-orders": 0.55,
    "/v1/supply-order/act/product/get": 0.55,
}


THROTTLE_MAX_INTERVAL = 20.0
THROTTLE_GROWTH = 2.0

THROTTLE_RELAX_AFTER = 5
THROTTLE_RELAX_FACTOR = 0.8

_throttle_lock = threading.Lock()
_last_call_at: dict[tuple[str, str], float] = {}
_interval: dict[tuple[str, str], float] = {}
_calm_streak: dict[tuple[str, str], int] = {}
_cooldown_until: dict[tuple[str, str], float] = {}


def _throttle_key(path: str) -> str:
    if path in THROTTLED_PATHS:
        return path
    return "supply-orders" if "/supply-order/" in path else path


def _throttle(path: str, client_id: str = "") -> None:
    key = _throttle_key(path)
    if key not in THROTTLED_PATHS:
        return
    paths = [key]
    if "/supply-order/" in path and key != "supply-orders":
        paths.append("supply-orders")

    while True:
        with _throttle_lock:
            now = time.monotonic()
            wait = max(
                max(
                    _last_call_at.get((client_id, name), float("-inf"))
                    + _interval.setdefault((client_id, name), THROTTLED_PATHS[name]),
                    _cooldown_until.get((client_id, name), 0.0),
                )
                - now
                for name in paths
            )
            if wait <= 0:
                for name in paths:
                    _last_call_at[(client_id, name)] = now
                return
        time.sleep(wait)


def _defer_requests(path: str, client_id: str, pause: float) -> None:
    name = _throttle_key(path)
    if name not in THROTTLED_PATHS:
        return
    with _throttle_lock:
        key = (client_id, name)
        _cooldown_until[key] = max(_cooldown_until.get(key, 0.0), time.monotonic() + pause)


def _note_rate_limit(path: str, client_id: str = "") -> float:

    path = _throttle_key(path)
    if path not in THROTTLED_PATHS:
        return 0.0

    with _throttle_lock:
        key = (client_id, path)
        current = _interval.get(key, THROTTLED_PATHS[path])
        updated = min(current * THROTTLE_GROWTH, THROTTLE_MAX_INTERVAL)
        _interval[key] = updated
        _calm_streak[key] = 0

    if updated > current:
        logger.info(
            "%sOzon %s: интервал между запросами увеличен до %.1f с",
            _store_label(),
            path,
            updated,
        )
    return updated


def _note_success(path: str, client_id: str = "") -> None:

    path = _throttle_key(path)
    if path not in THROTTLED_PATHS:
        return

    with _throttle_lock:
        base = THROTTLED_PATHS[path]
        key = (client_id, path)
        current = _interval.get(key, base)
        if current <= base:
            return

        streak = _calm_streak.get(key, 0) + 1
        if streak < THROTTLE_RELAX_AFTER:
            _calm_streak[key] = streak
            return

        _calm_streak[key] = 0
        _interval[key] = max(base, current * THROTTLE_RELAX_FACTOR)


def _retry_after(headers) -> float | None:

    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return None
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        try:
            moment = parsedate_to_datetime(str(raw))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            seconds = (moment - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def _backoff_pause(attempt: int) -> float:

    return RETRY_BACKOFF_SECONDS * (HTTP.retry_backoff_multiplier ** (attempt - 1)) + random.uniform(
        0, HTTP.retry_jitter_max_seconds
    )


def _retry_pause(path: str, attempt: int, retry_after: float | None, interval: float = 0.0) -> float:
    backoff = _backoff_pause(attempt)
    if path != OZON_STOCK_ANALYTICS.path:
        return retry_after if retry_after is not None else backoff
    return max(
        min(max(backoff, OZON_STOCK_ANALYTICS.retry_base_seconds), OZON_STOCK_ANALYTICS.retry_max_seconds),
        retry_after or 0.0,
        interval,
    )


SCHEME_RFBS = "rfbs"

_FRIENDLY_BY_STATUS = {
    HTTPStatus.BAD_REQUEST: "неверный запрос к Ozon (400)",
    HTTPStatus.UNAUTHORIZED: "Ozon не принял ключ (401) — проверьте Client-Id и Api-Key",
    HTTPStatus.FORBIDDEN: "нет доступа (403) — у ключа не хватает прав или он отозван",
    HTTPStatus.NOT_FOUND: "метод Ozon не найден (404) — возможно, изменилась версия API",
    HTTPStatus.CONFLICT: "конфликт запроса (409)",
    HTTPStatus.TOO_MANY_REQUESTS: "слишком часто обращаемся к Ozon (429) — сработал лимит запросов",
    HTTPStatus.INTERNAL_SERVER_ERROR: "внутренняя ошибка на стороне Ozon (500)",
    HTTPStatus.BAD_GATEWAY: "Ozon недоступен (502)",
    HTTPStatus.SERVICE_UNAVAILABLE: "Ozon недоступен (503)",
    HTTPStatus.GATEWAY_TIMEOUT: "Ozon не ответил вовремя (504)",
}


class OzonApiError(Exception):
    def __init__(self, status: int | None, detail: str = "") -> None:
        self.status = status
        self.detail = detail
        super().__init__(self.friendly)

    @property
    def friendly(self) -> str:
        base = _FRIENDLY_BY_STATUS.get(self.status)
        if (
            self.status == HTTPStatus.FORBIDDEN
            and OZON_API.subscription_error_marker in self.detail.casefold()
        ):
            base = OZON_API.subscription_error_message
        if base:
            return f"{base} ({self.detail})" if self.detail else base
        if self.status:
            return f"Ozon вернул ошибку {self.status}: {self.detail or 'без описания'}"
        return self.detail or "неизвестная ошибка при обращении к Ozon"


_GRPC_CODES = {
    OzonErrorCode.UNKNOWN: "внутренний сбой на стороне Ozon",
    OzonErrorCode.DEADLINE_EXCEEDED: "Ozon не уложился в свой таймаут",
    OzonErrorCode.RESOURCE_EXHAUSTED: "исчерпан лимит запросов",
    OzonErrorCode.INTERNAL: "внутренняя ошибка Ozon",
    OzonErrorCode.UNAVAILABLE: "сервис Ozon временно недоступен",
}


def _parse_error_body(raw: str) -> str:

    try:
        data = json.loads(raw)
    except ValueError:
        return raw[: OZON_API.error_body_length]

    if not isinstance(data, dict):
        return raw[: OZON_API.error_body_length]

    message = str(data.get("message") or data.get("error") or "").strip()
    code = data.get("code")
    hint = _GRPC_CODES.get(code) if isinstance(code, int) else None

    if message and hint and message != hint:
        return f"{message}; код {code}: {hint}"
    if hint:
        return f"код {code}: {hint}"
    return message or raw[: OZON_API.error_body_length]


def _request(path: str, client_id: str, api_key: str, payload: dict) -> dict:

    url = BASE_URL + path
    body = json.dumps(payload).encode("utf-8")

    last_error: OzonApiError | None = None
    max_attempts = PATH_MAX_ATTEMPTS.get(path, MAX_ATTEMPTS)

    for attempt in range(1, max_attempts + 1):
        _throttle(path, client_id)
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
            _note_success(path, client_id)
            try:
                return json.loads(raw)
            except ValueError as e:
                raise OzonApiError(None, f"Ozon вернул не-JSON ответ: {e}") from e

        except urllib.error.HTTPError as e:
            detail = _parse_error_body(e.read().decode("utf-8", errors="replace"))
            last_error = OzonApiError(e.code, detail)

            if e.code == 429 or 500 <= e.code < 600:
                interval = 0.0
                if e.code == 429:
                    interval = _note_rate_limit(path, client_id)

                pause = _retry_pause(path, attempt, _retry_after(e.headers), interval)
                if e.code == 429:
                    _defer_requests(path, client_id, pause)

                if attempt < max_attempts:
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
                time.sleep(_retry_pause(path, attempt, None))
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


def get_fbs_stock_by_warehouse(client_id: str, api_key: str, skus: list[str]) -> list[dict]:
    rows: list[dict] = []
    unique_skus = sorted(set(skus))
    for start in range(0, len(unique_skus), 100):
        cursor = ""
        seen: set[str] = set()
        while True:
            data = _request(
                "/v2/product/info/stocks-by-warehouse/fbs",
                client_id,
                api_key,
                {"sku": unique_skus[start : start + 100], "limit": 1000, "cursor": cursor},
            )
            page = data.get("products")
            if not isinstance(page, list):
                raise OzonApiError(None, "неожиданный ответ на остатки FBS (нет products)")
            rows.extend(page)
            if data.get("has_next") is False:
                break
            next_cursor = str(data.get("cursor") or "")
            if not next_cursor:
                if data.get("has_next"):
                    raise OzonApiError(None, "Ozon FBS: следующая страница без cursor")
                break
            if next_cursor in seen or not page:
                raise OzonApiError(None, "Ozon FBS: повтор cursor или пустая промежуточная страница")
            seen.add(next_cursor)
            cursor = next_cursor
    return rows


def get_stock_analytics(client_id: str, api_key: str, skus: list[int]) -> list[dict]:

    if not skus:
        return []

    rows: list[dict] = []
    chunk_size = OZON_STOCK_ANALYTICS.sku_batch_size

    for start_idx in range(0, len(skus), chunk_size):
        chunk = [str(s) for s in skus[start_idx : start_idx + chunk_size]]
        data = _request(OZON_STOCK_ANALYTICS.path, client_id, api_key, {"skus": chunk})

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
    seen_cursors: set[str] = set()
    seen_products: set[str] = set()

    while True:
        data = _request(
            "/v4/product/info/stocks",
            client_id,
            api_key,
            {"cursor": cursor, "limit": PAGE_SIZE, "filter": {"visibility": "ALL"}},
        )
        page = data.get("items")
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            raise OzonApiError(None, "неожиданный ответ на остатки товаров (нет items)")
        for row in page:
            identity = str(row.get("product_id") or "")
            if not identity or identity in seen_products:
                raise OzonApiError(None, "Ozon вернул повторный товар или товар без product_id")
            seen_products.add(identity)
        items.extend(page)
        cursor = data.get("cursor") or ""
        total = data.get("total_items", data.get("total"))
        if total is not None and len(items) >= int(total):
            return items
        if not cursor:
            if total is not None and len(items) < int(total):
                raise OzonApiError(None, "Ozon вернул неполный список товаров")
            return items
        if not isinstance(cursor, str) or cursor in seen_cursors or not page or len(items) > 500_000:
            raise OzonApiError(None, "Ozon вернул некорректную пагинацию остатков товаров")
        seen_cursors.add(cursor)


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


def get_reviews(client_id: str, api_key: str) -> list[dict]:
    rows: list[dict] = []
    cursor = ""
    seen: set[str] = set()
    while True:
        data = _request(
            "/v2/review/list",
            client_id,
            api_key,
            {"last_id": cursor, "limit": 100, "sort_dir": "DESC"},
        )
        page = data.get("reviews")
        if not isinstance(page, list):
            raise OzonApiError(None, "неожиданный ответ Ozon на список отзывов")
        rows.extend(page)
        if not data.get("has_next"):
            return rows
        next_cursor = str(data.get("last_id") or "")
        if not next_cursor or next_cursor == cursor or next_cursor in seen:
            raise OzonApiError(None, "Ozon не вернул новый курсор списка отзывов")
        seen.add(next_cursor)
        cursor = next_cursor


def _get_postings(path: str, client_id: str, api_key: str, since: str, to: str) -> list[dict]:

    rows: list[dict] = []
    offset = 0

    while True:
        data = _request(
            path,
            client_id,
            api_key,
            {
                "dir": SortDirection.ASC.value,
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
            raise OzonApiError(
                None,
                f"неожиданный формат отправлений Ozon: {result!r}"[: OZON_API.legacy_response_error_length],
            )

        rows.extend(page)
        has_next = result.get("has_next") if isinstance(result, dict) else None
        if not page or has_next is False or len(page) < PAGE_SIZE:
            return rows

        offset += len(page)
        if offset > OZON_API.max_postings:
            logger.warning("%sOzon: прервали обход отправлений %s на offset=%s", _store_label(), path, offset)
            return rows


def get_fbo_postings(client_id: str, api_key: str, since: str, to: str) -> list[dict]:
    return _get_postings(OZON_API.fbo_postings_path, client_id, api_key, since, to)


def get_fbs_postings(client_id: str, api_key: str, since: str, to: str) -> list[dict]:
    return _get_postings(OZON_API.fbs_postings_v3_path, client_id, api_key, since, to)


def get_fbs_postings_v4(client_id: str, api_key: str, since: str, to: str) -> list[dict]:
    rows: list[dict] = []
    cursor = ""
    seen_cursors: set[str] = set()
    seen_postings: set[str] = set()

    while True:
        payload = {
            "dir": SortDirection.ASC.value,
            "filter": {"since": since, "to": to},
            "limit": FBS_POSTINGS_V4_PAGE_SIZE,
            "with": {
                "analytics_data": False,
                "barcodes": False,
                "financial_data": False,
                "translit": False,
            },
        }
        if cursor:
            payload["cursor"] = cursor

        data = _request(OZON_API.fbs_postings_v4_path, client_id, api_key, payload)
        result = data.get("result") if isinstance(data.get("result"), dict) else data
        page = result.get("postings") if isinstance(result, dict) else None
        if page is None and isinstance(result, dict):
            page = result.get("items")
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            raise OzonApiError(None, "Ozon v4 не вернул список отправлений")
        has_next = result.get("has_next")
        if not isinstance(has_next, bool):
            raise OzonApiError(None, "Ozon v4 не подтвердил завершённость списка отправлений")
        for row in page:
            posting_number = str(row.get("posting_number") or "")
            if not posting_number or posting_number in seen_postings:
                raise OzonApiError(None, "Ozon v4 вернул отправление без номера или повторил отправление")
            seen_postings.add(posting_number)

        rows.extend(page)
        if not has_next:
            return rows
        if not page:
            raise OzonApiError(None, "Ozon v4 вернул пустую промежуточную страницу отправлений")
        if len(rows) > OZON_API.max_postings:
            raise OzonApiError(None, "Превышен предел обхода отправлений Ozon v4; выгрузка неполна")

        next_cursor = str(result.get("cursor") or "").strip()
        if not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
            raise OzonApiError(None, "Ozon v4 сообщил о следующей странице без нового cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


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
