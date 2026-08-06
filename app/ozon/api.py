"""
Клиент Ozon Seller API — только чтение остатков и складов.

Как и в WB-клиенте, работаем стандартным urllib, без новых зависимостей.
Отличия от WB, заложенные здесь:

- авторизация парой заголовков Client-Id + Api-Key вместо JWT;
- FBO не требует задачного отчёта, как в WB: /v2/analytics/stock_on_warehouses
  отдаёт данные синхронно, страницами. Но покрывает он ТОЛЬКО FBO;
- остатков FBS/rFBS по складам продавца сейчас взять неоткуда: старый метод
  /v1/product/info/stocks-by-warehouse/fbs Ozon отключил, а /v1/analytics/stocks
  вопреки названию отдаёт всё те же склады Ozon (FBO). Пока все проверенные
  магазины работают только по FBO, так что это не блокер;
- склад приходит прямо в строке остатка (warehouse_name), поэтому отдельно
  сопоставлять строки со списком складов не нужно;
- сопоставление с каталогом идёт по item_code — это артикул продавца,
  тот же, что у нас в stock_items (в WB приходилось матчить по баркоду).
"""

import json
import logging
import random
import socket
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger("checkstock.ozon_api")

# Какой магазин сейчас выгружается. Нужно для логов: запросы к Ozon идут из
# нескольких потоков параллельно, и без этой пометки в логе видно только
# «сработал лимит», но не понять, у какого кабинета проблема.
#
# threading.local, а не глобальная переменная: каждый магазин синхронизируется
# в своём потоке, и метки не должны перетирать друг друга.
_current = threading.local()


def set_store_context(label: str) -> None:
    """Помечает текущий поток именем магазина — попадёт во все логи Ozon."""
    _current.store = label


def clear_store_context() -> None:
    _current.store = ""


def _store_label() -> str:
    label = getattr(_current, "store", "")
    return f"[{label}] " if label else ""

BASE_URL = "https://api-seller.ozon.ru"
REQUEST_TIMEOUT = 60

# Аналитические методы Ozon лимитированы жёстче обычных, поэтому запас
# по паузам между повторами больше, чем в WB-клиенте.
MAX_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = 5

# Для некоторых методов упорствовать не стоит. /v1/analytics/stocks даёт
# только названия кластеров — украшение, которое лежит у нас в кэше, — а
# отвечает он то 429 (в теле {"code":8}, RESOURCE_EXHAUSTED), то 500 с
# {"code":2} — это UNKNOWN, то есть «у Ozon сломалось внутри и объяснить
# нечем». Пять повторов такого ответа только засоряют лог и тратят время
# синхронизации: вызывающий код и так умеет работать по сохранённым данным.
PATH_MAX_ATTEMPTS = {
    "/v1/analytics/stocks": 2,
}

# Стартовый интервал между запросами к «узким» методам, секунды.
#
# У /v1/analytics/stocks лимит считается в запросах в секунду, и повторы его
# не лечат: мы просто упираемся в него снова. Лечит только пауза ПЕРЕД
# запросом. Точного числа Ozon для этого метода не публикует, поэтому
# интервал не константа, а подстраивается по ответам (см. _note_rate_limit):
# на каждый 429 увеличиваем, при спокойной серии постепенно возвращаем назад.
# Так мы сходимся к реальному лимиту, не завися от недокументированного числа.
#
# Интервал глобальный на процесс, а не на магазин: лимит у Ozon на аккаунт,
# и два кабинета, синхронизируясь параллельно, складывают нагрузку друг другу.
THROTTLED_PATHS = {
    "/v1/analytics/stocks": 1.5,
}

# Границы, за которые подстройка не выходит.
THROTTLE_MAX_INTERVAL = 20.0
THROTTLE_GROWTH = 2.0
# Сколько успешных запросов подряд нужно, чтобы осторожно ускориться обратно
THROTTLE_RELAX_AFTER = 5
THROTTLE_RELAX_FACTOR = 0.8

_throttle_lock = threading.Lock()
_last_call_at: dict[str, float] = {}
_interval: dict[str, float] = {}
_calm_streak: dict[str, int] = {}


def _throttle(path: str) -> None:
    """Выдерживает паузу перед запросом к лимитированному методу."""
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
    """Метод упёрся в лимит — замедляемся. Возвращает новый интервал."""
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
            _store_label(), path, updated,
        )
    return updated


def _note_success(path: str) -> None:
    """Серия удачных запросов — можно осторожно ускоряться обратно,
    иначе после одного случайного 429 мы бы навсегда остались медленными."""
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
    """Ozon может подсказать, через сколько повторить. Если подсказал —
    слушаем его, а не свою формулу."""
    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return None


