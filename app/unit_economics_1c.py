import json
import math
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from app import db
from app import unit_economics_1c_advertising as advertising_sync
from app import unit_economics_1c_prices as price_sync
from app.config import settings
from app.domain import MOSCOW_TIMEZONE
from app.stores import STORES

DEFAULT_PERIOD_DAYS = 7
STOCK_COVERAGE_PERIOD_DAYS = 21


def calculate_paid_acceptance_cost(volume_l: float, acceptance_coefficient: float) -> float:
    volume = max(float(volume_l or 0), 0.0)
    coefficient = max(float(acceptance_coefficient or 0), 0.0)
    if volume < 1:
        return 0.0
    return round((1.7 + math.ceil(volume - 1) * 1.7) * coefficient, 2)


def calculate_delivery_with_returns(
    delivery_wb_rub: float,
    buyout_percent: float,
    return_cost_rub: float,
    paid_acceptance_cost: float,
) -> float:
    delivery = max(float(delivery_wb_rub or 0), 0.0)
    buyout_ratio = min(max(float(buyout_percent or 0), 0.0), 100.0) / 100
    return_cost = max(float(return_cost_rub or 0), 0.0)
    acceptance = max(float(paid_acceptance_cost or 0), 0.0)
    result = delivery * buyout_ratio + (return_cost + delivery * 2) * (1 - buyout_ratio) + acceptance
    return round(result, 2)


def calculate_drr_percent(advertising_spend: float, orders_amount: float) -> float:
    spend = max(float(advertising_spend or 0), 0.0)
    amount = max(float(orders_amount or 0), 0.0)
    if amount > 0:
        return round(spend / amount * 100, 2)
    return 100.0 if spend > 0 else 0.0


def calculate_tax_components(
    customer_price: float,
    vat_percent: float,
    usn_percent: float,
    osno_percent: float,
    tax_system: str,
) -> dict[str, float]:
    customer = max(float(customer_price), 0.0)
    vat_rate = max(float(vat_percent), 0.0)
    vat = customer * vat_rate / (100 + vat_rate)
    if str(tax_system).lower() == "osno":
        usn = 0.0
        osno = customer * max(float(osno_percent), 0.0) / 100
    else:
        usn = (customer - vat) * max(float(usn_percent), 0.0) / 100
        osno = 0.0
    return {
        "vat": vat,
        "usn": usn,
        "osno": osno,
        "secondary": osno if str(tax_system).lower() == "osno" else usn,
        "total": vat + usn + osno,
    }


def calculate_unit_profit(
    *,
    retail_price: float | None,
    customer_price: float | None,
    acquiring_percent: float | None,
    delivery_with_returns: float | None,
    storage_wb_rub: float | None,
    turnover_days: int | None,
    wb_commission_percent: float | None,
    drr_percent: float | None,
    purchase_price: float | None,
    fulfillment_cost: float | None,
    team_commission_percent: float | None,
    vat_percent: float | None,
    usn_percent: float | None,
    osno_percent: float | None,
    tax_system: str = "usn",
) -> dict[str, float] | None:
    """Calculate the same per-unit net profit that is shown in the UI calculator."""

    required = (
        retail_price,
        acquiring_percent,
        delivery_with_returns,
        storage_wb_rub,
        turnover_days,
        wb_commission_percent,
        drr_percent,
        purchase_price,
        fulfillment_cost,
        team_commission_percent,
        vat_percent,
    )
    active_secondary_rate = osno_percent if str(tax_system).lower() == "osno" else usn_percent
    if any(value is None for value in required) or active_secondary_rate is None:
        return None
    retail = max(float(retail_price), 0.0)
    customer = max(float(customer_price if customer_price is not None else retail), 0.0)
    acquiring = retail * max(float(acquiring_percent), 0.0) / 100
    logistics = max(float(delivery_with_returns), 0.0)
    storage = max(float(storage_wb_rub), 0.0) * max(int(turnover_days), 0)
    wb_commission = retail * max(float(wb_commission_percent), 0.0) / 100
    advertising = retail * max(float(drr_percent), 0.0) / 100
    team_commission = retail * max(float(team_commission_percent), 0.0) / 100
    taxes = calculate_tax_components(
        customer,
        float(vat_percent),
        float(usn_percent or 0),
        float(osno_percent or 0),
        tax_system,
    )
    net_revenue = retail - acquiring - logistics - storage - wb_commission - advertising
    margin = (
        net_revenue
        - max(float(purchase_price), 0.0)
        - max(float(fulfillment_cost), 0.0)
        - team_commission
        - taxes["total"]
    )
    return {
        "margin": round(margin, 2),
        "net_revenue": round(net_revenue, 2),
        "acquiring": round(acquiring, 2),
        "advertising": round(advertising, 2),
        "wb_commission": round(wb_commission, 2),
        "team_commission": round(team_commission, 2),
        "vat": round(taxes["vat"], 2),
        "usn": round(taxes["usn"], 2),
        "osno": round(taxes["osno"], 2),
        "tax": round(taxes["total"], 2),
        "storage": round(storage, 2),
    }


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _order_payload(row: dict) -> dict:
    try:
        raw = json.loads(str(row.get("raw_json") or "{}"))
    except (TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _nm_id_from_order(row: dict) -> str:
    raw = _order_payload(row)
    nm_id = str(raw.get("nmId") or raw.get("nmID") or "").strip()
    if nm_id:
        return nm_id
    article = str(row.get("article") or "").partition(" / ")[0].strip()
    return article if article.isdigit() else ""


def calculate_stock_coverage_days(
    total_stock: object,
    orders_count: object,
    *,
    period_days: int = STOCK_COVERAGE_PERIOD_DAYS,
) -> float:
    """Return stock coverage based on net orders, including zero-order products."""

    stock = max(_number(total_stock), 0.0)
    orders = max(_number(orders_count), 0.0)
    days = max(int(period_days or 0), 1)
    if stock <= 0 or orders <= 0:
        return 0.0
    return round(stock * days / orders, 2)


def load_product_average_daily_orders(
    store_slugs: tuple[str, ...],
    *,
    period_days: int = STOCK_COVERAGE_PERIOD_DAYS,
    today: date | None = None,
) -> dict[tuple[str, str], dict]:
    """Load net WB order demand used for the stock coverage column."""

    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    days = max(int(period_days or 0), 1)
    date_from = today - timedelta(days=days - 1)
    period_start = datetime.combine(date_from, time.min, tzinfo=MOSCOW_TIMEZONE).isoformat()
    period_end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=MOSCOW_TIMEZONE).isoformat()
    order_counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in db.get_unit_economics_1c_wb_order_metric_rows(
        store_slugs,
        period_start,
        period_end,
    ):
        nm_id = _nm_id_from_order(row)
        ordered_day = str(row.get("ordered_at") or "")[:10]
        if not nm_id or ordered_day < date_from.isoformat() or ordered_day > today.isoformat():
            continue
        count = max(_integer(row.get("quantity")) - _integer(row.get("cancelled_quantity")), 0)
        order_counts[(str(row["store_slug"]), nm_id)] += count

    return {
        key: {
            "period_from": date_from.isoformat(),
            "period_to": today.isoformat(),
            "period_days": days,
            "orders_count": count,
            "average_daily_orders": round(count / days, 4),
        }
        for key, count in order_counts.items()
    }


