"""WB API pagination and source collection for the conditions comparison."""

from datetime import UTC, date, datetime, timedelta
from time import sleep

from pydantic import JsonValue

from app.config import settings
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.wb_standard_costs import SourceBundle
from app.wb import api

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


def _rows(value: object, source: str) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Некорректный ответ WB: {source}")
    return value


def _finance(token: str, start: date, end: date) -> list[dict[str, JsonValue]]:
    result, seen, cursor = [], set(), 0
    for _ in range(settings.wb_standard_costs.finance_max_pages):
        page = api.request(
            "POST",
            FINANCE,
            token,
            json_body=dict(
                dateFrom=start.isoformat(),
                dateTo=end.isoformat(),
                period="daily",
                rrdId=cursor,
                limit=settings.wb_standard_costs.finance_page_size,
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


def _cards(token: str) -> list[dict[str, JsonValue]]:
    result, seen, cursor = [], set(), {"limit": settings.wb_standard_costs.card_page_size}
    for _ in range(settings.wb_standard_costs.card_max_pages):
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
        if int(next_cursor["total"]) < settings.wb_standard_costs.card_page_size:
            return result
        updated, nm = next_cursor.get("updatedAt"), next_cursor.get("nmID")
        if (
            not page
            or not updated
            or not nm
            or (updated, nm) == (cursor.get("updatedAt"), cursor.get("nmID"))
        ):
            raise ValueError("Неполная пагинация карточек WB")
        cursor = {"limit": settings.wb_standard_costs.card_page_size, "updatedAt": updated, "nmID": nm}
    raise ValueError("Превышен предел страниц карточек WB")


def _storage(token: str, start: date, end: date) -> list[dict[str, JsonValue]]:
    task = api.request(
        "GET", STORAGE, token, params={"dateFrom": start.isoformat(), "dateTo": end.isoformat()}
    )
    task_id = task["data"]["taskId"]
    for _ in range(settings.wb_standard_costs.storage_poll_attempts):
        state = api.request("GET", f"{STORAGE}/tasks/{task_id}/status", token)["data"]["status"]
        if state == "done":
            return _rows(api.request("GET", f"{STORAGE}/tasks/{task_id}/download", token), "хранение")
        if state not in ("new", "processing"):
            raise ValueError(f"Задача хранения WB завершилась со статусом {state}")
        sleep(settings.wb_standard_costs.storage_poll_seconds)
    raise TimeoutError("WB не подготовил хранение за отведённое время")


def fetch_sources(token: str, start: date, end: date) -> SourceBundle:
    """Collect a complete source bundle without changing database state."""
    finance = _finance(token, start, end)
    storage = _storage(token, start, end)
    cards = _cards(token)
    tariffs = {}
    for offset in range(7):
        day = (start + timedelta(days=offset)).isoformat()
        value = api.request("GET", COMMON + "/box", token, params={"date": day})
        _rows(value["response"]["data"]["warehouseList"], "тарифы")
        tariffs[day] = value
    commissions = api.request("GET", COMMON + "/commission", token, params={"locale": "ru"})
    _rows(commissions["report"], "комиссии")
    timestamp = datetime.now(UTC)
    raw = SourceBundle(
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