def _backoff_pause(attempt: int) -> float:
    """Растущая пауза со случайной добавкой.

    Добавка нужна, когда в лимит упёрлись сразу несколько потоков: без неё
    они отсчитают одинаковую паузу и синхронно ударят по API повторно.
    """
    return RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 1.5)

# Сколько строк просим за раз. Максимум у метода — 1000.
PAGE_SIZE = 1000

# Схемы работы Ozon. rFBS — склад продавца с доставкой силами Ozon;
# отличается от обычного FBS флагом is_rfbs в списке складов.
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
    """Ошибка обращения к Ozon с понятным пользователю текстом."""

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


# Ozon отдаёт в теле ошибки gRPC-код. Расшифровываем те, что реально
# встречаются, иначе в логе остаётся бесполезное {"code":2}.
_GRPC_CODES = {
    2: "внутренний сбой на стороне Ozon",
    4: "Ozon не уложился в свой таймаут",
    8: "исчерпан лимит запросов",
    13: "внутренняя ошибка Ozon",
    14: "сервис Ozon временно недоступен",
}


def _parse_error_body(raw: str) -> str:
    """Ozon кладёт описание ошибки в поле message, а иногда только код."""
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
    """POST-запрос к Ozon с повторами на временных ошибках."""
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

            # 429 и 5xx — временные, есть смысл повторить
            if e.code == 429 or 500 <= e.code < 600:
                if e.code == 429:
                    # заодно замедляем последующие запросы к этому методу,
                    # иначе повтор упрётся в тот же лимит
                    _note_rate_limit(path)

                if attempt < max_attempts:
                    pause = _retry_after(getattr(e, "headers", None)) or _backoff_pause(attempt)
                    logger.warning(
                        "%sOzon %s: %s, повтор через %.1f с",
                        _store_label(), path, last_error.friendly, pause,
                    )
                    time.sleep(pause)
                    continue
            raise last_error

        except (socket.timeout, TimeoutError) as e:
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


def get_own_warehouses(client_id: str, api_key: str) -> list[dict]:
    """Склады продавца — FBS и rFBS.

    Метод /v1/warehouse/list отключён Ozon 7 апреля 2026, работает только
    /v2/warehouse/list. Он страничный: ходим по курсору, пока has_next.

    В ответе, помимо имени и id, есть warehouse_type, is_express и is_rfbs —
    по последнему отличаем rFBS от обычного FBS.
    """
    warehouses: list[dict] = []
    cursor = ""

    while True:
        payload = {"limit": 100}
        if cursor:
            payload["cursor"] = cursor

        data = _request("/v2/warehouse/list", client_id, api_key, payload)

        # у v2 список лежит в warehouses, но подстрахуемся на случай result
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
    """Остатки FBO по складам Ozon.

    Метод отдаёт только FBO: склады здесь принадлежат Ozon (все с суффиксом
    _РФЦ / _МРФЦ). Параметр warehouse_type у него означает не схему работы,
    а тип склада (экспресс / обычный), поэтому фильтровать им FBS/FBO нельзя —
    на реальных данных это давало одинаковую выдачу для обоих значений.

    Строка: sku, item_code (артикул продавца), item_name, warehouse_name,
    free_to_sell_amount, reserved_amount, promised_amount.
    """
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
            logger.warning("%sOzon: прервали обход остатков FBO на offset=%s",
                           _store_label(), offset)
            return rows


def get_stock_analytics(client_id: str, api_key: str, skus: list[int]) -> list[dict]:
    """Расширенная аналитика остатков FBO по складам и кластерам Ozon.

    В обычной синхронизации НЕ используется: метод жёстко лимитирован, а
    единственное, что мы из него брали, — названия кластеров, и они теперь
    лежат в нашей таблице. Остаётся для диагностических скриптов.

    ВАЖНО: это НЕ остатки складов продавца, хотя так можно подумать по
    названию. На реальных данных метод вернул те же склады _РФЦ, что и
    /v2/analytics/stock_on_warehouses, плюс кластеры и оборачиваемость.
    Остатки FBS/rFBS сюда не попадают.

    Ценность метода в дополнительных полях, которых нет в обычном отчёте:
    available_stock_count (доступно), transit_stock_count (в пути),
    expiring_stock_count (истекает срок), excess_stock_count (излишки),
    return_from_customer_stock_count (возвраты), cluster_name, placement_zone.

    Лимит запросов здесь жёсткий: на 147 SKU уже прилетал 429, поэтому
    ходим пачками и полагаемся на повторы из _request.
    """
    if not skus:
        return []

    rows: list[dict] = []
    CHUNK = 100

    for start_idx in range(0, len(skus), CHUNK):
        chunk = [str(s) for s in skus[start_idx:start_idx + CHUNK]]
        data = _request("/v1/analytics/stocks", client_id, api_key, {"skus": chunk})

        page = data.get("items")
        if page is None:
            page = (data.get("result") or {}).get("items")
        if page is None:
            raise OzonApiError(
                None, f"неожиданный ответ на аналитику остатков (ключи: {sorted(data)[:6]})"
            )
        rows.extend(page)

    return rows