def load_product_metrics(
    store_slugs: tuple[str, ...],
    *,
    period_days: int = DEFAULT_PERIOD_DAYS,
    today: date | None = None,
) -> dict[tuple[str, str], dict]:
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    date_from = today - timedelta(days=max(1, period_days) - 1)
    days = [date_from + timedelta(days=offset) for offset in range((today - date_from).days + 1)]
    daily: dict[tuple[str, str], dict[str, dict[str, float | int]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "advertising_spend": 0.0,
                "orders_amount": 0.0,
                "orders_count": 0,
                "sold_count": 0,
                "retail_price_amount": 0.0,
                "retail_price_count": 0,
            }
        )
    )

    for row in db.get_unit_economics_1c_daily_advertising(
        store_slugs,
        date_from.isoformat(),
        today.isoformat(),
    ):
        key = (str(row["store_slug"]), str(row["nm_id"]))
        daily[key][str(row["day"])]["advertising_spend"] += _number(row.get("spend"))

    period_start = datetime.combine(date_from, time.min, tzinfo=MOSCOW_TIMEZONE).isoformat()
    period_end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=MOSCOW_TIMEZONE).isoformat()
    for row in db.get_unit_economics_1c_wb_order_metric_rows(store_slugs, period_start, period_end):
        nm_id = _nm_id_from_order(row)
        ordered_day = str(row.get("ordered_at") or "")[:10]
        if not nm_id or ordered_day < date_from.isoformat() or ordered_day > today.isoformat():
            continue
        key = (str(row["store_slug"]), nm_id)
        amount = max(_number(row.get("order_amount")) - _number(row.get("cancelled_amount")), 0.0)
        count = max(_integer(row.get("quantity")) - _integer(row.get("cancelled_quantity")), 0)
        sold_count = max(_integer(row.get("sold_quantity")) - _integer(row.get("return_quantity")), 0)
        retail_price = _number(_order_payload(row).get("priceWithDisc"))
        daily[key][ordered_day]["orders_amount"] += amount
        daily[key][ordered_day]["orders_count"] += count
        daily[key][ordered_day]["sold_count"] += min(sold_count, count)
        if retail_price > 0 and count > 0:
            daily[key][ordered_day]["retail_price_amount"] += retail_price * count
            daily[key][ordered_day]["retail_price_count"] += count

    result: dict[tuple[str, str], dict] = {}
    for key, by_day in daily.items():
        history = []
        for day_value in days:
            values = by_day[day_value.isoformat()]
            advertising_spend = round(float(values["advertising_spend"]), 2)
            orders_amount = round(float(values["orders_amount"]), 2)
            orders_count = int(values["orders_count"])
            sold_count = min(int(values["sold_count"]), orders_count)
            retail_price_count = int(values["retail_price_count"])
            history.append(
                {
                    "date": day_value.isoformat(),
                    "advertising_spend": advertising_spend,
                    "orders_amount": orders_amount,
                    "orders_count": orders_count,
                    "sold_count": sold_count,
                    "average_retail_price": (
                        round(float(values["retail_price_amount"]) / retail_price_count, 2)
                        if retail_price_count
                        else None
                    ),
                    "retail_price_count": retail_price_count,
                    "drr": calculate_drr_percent(advertising_spend, orders_amount),
                }
            )
        spend = round(sum(item["advertising_spend"] for item in history), 2)
        orders_amount = round(sum(item["orders_amount"] for item in history), 2)
        orders_count = sum(item["orders_count"] for item in history)
        sold_count = sum(item["sold_count"] for item in history)
        retail_price_count = sum(item["retail_price_count"] for item in history)
        retail_price_amount = sum(
            (item["average_retail_price"] or 0) * item["retail_price_count"] for item in history
        )
        result[key] = {
            "period_from": date_from.isoformat(),
            "period_to": today.isoformat(),
            "period_days": len(days),
            "spend": spend,
            "orders_amount": orders_amount,
            "orders_count": orders_count,
            "sold_count": sold_count,
            "average_retail_price": (
                round(retail_price_amount / retail_price_count, 2) if retail_price_count else None
            ),
            "buyout_percent": round(sold_count / orders_count * 100, 2) if orders_count else 0.0,
            "drr": calculate_drr_percent(spend, orders_amount),
            "daily": history,
        }
    return result


