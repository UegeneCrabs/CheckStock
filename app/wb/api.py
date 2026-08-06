"""
Тонкий клиент к API Wildberries для остатков FBS и FBO.

Использует только стандартную библиотеку (urllib) — без сторонних
пакетов вроде requests/httpx, чтобы не требовать pip install в venv.

Документация (см. dev.wildberries.ru/openapi):
- FBS (склад продавца), категория токена "Маркетплейс":
    GET  /api/v3/warehouses            — список своих складов
    POST /api/v3/stocks/{warehouseId}  — остатки по баркодам на складе
    база: https://marketplace-api.wildberries.ru

- FBO (склады WB), категория токена "Аналитика":
    Старый метод GET /api/v1/supplier/stocks отключён Wildberries
    (see release notes id=494) — заменён отчётом "Остатки на складах WB":
        GET /api/v1/warehouse_remains                              — создать задачу
        GET /api/v1/warehouse_remains/tasks/{task_id}/status        — статус
        GET /api/v1/warehouse_remains/tasks/{task_id}/download      — скачать отчёт
    база: https://seller-analytics-api.wildberries.ru
    Отчёт возвращает по каждому баркоду список складов с остатками —
    то, что нужно для детализации FBO по складам.

- Заказы, категория токена "Статистика":
    GET /api/v1/supplier/orders — заказы с указанной даты
    база: https://statistics-api.wildberries.ru

- Каталог карточек, категория токена "Контент":
    POST /content/v2/get/cards/list  — карточки постранично, по курсору
    база: https://content-api.wildberries.ru
"""

import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("checkstock.wb_api")

FBS_BASE = "https://marketplace-api.wildberries.ru"
ANALYTICS_BASE = "https://seller-analytics-api.wildberries.ru"
CONTENT_BASE = "https://content-api.wildberries.ru"
STATISTICS_BASE = "https://statistics-api.wildberries.ru"

# Сколько карточек просить за раз. 100 — потолок метода.
CARDS_PAGE_LIMIT = 100

# Сколько штрихкодов помещается в один запрос остатков FBS. Потолок WB — 1000,
# при превышении метод отвечает 400 «неверные параметры», не уточняя, чем
# именно они неверны. До выгрузки каталога по API у магазинов было меньше
# тысячи позиций, поэтому ограничение и не проявлялось.
FBS_SKUS_PER_REQUEST = 1000

REQUEST_TIMEOUT = 30

# Псевдо-"склады" в ответе отчёта warehouse_remains — это не физические склады,
# а служебные агрегаты (товар в пути, итоговая сумма и т.п.). Их нельзя
# складывать вместе с реальными складами — иначе остаток задвоится.
NON_WAREHOUSE_LABELS = {
    "В пути до получателей",
    "В пути возвраты на склад WB",
    "Всего находится на складах",
}

# Сколько раз повторить запрос при 429 (слишком много запросов) и с какой паузой
# по умолчанию (если WB не прислал точную подсказку в заголовках)
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_BACKOFF_SECONDS = 5

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
    """Ошибка обращения к WB API с человекочитаемым сообщением в .friendly."""

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
    """Сколько ждать перед повтором после 429 — берём точную подсказку WB из
    заголовков (X-Ratelimit-Retry/X-Ratelimit-Reset), если она есть, иначе —
    свой запасной вариант с нарастающей паузой."""
    headers = getattr(http_error, "headers", None)
    if headers:
        for key in ("X-Ratelimit-Retry", "X-Ratelimit-Reset", "Retry-After"):
            val = headers.get(key)
            if val:
                try:
                    return max(float(val), 0.5)
                except (TypeError, ValueError):
                    pass
    return RATE_LIMIT_BACKOFF_SECONDS * attempt