def normalize_analytics_row(row: dict) -> dict:
    """Приводит строку аналитики к тому же виду, что и обычный отчёт по складам.

    available_stock_count — то, что реально доступно к продаже; valid_stock_count
    считает и то, что лежит с ограничениями, поэтому берём именно available.
    """
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
    """Тоталы по товарам в разрезе схем: fbo, fbs, rfbs.

    Здесь же берём SKU для запроса остатков FBS по складам.
    """
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


# --- Каталог товаров -------------------------------------------------------
#
# Остатки отвечают на вопрос «сколько», каталог — «что это за товар».
# Нужны оба: у одного нашего товара на Ozon может быть несколько карточек,
# у каждой свой артикул продавца, свой SKU и свой баркод.
#
# Забирается в два шага, как устроено у Ozon:
#   1. /v3/product/list — список всех карточек, отдаёт только id и offer_id;
#   2. /v3/product/info/list — подробности пачками: название, баркоды, SKU.

# У info/list лимит 1000 идентификаторов за запрос.
INFO_CHUNK = 1000


def get_product_list(client_id: str, api_key: str) -> list[dict]:
    """Все карточки товаров кабинета: product_id + offer_id.

    Пагинация здесь не курсорная, а по last_id: передаём id последней
    полученной карточки. Признак конца — пустая страница либо страница
    короче запрошенного лимита.

    visibility=ALL, потому что нам нужны и архивные карточки: по ним может
    оставаться остаток, который иначе потеряется.
    """
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
            raise OzonApiError(
                None, f"неожиданный ответ на список товаров (ключи: {sorted(result)[:6]})"
            )

        items.extend(page)
        last_id = result.get("last_id") or ""

        if not page or not last_id or len(page) < PAGE_SIZE:
            return items

        if len(items) > 200_000:
            logger.warning("%sOzon: прервали обход каталога на %s карточках",
                           _store_label(), len(items))
            return items


def get_product_info(client_id: str, api_key: str, product_ids: list[int]) -> list[dict]:
    """Подробности по карточкам: название, баркоды, SKU.

    Ходим пачками по INFO_CHUNK. Пустой список на входе — пустой на выходе,
    чтобы вызывающий код не проверял это отдельно.
    """
    if not product_ids:
        return []

    items: list[dict] = []

    for start in range(0, len(product_ids), INFO_CHUNK):
        chunk = [int(pid) for pid in product_ids[start:start + INFO_CHUNK]]
        data = _request(
            "/v3/product/info/list",
            client_id,
            api_key,
            {"product_id": chunk, "offer_id": [], "sku": []},
        )
        result = data.get("result") or data
        page = result.get("items")
        if page is None:
            raise OzonApiError(
                None, f"неожиданный ответ на карточки товаров (ключи: {sorted(result)[:6]})"
            )
        items.extend(page)

    return items


def normalize_product(row: dict) -> dict:
    """Приводит карточку к плоскому виду, независимо от версии ответа.

    Ozon за время жизни API менял, где лежит SKU: раньше он был в sources[]
    с разбивкой по схемам, сейчас чаще приходит полем sku. Баркод так же:
    одиночный barcode у старых карточек, массив barcodes у новых. Разбираем
    оба варианта, чтобы не переписывать это при следующем изменении.
    """
    barcodes: list[str] = []
    raw_barcodes = row.get("barcodes")
    if isinstance(raw_barcodes, list):
        barcodes = [str(b).strip() for b in raw_barcodes if str(b or "").strip()]
    single = str(row.get("barcode") or "").strip()
    if single and single not in barcodes:
        barcodes.insert(0, single)

    sku = row.get("sku")
    if not sku:
        # старый формат: SKU лежит в источниках, по одному на схему
        for source in row.get("sources") or []:
            if source.get("sku"):
                sku = source["sku"]
                break

    try:
        sku_value = int(sku) if sku else None
    except (TypeError, ValueError):
        sku_value = None

    return {
        "product_id": row.get("id") or row.get("product_id"),
        "offer_id": str(row.get("offer_id") or "").strip(),
        "sku": sku_value,
        "name": str(row.get("name") or "").strip(),
        "barcodes": barcodes,
        "archived": bool(row.get("is_archived") or row.get("archived")),
        # когда карточку последний раз меняли в кабинете Ozon
        "updated_at": str(row.get("updated_at") or "").strip(),
    }
