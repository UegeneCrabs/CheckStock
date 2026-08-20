from calendar import monthrange
from datetime import date, datetime, timedelta

from app.domain import MOSCOW_TIMEZONE
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

URGENT_WINDOW = timedelta(hours=36)
PLANNED_SUPPLY_RANGE_MONTHS = 3
BOX_TYPE_LABELS = {
    0: "Виртуальная поставка",
    1: "Короба",
    2: "Короба",
    5: "Монопаллеты",
    6: "Суперсейф",
}


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=MOSCOW_TIMEZONE)
    return value.astimezone(MOSCOW_TIMEZONE)


def shift_month(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def planned_supply_date_bounds(now: datetime | None = None) -> dict[str, date]:
    today = normalize_datetime(now or datetime.now(MOSCOW_TIMEZONE)).date()
    return {
        "min_date": shift_month(today, -PLANNED_SUPPLY_RANGE_MONTHS),
        "max_date": shift_month(today, PLANNED_SUPPLY_RANGE_MONTHS),
        "default_from": shift_month(today, -PLANNED_SUPPLY_RANGE_MONTHS),
        "default_to": shift_month(today, PLANNED_SUPPLY_RANGE_MONTHS),
    }


def planned_supply_date_range(
    date_from: date | None = None,
    date_to: date | None = None,
    *,
    now: datetime | None = None,
) -> tuple[date, date, dict[str, date]]:
    bounds = planned_supply_date_bounds(now)
    start = date_from or bounds["default_from"]
    end = date_to or bounds["default_to"]
    if start < bounds["min_date"] or end > bounds["max_date"]:
        raise ValueError("Даты можно выбирать только в пределах 3 месяцев от текущей даты")
    if start > end:
        raise ValueError("Дата начала не может быть позже даты окончания")
    return start, end, bounds


def parse_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return normalize_datetime(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        return None


def is_urgent(value: object, now: datetime | None = None) -> bool:
    delivery_at = parse_datetime(value)
    if delivery_at is None:
        return False
    current = normalize_datetime(now or datetime.now(MOSCOW_TIMEZONE))
    return delivery_at <= current + URGENT_WINDOW


def supply_type_label(row: dict) -> str:
    box_type_id = row.get("boxTypeID")
    try:
        normalized = int(box_type_id)
    except (TypeError, ValueError):
        return "Не указан"
    if normalized == 2 and row.get("isBoxOnPallet") is True:
        return "Поштучная паллета"
    return BOX_TYPE_LABELS.get(normalized, f"Тип {normalized}")


def load_wb_planned_supplies(
    store_slugs: tuple[str, ...],
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    now: datetime | None = None,
) -> dict:
    supplies: list[dict] = []
    errors: list[dict] = []
    current = normalize_datetime(now or datetime.now(MOSCOW_TIMEZONE))
    start, end, bounds = planned_supply_date_range(date_from, date_to, now=current)

    for store_slug in store_slugs:
        store = STORES.get(store_slug)
        if store is None:
            continue
        if not wb_tokens.has_token(store_slug):
            errors.append(
                {
                    "store_slug": store_slug,
                    "store_name": store.name,
                    "error": "WB-токен не настроен",
                }
            )
            continue
        try:
            rows = wb_api.get_fbw_supplies(
                wb_tokens.get_token(store_slug),
                status_ids=(2,),
                date_from=start.isoformat(),
                date_to=end.isoformat(),
            )
        except Exception as error:
            message = error.friendly if isinstance(error, wb_api.WBApiError) else str(error)
            errors.append(
                {"store_slug": store_slug, "store_name": store.name, "error": message}
            )
            continue

        for row in rows:
            if int(row.get("statusID") or 0) != 2:
                continue
            supply_date = str(row.get("supplyDate") or "").strip()
            parsed_supply_date = parse_datetime(supply_date)
            if parsed_supply_date is None or not start <= parsed_supply_date.date() <= end:
                continue
            warehouse_name = str(
                row.get("warehouseName") or row.get("actualWarehouseName") or ""
            ).strip()
            supplies.append(
                {
                    "store_slug": store_slug,
                    "store_name": store.name,
                    "supply_id": row.get("supplyID"),
                    "preorder_id": row.get("preorderID"),
                    "supply_date": supply_date,
                    "warehouse_name": warehouse_name,
                    "transit_warehouse_name": str(row.get("transitWarehouseName") or "").strip(),
                    "supply_type": supply_type_label(row),
                    "box_type_id": row.get("boxTypeID"),
                    "status": "Запланировано",
                    "is_urgent": is_urgent(supply_date, current),
                }
            )

    supplies.sort(
        key=lambda item: (
            parse_datetime(item["supply_date"]) is None,
            parse_datetime(item["supply_date"])
            or datetime.max.replace(tzinfo=MOSCOW_TIMEZONE),
            item["store_name"],
        )
    )
    return {
        "supplies": supplies,
        "errors": errors,
        "fetched_at": current.isoformat(timespec="seconds"),
        "urgent_window_hours": int(URGENT_WINDOW.total_seconds() // 3600),
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "min_date": bounds["min_date"].isoformat(),
        "max_date": bounds["max_date"].isoformat(),
        "past_months": PLANNED_SUPPLY_RANGE_MONTHS,
        "future_months": PLANNED_SUPPLY_RANGE_MONTHS,
    }