def _request(method: str, url: str, token: str, params: dict | None = None, json_body=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    data = None
    headers = {"Authorization": token}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    attempts = RATE_LIMIT_RETRIES + 1
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw_body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            title, detail = _parse_error_body(raw)
            if e.code == 429 and attempt < attempts:
                wait = _retry_after_seconds(e, attempt)
                logger.warning("WB 429 (попытка %s/%s), ждём %.1fс: %s", attempt, attempts, wait, url)
                time.sleep(wait)
                continue
            raise WBApiError(e.code, title, detail) from e
        except (socket.timeout, TimeoutError) as e:
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


def _expect(value, *keys, context: str):
    """Достаёт вложенные ключи из ответа WB и превращает KeyError/TypeError
    (неожиданная форма ответа) в понятную WBApiError вместо сырого traceback."""
    cur = value
    try:
        for key in keys:
            cur = cur[key]
        return cur
    except (KeyError, TypeError, IndexError) as e:
        raise WBApiError(
            None, detail=f"неожиданный формат ответа от WB ({context}): {value!r}"[:300]
        ) from e


def get_own_warehouses(token: str) -> list[dict]:
    """Список складов продавца (нужен warehouseId для остатков FBS).
    Требует токен с категорией 'Маркетплейс'."""
    data = _request("GET", f"{FBS_BASE}/api/v3/warehouses", token)
    if data is None:
        return []
    if not isinstance(data, list):
        raise WBApiError(None, detail=f"неожиданный формат ответа от WB (список складов): {data!r}"[:300])
    return data


def get_fbs_stock(token: str, warehouse_id: int, barcodes: list[str]) -> dict[str, int]:
    """Остатки FBS по списку баркодов на складе продавца. Возвращает {barcode: quantity}.

    Список режем на части по FBS_SKUS_PER_REQUEST: у метода есть потолок, а
    каталог магазина его перерастает. Пустые и повторяющиеся баркоды убираем —
    WB считает такой список некорректным целиком и не отвечает вообще ничего,
    то есть один мусорный элемент стоил бы остатков по всему складу.
    """
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
        chunk = unique[start:start + FBS_SKUS_PER_REQUEST]
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
        except (KeyError, TypeError) as e:
            raise WBApiError(
                None, detail=f"неожиданный формат остатков FBS: {stocks!r}"[:300]
            ) from e

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
    data = _request(
        "GET", f"{ANALYTICS_BASE}/api/v1/warehouse_remains/tasks/{task_id}/status", token
    )
    return _expect(data, "data", "status", context="статус отчёта FBO")


def _download_warehouse_remains(token: str, task_id: str) -> list[dict]:
    return (
        _request("GET", f"{ANALYTICS_BASE}/api/v1/warehouse_remains/tasks/{task_id}/download", token)
        or []
    )


def get_fbo_stock_by_warehouse(
    token: str, poll_interval: float = 5.0, max_wait: float = 120.0
) -> dict[tuple[str, str], int]:
    """
    Остатки FBO по баркоду и складу WB: {(barcode, warehouseName): quantity}.
    Через отчёт "Остатки на складах WB": создать задачу -> дождаться готовности -> скачать.
    Требует токен с категорией 'Аналитика'.
    """
    task_id = _create_warehouse_remains_task(token)

    waited = 0.0
    status = "processing"
    while status not in ("done", "error", "purged", "canceled") and waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval
        status = _get_warehouse_remains_status(token, task_id)

    if status != "done":
        raise WBApiError(
            None, detail=f"отчёт по остаткам не собрался за {max_wait:.0f} с (статус: {status})"
        )

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


# ----------------------------------------------------------------------
# Каталог карточек
# ----------------------------------------------------------------------

def get_cards_list(token: str, page_limit: int = CARDS_PAGE_LIMIT) -> list[dict]:
    """Все карточки продавца. Требует токен с категорией 'Контент'.

    Метод страничный и курсорный: в ответе приходит cursor с updatedAt и nmID
    последней карточки, их же нужно отправить в следующем запросе. Признак
    конца — cursor.total меньше запрошенного лимита; по нему и останавливаемся,
    а не по «пришло пусто», иначе последняя неполная страница стоила бы лишнего
    запроса на каждой синхронизации.

    Фильтр withPhoto = -1 означает «любые»: карточка без фото — обычная
    карточка, и терять её остаток было бы нечем оправдать.
    """
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
            # Страховка от зацикливания: если WB вернёт ту же страницу снова
            # (курсор не сдвинулся), мы иначе крутились бы вечно.
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
            # без курсора следующая страница будет той же самой
            return cards


def normalize_card(card: dict) -> dict:
    """Карточка WB -> плоский вид для каталога.

    sizes у WB это размеры одной карточки, и у КАЖДОГО свой баркод (skus).
    Для одежды это разные физически товары на складе, поэтому размеры
    возвращаем списком, а не схлопываем в один баркод: остатки считаются
    по баркоду, и потерянный размер означал бы потерянный остаток.
    """
    sizes = []
    for size in card.get("sizes") or []:
        skus = [str(sku).strip() for sku in (size.get("skus") or []) if str(sku).strip()]
        if not skus:
            continue
        sizes.append({
            "tech_size": str(size.get("techSize") or "").strip(),
            "barcode": skus[0],
            "extra_barcodes": skus[1:],
        })

    return {
        "nm_id": str(card.get("nmID") or "").strip(),
        "vendor_code": str(card.get("vendorCode") or "").strip(),
        "title": str(card.get("title") or "").strip(),
        # когда карточку последний раз меняли в кабинете WB
        "updated_at": str(card.get("updatedAt") or "").strip(),
        "sizes": sizes,
    }


# ----------------------------------------------------------------------
# Заказы (статистика)
# ----------------------------------------------------------------------

# Сколько строк WB отдаёт за один запрос заказов. Если пришло ровно столько,
# значит это не весь ответ и надо просить продолжение.
ORDERS_PAGE_SIZE = 80000

# Ограничение метода — один запрос в минуту. Постранично ходим редко (три
# недели заказов в одну страницу помещаются с запасом), но если понадобится,
# пауза обязательна, иначе WB ответит 429 и следующая страница потеряется.
ORDERS_PAGE_PAUSE_SECONDS = 61


def get_orders(token: str, date_from: str, max_pages: int = 10) -> list[dict]:
    """Заказы начиная с date_from (формат «2026-07-16T00:00:00»).

    Требует токен с категорией 'Статистика'.

    flag=0 означает «всё, что изменилось с этой даты», а не «создано с этой
    даты»: в выборку попадут и старые заказы, у которых поменялся статус.
    Отсекать по дате создания приходится уже у себя — для отчёта важно, когда
    заказ сделали, а не когда его последний раз трогали.

    Постраничность у метода своя: следующий кусок просят, подставив самую
    позднюю lastChangeDate из предыдущего ответа.
    """
    orders: list[dict] = []
    cursor = date_from
    seen_srids: set[str] = set()

    for page in range(max_pages):
        if page:
            time.sleep(ORDERS_PAGE_PAUSE_SECONDS)

        rows = _request(
            "GET",
            f"{STATISTICS_BASE}/api/v1/supplier/orders",
            token,
            params={"dateFrom": cursor, "flag": 0},
        ) or []

        if not isinstance(rows, list):
            raise WBApiError(None, detail=f"неожиданный формат заказов WB: {rows!r}"[:300])

        fresh = []
        for row in rows:
            # srid — идентификатор заказа. Страницы у WB перекрываются по
            # секунде, и без этого пограничные заказы посчитались бы дважды.
            srid = str(row.get("srid") or "")
            if srid and srid in seen_srids:
                continue
            if srid:
                seen_srids.add(srid)
            fresh.append(row)

        orders.extend(fresh)

        if len(rows) < ORDERS_PAGE_SIZE:
            return orders

        next_cursor = max((str(r.get("lastChangeDate") or "") for r in rows), default="")
        if not next_cursor or next_cursor == cursor:
            return orders
        cursor = next_cursor

    logger.warning("WB: заказы оборваны на %s страницах — данных больше, чем ожидалось",
                   max_pages)
    return orders