def empty_product_metrics(*, period_days: int = DEFAULT_PERIOD_DAYS, today: date | None = None) -> dict:
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    date_from = today - timedelta(days=max(1, period_days) - 1)
    return {
        "period_from": date_from.isoformat(),
        "period_to": today.isoformat(),
        "period_days": max(1, period_days),
        "spend": 0.0,
        "orders_amount": 0.0,
        "orders_count": 0,
        "sold_count": 0,
        "average_retail_price": None,
        "buyout_percent": 0.0,
        "drr": 0.0,
        "daily": [
            {
                "date": (date_from + timedelta(days=offset)).isoformat(),
                "advertising_spend": 0.0,
                "orders_amount": 0.0,
                "orders_count": 0,
                "sold_count": 0,
                "average_retail_price": None,
                "retail_price_count": 0,
                "drr": 0.0,
            }
            for offset in range(max(1, period_days))
        ],
    }


def sync_store(store_slug: str) -> dict:
    if store_slug not in STORES:
        raise ValueError("Неизвестный кабинет")
    prices = price_sync.sync_store(store_slug)
    advertising = advertising_sync.sync_store(store_slug)
    return {
        "ok": bool(prices.get("ok")) and bool(advertising.get("ok")),
        "prices": prices,
        "advertising": advertising,
    }


def sync_stores(store_slugs: tuple[str, ...]) -> dict[str, dict]:
    stores = tuple(store_slug for store_slug in store_slugs if store_slug in STORES)
    prices = price_sync.sync_stores(stores)
    advertising = advertising_sync.sync_stores(stores)
    return {
        store_slug: {
            "ok": bool(prices[store_slug].get("ok")) and bool(advertising[store_slug].get("ok")),
            "prices": prices[store_slug],
            "advertising": advertising[store_slug],
        }
        for store_slug in stores
    }


def sync_all() -> dict[str, dict]:
    return sync_stores(tuple(STORES))


def _attempted_today(state: dict | None, today: date) -> bool:
    threshold = datetime.combine(today, time.min, tzinfo=MOSCOW_TIMEZONE)
    return _attempted_since(state, threshold)


def _attempted_since(state: dict | None, threshold: datetime) -> bool:
    if not state or not state.get("last_attempt_at"):
        return False
    try:
        attempted_at = datetime.fromisoformat(str(state["last_attempt_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    if attempted_at.tzinfo is None:
        attempted_at = attempted_at.replace(tzinfo=UTC)
    if threshold.tzinfo is None:
        threshold = threshold.replace(tzinfo=UTC)
    return attempted_at >= threshold


def sync_prices_due() -> dict[str, dict]:
    stores = tuple(STORES)
    threshold = datetime.now(MOSCOW_TIMEZONE) - timedelta(
        seconds=settings.unit_economics_1c_price_sync_interval_seconds
    )
    price_states = {
        str(state["store_slug"]): state for state in db.list_unit_economics_1c_price_sync_states(stores)
    }
    due = tuple(
        store_slug for store_slug in stores if not _attempted_since(price_states.get(store_slug), threshold)
    )
    return price_sync.sync_stores(due)


def sync_wallet_prices() -> dict[str, dict]:
    """Refresh public SPP and WB Wallet prices without calling the seller-price API."""

    return price_sync.sync_stores(
        tuple(STORES),
        load_retail_prices=False,
        record_state=False,
    )


def sync_due() -> dict[str, dict]:
    """Backward-compatible name for the scheduled price-only synchronization."""
    return sync_prices_due()
