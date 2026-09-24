import math
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app import db
from app.config import settings
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.economics.wb import advertising as advertising_sync
from app.economics.wb import prices as price_sync

DEFAULT_PERIOD_DAYS = 7
STOCK_COVERAGE_PERIOD_DAYS = 21
LOW_STOCK_COVERAGE_DAYS = 14
OVERSTOCK_COVERAGE_DAYS = 90


def money(value: object) -> float:
    """Round displayed WB money and percentages the same way as YM."""
    display = Decimal(format(Decimal(str(value)), ".15g"))
    return float(display.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def resolve_buyout_percent(raw: object, default: float | None = None) -> float:
    """Resolve current WB buyout; historical snapshots must not use today's default."""
    measured = money(min(max(_number(raw), 0.0), 100.0))
    return measured if measured > 0 else money(min(max(_number(default), 0.0), 100.0))


def apply_buyout_default(metrics: dict, default: float | None) -> dict:
    """Keep WB's raw value while recalculating dependent metrics with the effective one."""
    raw = metrics.get("raw_buyout_percent", metrics.get("buyout_percent"))
    effective = resolve_buyout_percent(raw, default)
    return {
        **metrics,
        "raw_buyout_percent": raw,
        "buyout_percent": effective,
        "default_buyout_percent": default,
        "buyout_default_applied": _number(raw) <= 0 and effective > 0,
        "spend_per_order": calculate_advertising_per_unit(
            metrics.get("spend"),
            metrics.get("orders_count"),
            effective,
        ),
        "drr": calculate_drr_percent(metrics.get("spend"), metrics.get("orders_amount"), effective),
        "daily": [
            {
                **day,
                "drr": calculate_drr_percent(
                    day.get("advertising_spend"),
                    day.get("orders_amount"),
                    effective,
                ),
            }
            for day in metrics.get("daily") or []
        ],
    }


def calculate_paid_acceptance_cost(volume_l: float, acceptance_coefficient: float) -> float:
    volume = max(float(volume_l or 0), 0.0)
    coefficient = max(float(acceptance_coefficient or 0), 0.0)
    if volume < 1:
        return 0.0
    return money((1.7 + math.ceil(volume - 1) * 1.7) * coefficient)


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
    return money(result)


def _effective_buyout_ratio(buyout_percent: float) -> float:
    """Return a safe buyout ratio, treating a missing WB value as 100%."""

    buyout_ratio = min(max(float(buyout_percent or 0), 0.0), 100.0) / 100
    return buyout_ratio if buyout_ratio > 0 else 1.0


def calculate_drr_percent(
    advertising_spend: float,
    orders_amount: float,
    buyout_percent: float = 100,
) -> float:
    """Return DRR against expected bought-out WB funnel turnover."""

    spend = max(float(advertising_spend or 0), 0.0)
    amount = max(float(orders_amount or 0), 0.0)
    if amount > 0:
        return money(spend / amount / _effective_buyout_ratio(buyout_percent) * 100)
    return 100.0 if spend > 0 else 0.0


def calculate_advertising_per_unit(
    advertising_spend: float,
    orders_count: int,
    buyout_percent: float,
) -> float:
    """Allocate advertising spend to an expected bought unit.

    Until WB supplies a positive buyout percentage, use 100% so advertising
    remains included in unit profit instead of disappearing from the result.
    """

    spend = max(float(advertising_spend or 0), 0.0)
    orders = max(int(orders_count or 0), 0)
    if not orders:
        return 0.0
    return money(spend / orders / _effective_buyout_ratio(buyout_percent))


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
    advertising_rub: float | None,
    purchase_price: float | None,
    fulfillment_cost: float | None,
    team_commission_percent: float | None,
    vat_percent: float | None,
    usn_percent: float | None,
    osno_percent: float | None,
    tax_system: str = "usn",
    margin_only: bool = False,
) -> dict[str, float] | None:
    """Calculate the same per-unit net profit that is shown in the UI calculator."""

    required = (
        retail_price,
        acquiring_percent,
        delivery_with_returns,
        storage_wb_rub,
        turnover_days,
        wb_commission_percent,
        advertising_rub,
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
    advertising = max(float(advertising_rub), 0.0)
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
    rounded_margin = money(margin)
    if margin_only:
        return {"margin": rounded_margin}
    return {
        "margin": rounded_margin,
        "net_revenue": money(net_revenue),
        "acquiring": money(acquiring),
        "advertising": money(advertising),
        "wb_commission": money(wb_commission),
        "team_commission": money(team_commission),
        "vat": money(taxes["vat"]),
        "usn": money(taxes["usn"]),
        "osno": money(taxes["osno"]),
        "tax": money(taxes["total"]),
        "storage": money(storage),
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
    return money(stock * days / orders)


def classify_stock_state(
    total_stock: object,
    orders_count: object,
    internal_status: object,
    *,
    period_days: int = STOCK_COVERAGE_PERIOD_DAYS,
) -> dict[str, object]:
    """Classify shortages from 1C levels and 21-day demand, never by an absolute stock count."""

    stock = max(_number(total_stock), 0.0)
    orders = max(_number(orders_count), 0.0)
    status = str(internal_status or "").strip().casefold()
    coverage = calculate_stock_coverage_days(stock, orders, period_days=period_days)
    shortage_status = any(
        marker in status for marker in ("low", "short", "шорт", "шерт", "дефиц", "мало", "законч")
    )
    risk_status = shortage_status or any(
        marker in status for marker in ("risk", "риск", "over", "овер", "избыт")
    )
    demand_low = orders > 0 and coverage <= LOW_STOCK_COVERAGE_DAYS
    demand_over = orders > 0 and coverage >= OVERSTOCK_COVERAGE_DAYS
    is_low = shortage_status or demand_low
    is_risk = risk_status or demand_low or demand_over
    return {
        "is_low": is_low,
        "is_risk": is_risk,
        "coverage_days": coverage,
        "reason": "internal" if risk_status else "coverage" if demand_low or demand_over else None,
    }


def load_product_average_daily_orders(
    store_slugs: tuple[str, ...],
    *,
    period_days: int = STOCK_COVERAGE_PERIOD_DAYS,
    today: date | None = None,
) -> dict[tuple[str, str], dict]:
    """Load WB funnel orderCount demand used for the stock coverage column."""

    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    days = max(int(period_days or 0), 1)
    date_from = today - timedelta(days=days - 1)
    order_counts: dict[tuple[str, str], int] = defaultdict(int)
    loaded_days: dict[str, set[str]] = defaultdict(set)
    for row in db.get_unit_economics_1c_funnel_daily_order_rows(
        store_slugs,
        date_from.isoformat(),
        today.isoformat(),
    ):
        key = (str(row["store_slug"]), str(row["article"]))
        order_counts[key] += _integer(row.get("orders_count"))
        loaded_days[key[0]].add(str(row["day"]))

    expected = [(date_from + timedelta(days=offset)).isoformat() for offset in range(days)]

    def coverage(store: str) -> dict:
        present = [day for day in expected if day in loaded_days[store]]
        return {
            "dates": present,
            "days": len(present),
            "expected_days": days,
            "complete": len(present) == days,
            "missing_dates": [day for day in expected if day not in loaded_days[store]],
        }

    return {
        key: {
            "period_from": date_from.isoformat(),
            "period_to": today.isoformat(),
            "period_days": days,
            "orders_count": count,
            "average_daily_orders": round(count / max(len(loaded_days[key[0]]), 1), 4),
            "coverage": coverage(key[0]),
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
                "advertising_impressions": 0,
                "advertising_clicks": 0,
                "orders_amount": 0.0,
                "orders_count": 0,
                "cancel_amount": 0.0,
                "cancel_count": 0,
                "buyout_amount": None,
                "buyout_count": None,
                "buyout_percent": None,
                "funnel_updated_at": None,
                "funnel_source_version": None,
                "funnel_vendor_code": None,
                "funnel_product_name": None,
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
        daily[key][str(row["day"])]["advertising_impressions"] += _integer(row.get("impressions"))
        daily[key][str(row["day"])]["advertising_clicks"] += _integer(row.get("clicks"))

    for row in db.get_unit_economics_1c_funnel_daily_order_rows(
        store_slugs,
        date_from.isoformat(),
        today.isoformat(),
    ):
        key = (str(row["store_slug"]), str(row["article"]))
        ordered_day = str(row["day"])
        daily[key][ordered_day]["orders_amount"] += _number(row.get("orders_amount"))
        daily[key][ordered_day]["orders_count"] += _integer(row.get("orders_count"))
        daily[key][ordered_day]["cancel_amount"] += _number(row.get("cancel_amount"))
        daily[key][ordered_day]["cancel_count"] += _integer(row.get("cancel_count"))
        if row.get("buyout_amount") is not None:
            daily[key][ordered_day]["buyout_amount"] = money(
                _number(daily[key][ordered_day].get("buyout_amount")) + _number(row.get("buyout_amount"))
            )
        if row.get("buyout_count") is not None:
            daily[key][ordered_day]["buyout_count"] = _integer(
                daily[key][ordered_day].get("buyout_count")
            ) + _integer(row.get("buyout_count"))
        if row.get("buyout_percent") is not None:
            daily[key][ordered_day]["buyout_percent"] = money(
                min(max(_number(row.get("buyout_percent")), 0.0), 100.0)
            )
        updated_at = str(row.get("updated_at") or "") or None
        current_updated_at = daily[key][ordered_day]["funnel_updated_at"]
        if updated_at and (not current_updated_at or updated_at > current_updated_at):
            daily[key][ordered_day]["funnel_updated_at"] = updated_at
        source_version = _integer(row.get("source_version"))
        current_source_version = daily[key][ordered_day]["funnel_source_version"]
        if source_version and (current_source_version is None or source_version < current_source_version):
            daily[key][ordered_day]["funnel_source_version"] = source_version
        if not daily[key][ordered_day]["funnel_vendor_code"]:
            daily[key][ordered_day]["funnel_vendor_code"] = str(row.get("vendor_code") or "") or None
        if not daily[key][ordered_day]["funnel_product_name"]:
            daily[key][ordered_day]["funnel_product_name"] = str(row.get("product_name") or "") or None

    funnel_metrics = {
        (str(row["store_slug"]), str(row["article"])): row
        for row in db.get_unit_economics_1c_funnel_product_metrics(store_slugs)
    }
    for key in funnel_metrics:
        daily[key]

    cabinets = {item.store_slug: item for item in db.list_unit_economics_1c_cabinet_settings(store_slugs)}
    result: dict[tuple[str, str], dict] = {}
    for key, by_day in daily.items():
        funnel_metric = funnel_metrics.get(key) or {}
        buyout_percent = money(_number(funnel_metric.get("buyout_percent")))
        history = []
        for day_value in days:
            values = by_day[day_value.isoformat()]
            advertising_spend = money(values["advertising_spend"])
            advertising_impressions = int(values["advertising_impressions"])
            advertising_clicks = int(values["advertising_clicks"])
            orders_amount = money(values["orders_amount"])
            orders_count = int(values["orders_count"])
            cancel_amount = money(values["cancel_amount"])
            cancel_count = int(values["cancel_count"])
            buyout_amount = (
                money(values["buyout_amount"]) if values["buyout_amount"] is not None else None
            )
            buyout_count = int(values["buyout_count"]) if values["buyout_count"] is not None else None
            daily_buyout_percent = (
                money(values["buyout_percent"]) if values["buyout_percent"] is not None else None
            )
            history.append(
                {
                    "date": day_value.isoformat(),
                    "advertising_spend": advertising_spend,
                    "advertising_impressions": advertising_impressions,
                    "advertising_clicks": advertising_clicks,
                    "orders_amount": orders_amount,
                    "orders_count": orders_count,
                    "cancel_amount": cancel_amount,
                    "cancel_count": cancel_count,
                    "buyout_amount": buyout_amount,
                    "buyout_count": buyout_count,
                    "buyout_percent": daily_buyout_percent,
                    "net_orders_amount": money(orders_amount - cancel_amount),
                    "net_orders_count": orders_count - cancel_count,
                    "funnel_updated_at": values["funnel_updated_at"],
                    "funnel_source_version": values["funnel_source_version"],
                    "funnel_vendor_code": values["funnel_vendor_code"],
                    "funnel_product_name": values["funnel_product_name"],
                    "drr": calculate_drr_percent(
                        advertising_spend,
                        orders_amount,
                        buyout_percent,
                    ),
                }
            )
        spend = money(sum(item["advertising_spend"] for item in history))
        impressions = sum(item["advertising_impressions"] for item in history)
        clicks = sum(item["advertising_clicks"] for item in history)
        orders_amount = money(sum(item["orders_amount"] for item in history))
        orders_count = sum(item["orders_count"] for item in history)
        cancel_amount = money(sum(item["cancel_amount"] for item in history))
        cancel_count = sum(item["cancel_count"] for item in history)
        known_buyout_amounts = [
            float(item["buyout_amount"]) for item in history if item["buyout_amount"] is not None
        ]
        known_buyout_counts = [
            int(item["buyout_count"]) for item in history if item["buyout_count"] is not None
        ]
        buyout_weight = sum(
            int(item["orders_count"]) for item in history if item["buyout_percent"] is not None
        )
        range_buyout_percent = (
            money(
                sum(
                    float(item["buyout_percent"]) * int(item["orders_count"])
                    for item in history
                    if item["buyout_percent"] is not None
                )
                / buyout_weight
            )
            if buyout_weight
            else None
        )
        funnel_updated_at = max(
            (str(item["funnel_updated_at"]) for item in history if item["funnel_updated_at"]),
            default=None,
        )
        funnel_source_versions = [
            int(item["funnel_source_version"])
            for item in history
            if item["funnel_source_version"] is not None
        ]
        funnel_vendor_code = next(
            (item["funnel_vendor_code"] for item in history if item["funnel_vendor_code"]),
            None,
        )
        funnel_product_name = next(
            (item["funnel_product_name"] for item in history if item["funnel_product_name"]),
            None,
        )
        matching_funnel_period = (
            str(funnel_metric.get("period_from") or "") == date_from.isoformat()
            and str(funnel_metric.get("period_to") or "") == today.isoformat()
        )
        if matching_funnel_period:
            orders_amount = money(_number(funnel_metric.get("orders_amount")))
            orders_count = _integer(funnel_metric.get("orders_count"))
            cancel_amount = money(_number(funnel_metric.get("cancel_amount")))
            cancel_count = _integer(funnel_metric.get("cancel_count"))
            funnel_updated_at = str(funnel_metric.get("updated_at") or "") or funnel_updated_at
            if not funnel_source_versions:
                source_version = _integer(funnel_metric.get("source_version"))
                if source_version:
                    funnel_source_versions.append(source_version)
        result[key] = {
            "period_from": date_from.isoformat(),
            "period_to": today.isoformat(),
            "period_days": len(days),
            "spend": spend,
            "average_daily_spend": money(spend / len(days)),
            "spend_per_order": calculate_advertising_per_unit(
                spend,
                orders_count,
                buyout_percent,
            ),
            "impressions": impressions,
            "clicks": clicks,
            "ctr": money(clicks / impressions * 100) if impressions else 0.0,
            "cpc": money(spend / clicks) if clicks else 0.0,
            "orders_amount": orders_amount,
            "orders_count": orders_count,
            "cancel_amount": cancel_amount,
            "cancel_count": cancel_count,
            "buyout_amount": money(sum(known_buyout_amounts)) if known_buyout_amounts else None,
            "buyout_count": sum(known_buyout_counts) if known_buyout_counts else None,
            "range_buyout_percent": range_buyout_percent,
            "buyout_percent_weight": buyout_weight,
            "net_orders_amount": money(orders_amount - cancel_amount),
            "net_orders_count": orders_count - cancel_count,
            "funnel_updated_at": funnel_updated_at,
            "funnel_source_version": min(funnel_source_versions, default=None),
            "funnel_vendor_code": funnel_vendor_code,
            "funnel_product_name": funnel_product_name,
            "sold_count": 0,
            "average_retail_price": None,
            "buyout_percent": buyout_percent,
            "buyout_orders_count": _integer(funnel_metric.get("orders_count")),
            "buyout_orders_amount": money(_number(funnel_metric.get("orders_amount"))),
            "buyout_cancel_count": _integer(funnel_metric.get("cancel_count")),
            "buyout_cancel_amount": money(_number(funnel_metric.get("cancel_amount"))),
            "buyout_net_orders_count": (
                _integer(funnel_metric.get("orders_count")) - _integer(funnel_metric.get("cancel_count"))
            ),
            "buyout_net_orders_amount": money(
                _number(funnel_metric.get("orders_amount")) - _number(funnel_metric.get("cancel_amount"))
            ),
            "buyout_period_from": funnel_metric.get("period_from"),
            "buyout_period_to": funnel_metric.get("period_to"),
            "buyout_updated_at": funnel_metric.get("updated_at"),
            "buyout_source_version": funnel_metric.get("source_version"),
            "drr": calculate_drr_percent(spend, orders_amount, buyout_percent),
            "daily": history,
        }
        result[key] = apply_buyout_default(result[key], cabinets[key[0]].default_buyout_percent)
    return result


def empty_product_metrics(*, period_days: int = DEFAULT_PERIOD_DAYS, today: date | None = None) -> dict:
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    date_from = today - timedelta(days=max(1, period_days) - 1)
    return {
        "period_from": date_from.isoformat(),
        "period_to": today.isoformat(),
        "period_days": max(1, period_days),
        "spend": 0.0,
        "average_daily_spend": 0.0,
        "spend_per_order": 0.0,
        "impressions": 0,
        "clicks": 0,
        "ctr": 0.0,
        "cpc": 0.0,
        "orders_amount": 0.0,
        "orders_count": 0,
        "cancel_amount": 0.0,
        "cancel_count": 0,
        "buyout_amount": None,
        "buyout_count": None,
        "range_buyout_percent": None,
        "buyout_percent_weight": 0,
        "net_orders_amount": 0.0,
        "net_orders_count": 0,
        "funnel_updated_at": None,
        "funnel_source_version": None,
        "funnel_vendor_code": None,
        "funnel_product_name": None,
        "sold_count": 0,
        "average_retail_price": None,
        "buyout_percent": 0.0,
        "buyout_orders_count": 0,
        "buyout_orders_amount": 0.0,
        "buyout_cancel_count": 0,
        "buyout_cancel_amount": 0.0,
        "buyout_net_orders_count": 0,
        "buyout_net_orders_amount": 0.0,
        "buyout_period_from": None,
        "buyout_period_to": None,
        "buyout_updated_at": None,
        "buyout_source_version": None,
        "drr": 0.0,
        "daily": [
            {
                "date": (date_from + timedelta(days=offset)).isoformat(),
                "advertising_spend": 0.0,
                "advertising_impressions": 0,
                "advertising_clicks": 0,
                "orders_amount": 0.0,
                "orders_count": 0,
                "cancel_amount": 0.0,
                "cancel_count": 0,
                "buyout_amount": None,
                "buyout_count": None,
                "buyout_percent": None,
                "net_orders_amount": 0.0,
                "net_orders_count": 0,
                "funnel_updated_at": None,
                "funnel_source_version": None,
                "funnel_vendor_code": None,
                "funnel_product_name": None,
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


def sync_prices_due(store_slugs: tuple[str, ...] | None = None) -> dict[str, dict]:
    stores = tuple(
        store_slug
        for store_slug in (tuple(STORES) if store_slugs is None else store_slugs)
        if store_slug in STORES
    )
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


def sync_wallet_prices(store_slugs: tuple[str, ...] | None = None) -> dict[str, dict]:
    """Refresh public SPP and WB Wallet prices without calling the seller-price API."""

    reports = price_sync.sync_stores(
        tuple(
            store_slug
            for store_slug in (tuple(STORES) if store_slugs is None else store_slugs)
            if store_slug in STORES
        ),
        load_retail_prices=False,
        record_state=False,
    )
    for store_slug, report in reports.items():
        ok = bool(report.get("ok")) and bool(report.get("wallet_discount_ok"))
        db.record_sync_health(
            store_slug,
            "WB",
            "unit_economics_1c_wallet",
            ok,
            report.get("wallet_error") or report.get("error"),
            datetime.now(UTC).isoformat(),
        )
    return reports


def sync_due() -> dict[str, dict]:
    """Backward-compatible name for the scheduled price-only synchronization."""
    return sync_prices_due()
