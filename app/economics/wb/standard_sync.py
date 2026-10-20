"""Load a rolling completed week through WB API; publish only a complete snapshot."""

import logging
from datetime import UTC, datetime, timedelta
from time import sleep

from app.core.domain import MOSCOW_TIMEZONE
from app.economics.wb.standard_costs import build
from app.jobs import settings
from app.repositories import wb_standard_costs
from app.wb import api, tokens

JOB = "wb_standard_costs_sync"
INTERVAL_SECONDS = 4 * 60 * 60
WEEK_DAYS = 7
FINANCE_PAGE_SIZE = 100000
FINANCE_MAX_PAGES = 1000
CARD_PAGE_SIZE = 100
CARD_MAX_PAGES = 10000
STORAGE_POLL_ATTEMPTS = 30
STORAGE_POLL_SECONDS = 5
FINANCE = "https://finance-api.wildberries.ru/api/finance/v1/sales-reports/detailed"
COMMON = "https://common-api.wildberries.ru/api/v1/tariffs"
STORAGE = api.ANALYTICS_BASE + "/api/v1/paid_storage"
FIELDS = [
    "rrdId",
    "nmId",
    "title",
    "vendorCode",
    "subjectName",
    "quantity",
    "docTypeName",
    "sellerOperName",
    "retailPriceWithDisc",
    "officeName",
    "deliveryAmount",
    "returnAmount",
    "saleDt",
    "rrDate",
    "currency",
    "giBoxTypeName",
    "deliveryMethod",
]
logger = logging.getLogger(__name__)


def completed_week(now=None):
    now = now or datetime.now(MOSCOW_TIMEZONE)
    end = now.astimezone(MOSCOW_TIMEZONE).date() - timedelta(days=1)
    return end - timedelta(days=WEEK_DAYS - 1), end


def _rows(value, source):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Некорректный ответ WB: {source}")
    return value


def _finance(token, start, end):
    result, seen, cursor = [], set(), 0
    for _ in range(FINANCE_MAX_PAGES):
        page = api.request(
            "POST",
            FINANCE,
            token,
            json_body=dict(
                dateFrom=start.isoformat(),
                dateTo=end.isoformat(),
                period="daily",
                rrdId=cursor,
                limit=FINANCE_PAGE_SIZE,
                fields=FIELDS,
            ),
        )
        # WB can return HTTP 204 for the end of the report.
        if page is None:
            return result
        page = _rows(page, "реализация")
        if not page:
            return result
        ids = [int(row["rrdId"]) for row in page]
        if len(set(ids)) != len(ids) or seen.intersection(ids) or min(ids) <= cursor:
            raise ValueError("Повтор или остановка курсора реализации WB")
        seen.update(ids)
        result.extend(page)
        cursor = max(ids)
    raise ValueError("Превышен предел страниц реализации WB")


def _cards(token):
    result, seen, cursor = [], set(), {"limit": CARD_PAGE_SIZE}
    for _ in range(CARD_MAX_PAGES):
        response = api.request(
            "POST",
            api.CONTENT_BASE + "/content/v2/get/cards/list",
            token,
            json_body={"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}},
        )
        page = _rows(response["cards"], "карточки")
        next_cursor = response["cursor"]
        for card in page:
            key = card["nmID"]
            if key in seen:
                raise ValueError("Повтор страницы карточек WB")
            seen.add(key)
        result.extend(page)
        if int(next_cursor["total"]) < CARD_PAGE_SIZE:
            return result
        updated, nm = next_cursor.get("updatedAt"), next_cursor.get("nmID")
        if (
            not page
            or not updated
            or not nm
            or (updated, nm) == (cursor.get("updatedAt"), cursor.get("nmID"))
        ):
            raise ValueError("Неполная пагинация карточек WB")
        cursor = {"limit": CARD_PAGE_SIZE, "updatedAt": updated, "nmID": nm}
    raise ValueError("Превышен предел страниц карточек WB")


def _storage(token, start, end):
    task = api.request(
        "GET", STORAGE, token, params={"dateFrom": start.isoformat(), "dateTo": end.isoformat()}
    )
    task_id = task["data"]["taskId"]
    for _ in range(STORAGE_POLL_ATTEMPTS):
        state = api.request("GET", f"{STORAGE}/tasks/{task_id}/status", token)["data"]["status"]
        if state == "done":
            return _rows(api.request("GET", f"{STORAGE}/tasks/{task_id}/download", token), "хранение")
        if state not in ("new", "processing"):
            raise ValueError(f"Задача хранения WB завершилась со статусом {state}")
        sleep(STORAGE_POLL_SECONDS)
    raise TimeoutError("WB не подготовил хранение за отведённое время")


def fetch_sources(token, start, end):
    """Collect a complete source bundle without changing database state."""
    finance = _finance(token, start, end)
    storage = _storage(token, start, end)
    cards = _cards(token)
    tariffs = {}
    for offset in range(WEEK_DAYS):
        day = (start + timedelta(days=offset)).isoformat()
        value = api.request("GET", COMMON + "/box", token, params={"date": day})
        _rows(value["response"]["data"]["warehouseList"], "тарифы")
        tariffs[day] = value
    commissions = api.request("GET", COMMON + "/commission", token, params={"locale": "ru"})
    _rows(commissions["report"], "комиссии")
    timestamp = datetime.now(UTC)
    raw = dict(
        date_from=start.isoformat(),
        date_to=end.isoformat(),
        updated_at=timestamp.isoformat(),
        commission_date=timestamp.astimezone(MOSCOW_TIMEZONE).date().isoformat(),
        finance=finance,
        storage=storage,
        cards=cards,
        tariffs=tariffs,
        commissions=commissions,
        sources=[
            FINANCE,
            STORAGE,
            COMMON + "/box",
            COMMON + "/commission",
            api.CONTENT_BASE + "/content/v2/get/cards/list",
        ],
    )
    return raw


def sync_store(store, *, now=None):
    """Publish sources and calculated report together after successful validation."""
    start, end = completed_week(now)
    raw = fetch_sources(tokens.get_token(store), start, end)
    report = build(raw)
    # No DB writes until all API pages and calculation validation have succeeded.
    wb_standard_costs.save(store, report, raw=raw)
    return dict(ok=True, date_from=raw["date_from"], date_to=raw["date_to"], rows=len(report["rows"]))


def sync_all():
    result = {}
    for store in settings.enabled_stores(JOB, "WB"):
        try:
            if not tokens.has_token(store):
                result[store] = dict(ok=True, skipped=True, reason="Нет токена WB")
                continue
            result[store] = sync_store(store)
        except Exception as error:
            logger.exception("wb_standard_costs_sync_failed store=%s", store)
            result[store] = dict(ok=False, error=str(error))
    return result
