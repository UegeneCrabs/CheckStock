"""Shared WB calculator catalogue, independent of HTTP routes.

The existing catalogue JSON contract is preserved for browser consumers.
"""

import math
from datetime import UTC, date, datetime, timedelta

from app import db
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.dto.unit_economics_1c import UnitEconomics1CProductSettings
from app.economics import data_errors as unit_economics_data_errors
from app.economics.completeness import aggregate_days, annotate, describe
from app.economics.wb import calculations as unit_economics_1c
from app.economics.wb import history as unit_economics_1c_history
from app.economics.yandex import calculations as unit_economics_yandex
from app.exports import stock_sheet_inbound
from app.repositories import unit_economics_data_errors as data_errors_repository
from app.repositories.economics_coverage import wb_days

NEW_PRODUCT_MAX_SALES_DAYS = 28


def _price_value(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
        return unit_economics_1c.money(parsed) if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _nm_id(article: object) -> str:
    return str(article or "").partition(" / ")[0].strip()


def _optional_integer(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _period_coverage(
    covered_days: list[str] | set[str] | tuple[str, ...],
    date_from: date,
    date_to: date,
) -> dict:
    dates = sorted(
        {
            parsed.isoformat()
            for value in covered_days
            for parsed in (_optional_day(value),)
            if parsed is not None and date_from <= parsed <= date_to
        }
    )
    expected_days = (date_to - date_from).days + 1
    expected_dates = {(date_from + timedelta(days=offset)).isoformat() for offset in range(expected_days)}
    return {
        "dates": dates,
        "days": len(dates),
        "expected_days": expected_days,
        "complete": set(dates) == expected_dates,
        "period_from": dates[0] if dates else None,
        "period_to": dates[-1] if dates else None,
        "missing_dates": sorted(expected_dates.difference(dates)),
    }


def _report_historical_economics(
    *,
    date_from: date,
    date_to: date,
    daily_orders: dict[str, dict],
    margin_snapshots: dict[str, dict],
    live_day: date,
    live_unit_margin: float | None,
    live_purchase_price: float | None,
    daily_advertising: dict[str, float] | None = None,
    available_days: set[str] | None = None,
    fallback_buyout_percent: float | None = None,
    allow_partial: bool = False,
    include_uncovered_advertising: bool = False,
    orders_days: set[str] | None = None,
    advertising_days: set[str] | None = None,
    live_snapshot: dict | None = None,
    ignore_deferred_cost_warnings: bool = False,
) -> dict:
    """All surfaces share daily inputs and explicit source coverage."""
    daily_advertising = daily_advertising or {}
    rows = []
    current = date_from
    while current <= date_to:
        key = current.isoformat()
        snapshot = margin_snapshots.get(key)
        if snapshot is None and current == live_day:
            snapshot = live_snapshot
        rows.append(
            unit_economics_1c_history.report_day(
                key,
                snapshot,
                daily_orders.get(key) or {},
                daily_advertising.get(key),
                orders_known=key in (orders_days if orders_days is not None else available_days or set()),
                ads_known=key
                in (advertising_days if advertising_days is not None else available_days or set()),
                ignore_deferred_cost_warnings=ignore_deferred_cost_warnings,
            )
        )
        current += timedelta(days=1)
    period = aggregate_days(rows, [r["day"] for r in rows], unit_economics_1c.money)
    known_ads = [r["advertising_spend"] for r in rows if r["advertising_spend"] is not None]
    spend = unit_economics_1c.money(sum(known_ads)) if known_ads else None
    bought = sum(
        r["expected_buyouts"] for r in rows if r["profit"] is not None and r["expected_buyouts"] is not None
    )
    weights = [
        (r["inputs"]["buyout_percent"], r["orders_count"])
        for r in rows
        if r["orders_count"] is not None and r["inputs"].get("buyout_percent") is not None
    ]
    count = sum(n for _, n in weights)
    amounts = [
        float((daily_orders.get(r["day"]) or {}).get("orders_amount") or 0)
        * r["inputs"]["buyout_percent"]
        / 100
        for r in rows
        if r["orders_count"] is not None and r["inputs"].get("buyout_percent") is not None
    ]
    amount = sum(amounts) if amounts else None
    return {
        **period,
        "orders": unit_economics_1c.money(bought),
        "advertising_spend": spend,
        "expected_buyout_amount": unit_economics_1c.money(amount) if amount is not None else None,
        "drr": unit_economics_1c.money(spend / amount * 100)
        if amount and spend is not None
        else 0.0
        if spend == 0
        else None,
        "buyout_percent": unit_economics_1c.money(sum(q * n for q, n in weights) / count) if count else None,
        "buyout_orders_count": count,
        "daily": rows,
    }


def _unit_economics_1c_mock_product(
    store_slug: str,
    product: dict,
    price_snapshot: dict | None = None,
    acquiring_percent: float = 3.8,
    product_metrics: dict | None = None,
    current_product_metrics: dict | None = None,
    closed_period_economics: dict | None = None,
    turnover_coverage: dict | None = None,
    history_product_metrics: dict | None = None,
    history_day_economics: dict[str, dict] | None = None,
    history_prices_by_day: dict[str, dict] | None = None,
    product_settings: UnitEconomics1CProductSettings | None = None,
    acceptance_coefficient: float = 0,
    team_commission_percent: float = 0,
    vat_percent: float = 0,
    usn_percent: float = 0,
    osno_percent: float = 0,
    tax_system: str = "usn",
    product_reference: dict | None = None,
    wb_extra_tariff_percent: float = 0,
    stock_history_by_day: dict[str, dict] | None = None,
    stock_order_metrics: dict | None = None,
    reputation: dict | None = None,
    glued_products: list[dict] | None = None,
    history_days: int = 7,
    first_sale_at: str | None = None,
    sales_age_today: date | None = None,
    default_buyout_percent: float | None = None,
    source_states: dict[str, dict] | None = None,
    inbound_quantity: int | None = None,
    inbound_partial: bool = False,
    inbound_message: str = "",
) -> dict:
    """Build the 1C layout while keeping WB prices, orders and advertising real."""
    article = str(product.get("article") or "").strip()
    price_snapshot = price_snapshot or {}
    product_metrics = unit_economics_1c.apply_buyout_default(
        product_metrics or unit_economics_1c.empty_product_metrics(),
        default_buyout_percent,
    )
    current_product_metrics = current_product_metrics or product_metrics
    history_product_metrics = history_product_metrics or product_metrics
    history_day_economics = history_day_economics or {}
    history_prices_by_day = history_prices_by_day or {}
    product_settings = product_settings or UnitEconomics1CProductSettings(
        store_slug=store_slug,
        article=article,
    )
    product_reference = product_reference or {}
    stock_history_by_day = stock_history_by_day or {}
    stock_order_metrics = stock_order_metrics or {}
    reputation = reputation or {}
    glued_products = glued_products or []
    data_errors = unit_economics_data_errors.product_errors(
        product,
        price_snapshot,
        product_reference,
        product_metrics,
        reputation,
        source_states,
    )
    spp_price = _price_value(price_snapshot.get("customer_price_with_spp"))
    wallet_price = _price_value(price_snapshot.get("customer_price_with_wallet"))
    current_price = _price_value(price_snapshot.get("retail_price"))
    orders_count = max(int(current_product_metrics.get("orders_count") or 0), 0)
    average_customer_price = (
        unit_economics_1c.money(float(current_product_metrics.get("orders_amount") or 0) / orders_count)
        if orders_count
        else None
    )
    calculation_price = (
        spp_price
        if spp_price is not None
        else current_price
        if current_price is not None
        else average_customer_price
    )
    calculation_price_source = (
        "Цена с СПП"
        if spp_price is not None
        else "Текущая цена WB"
        if current_price is not None
        else "Средняя цена заказа"
        if average_customer_price is not None
        else None
    )
    economics_retail_price = (
        current_price
        if current_price is not None
        else _price_value(product_metrics.get("average_retail_price"))
    )
    if economics_retail_price is None:
        economics_retail_price = calculation_price
    fbs_stock = max(_optional_integer(product.get("fbs_stock")) or 0, 0)
    fbo_stock = max(_optional_integer(product.get("fbo_stock")) or 0, 0)
    fulfillment_stock = max(_optional_integer(product.get("ff_available")) or 0, 0)
    total_stock = fbs_stock + fbo_stock + fulfillment_stock
    stock_period_days = max(
        int(stock_order_metrics.get("period_days") or unit_economics_1c.STOCK_COVERAGE_PERIOD_DAYS),
        1,
    )
    stock_coverage = stock_order_metrics.get("coverage")
    stock_loaded_days = (
        int(stock_coverage.get("days") or 0)
        if stock_coverage
        else (stock_period_days if stock_order_metrics else 0)
    )
    stock_orders_count = max(int(stock_order_metrics.get("orders_count") or 0), 0)
    average_daily_orders = (
        unit_economics_1c.money(stock_orders_count / stock_loaded_days) if stock_loaded_days else None
    )
    stock_days = (
        unit_economics_1c.calculate_stock_coverage_days(
            total_stock,
            stock_orders_count,
            period_days=stock_loaded_days,
        )
        if stock_loaded_days
        else None
    )
    measured_buyout_percent = min(
        max(float(product_metrics.get("buyout_percent") or 0), 0.0),
        100.0,
    )
    current_buyout_percent = measured_buyout_percent
    store = STORES[store_slug]
    paid_acceptance_cost = unit_economics_1c.calculate_paid_acceptance_cost(
        product_settings.volume_l,
        acceptance_coefficient,
    )
    current_logistics = unit_economics_1c.calculate_delivery_with_returns(
        product_settings.delivery_wb_rub,
        current_buyout_percent,
        product_settings.return_cost_rub,
        paid_acceptance_cost,
    )
    abc_code = str(product_reference.get("abc_code") or "").strip() or None
    turnover_days = _optional_integer(product_reference.get("turnover_days"))
    if turnover_days is None:
        turnover_days = 21
    purchase_price = _price_value(product_reference.get("purchase_price"))
    fulfillment_cost = _price_value(product_reference.get("fulfillment_cost"))
    source_team_commission = _price_value(product_reference.get("team_commission_percent"))
    effective_team_commission = (
        source_team_commission
        if source_team_commission is not None
        else unit_economics_1c.money(team_commission_percent)
    )
    category = str(product_reference.get("category") or "").strip() or None
    subject_commission_percent = _price_value(product_reference.get("subject_commission_percent"))
    has_subject_commission = subject_commission_percent is not None
    if subject_commission_percent is None:
        subject_commission_percent = 0.0
    extra_tariff_percent = unit_economics_1c.money(max(float(wb_extra_tariff_percent or 0), 0.0))
    commission_percent = unit_economics_1c.money(subject_commission_percent + extra_tariff_percent)
    commission_value = (
        unit_economics_1c.money(economics_retail_price * commission_percent / 100)
        if economics_retail_price is not None
        else None
    )
    storage_sum = (
        unit_economics_1c.money(turnover_days * product_settings.storage_wb_rub)
        if turnover_days is not None
        else None
    )
    vat_rate = max(float(vat_percent or 0), 0.0)
    usn_rate = max(float(usn_percent or 0), 0.0)
    osno_rate = max(float(osno_percent or 0), 0.0)
    effective_tax_system = "osno" if store_slug == "gogol" and str(tax_system).lower() == "osno" else "usn"
    tax_components = (
        unit_economics_1c.calculate_tax_components(
            calculation_price,
            vat_rate,
            usn_rate,
            osno_rate,
            effective_tax_system,
        )
        if calculation_price is not None
        else None
    )
    history = []
    metric_history = {
        str(item.get("date")): item
        for item in history_product_metrics.get("daily") or []
        if isinstance(item, dict)
    }
    try:
        history_end = date.fromisoformat(str(history_product_metrics.get("period_to")))
    except ValueError:
        history_end = date.today()
    period_days = max(int(product_metrics.get("period_days") or 7), 1)
    history_period_days = max(int(history_product_metrics.get("period_days") or period_days), 1)
    visible_history_start = max(history_period_days - max(int(history_days), 0), 0)
    for offset_from_start in range(visible_history_start, history_period_days):
        offset = history_period_days - offset_from_start - 1
        day = history_end - timedelta(days=offset)
        day_metrics = metric_history.get(day.isoformat()) or {}
        day_ads = unit_economics_1c.money(float(day_metrics.get("advertising_spend") or 0))
        day_orders_amount = unit_economics_1c.money(float(day_metrics.get("orders_amount") or 0))
        day_orders_count = max(int(day_metrics.get("orders_count") or 0), 0)
        saved_day_economics = history_day_economics.get(day.isoformat())
        if saved_day_economics is not None:
            day_buyout_percent = _price_value(saved_day_economics.get("buyout_percent"))
            if day_buyout_percent is None:
                day_buyout_percent = 0.0
            purchased_units = unit_economics_1c.money(day_orders_count * day_buyout_percent / 100)
            day_drr = unit_economics_1c.calculate_drr_percent(
                day_ads,
                day_orders_amount,
                day_buyout_percent,
            )
            day_margin = saved_day_economics.get("margin")
            day_purchase_value = saved_day_economics.get("purchase_value")
        else:
            day_buyout_percent = None
            day_drr = None
            purchased_units = None
            day_margin = None
            day_purchase_value = None
        day_stock = stock_history_by_day.get(day.isoformat()) or {}
        history_fbs = _optional_integer(day_stock.get("fbs"))
        history_fbo = _optional_integer(day_stock.get("fbo"))
        history_fulfillment = _optional_integer(day_stock.get("fulfillment"))
        visible_fbs = history_fbs if history_fbs is not None else fbs_stock if offset == 0 else None
        visible_fbo = history_fbo if history_fbo is not None else fbo_stock if offset == 0 else None
        visible_fulfillment = (
            history_fulfillment
            if history_fulfillment is not None
            else fulfillment_stock
            if offset == 0
            else None
        )
        visible_stock_values = (visible_fbs, visible_fbo, visible_fulfillment)
        history_item = {
            "date": day.isoformat(),
            "label": day.strftime("%d.%m"),
            "price_with_spp_rub": _price_value(
                (history_prices_by_day.get(day.isoformat()) or {}).get("customer_price_with_spp")
            ),
            "margin_rub": day_margin,
            "margin_complete": (saved_day_economics or {}).get("complete", False),
            "messages": (saved_day_economics or {}).get(
                "messages", ["Недостаточно данных: нет дневного снимка"]
            ),
            "advertising_rub": day_ads,
            "drr_percent": day_drr,
            "orders_count": day_orders_count,
            "purchased_units": purchased_units,
            "buyout_percent": day_buyout_percent,
            "turnover_rub": day_orders_amount,
            "fbs_units": visible_fbs,
            "fbo_units": visible_fbo,
            "fulfillment_units": visible_fulfillment,
            "stock_units": (
                sum(value or 0 for value in visible_stock_values)
                if any(value is not None for value in visible_stock_values)
                else None
            ),
            "purchase_value": day_purchase_value,
        }
        if offset_from_start >= visible_history_start:
            history.append(history_item)

    period_orders_turnover = unit_economics_1c.money(float(product_metrics.get("orders_amount") or 0))
    period_turnover = unit_economics_1c.money(
        period_orders_turnover - float(product_metrics.get("cancel_amount") or 0)
    )
    period_advertising_spend = unit_economics_1c.money(float(product_metrics.get("spend") or 0))
    average_daily_advertising = unit_economics_1c.money(period_advertising_spend / period_days)
    period_orders_count = max(int(product_metrics.get("orders_count") or 0), 0)
    period_buyout_percent = measured_buyout_percent
    advertising_per_unit = unit_economics_1c.calculate_advertising_per_unit(
        period_advertising_spend,
        period_orders_count,
        period_buyout_percent,
    )
    period_drr = unit_economics_1c.calculate_drr_percent(
        float(product_metrics.get("drr_spend", period_advertising_spend) or 0),
        float(product_metrics.get("drr_orders_amount", period_orders_turnover) or 0),
        period_buyout_percent,
    )
    period_purchase_value = (
        unit_economics_1c.money(purchase_price * period_orders_count) if purchase_price is not None else None
    )
    current_advertising_spend = unit_economics_1c.money(float(current_product_metrics.get("spend") or 0))
    current_advertising_per_unit = unit_economics_1c.calculate_advertising_per_unit(
        current_advertising_spend,
        orders_count,
        current_buyout_percent,
    )
    current_unit_profit = unit_economics_1c.calculate_unit_profit(
        retail_price=economics_retail_price,
        customer_price=calculation_price,
        acquiring_percent=acquiring_percent,
        delivery_with_returns=current_logistics,
        storage_wb_rub=product_settings.storage_wb_rub,
        turnover_days=turnover_days,
        wb_commission_percent=commission_percent,
        advertising_rub=current_advertising_per_unit,
        purchase_price=purchase_price,
        fulfillment_cost=fulfillment_cost,
        team_commission_percent=effective_team_commission,
        vat_percent=vat_rate,
        usn_percent=usn_rate,
        osno_percent=osno_rate,
        tax_system=effective_tax_system,
    )
    current_unit_margin = current_unit_profit["margin"] if current_unit_profit else None
    if orders_count == 0 and current_advertising_spend > 0:
        current_unit_margin = -current_advertising_spend
    period_margin = (
        unit_economics_1c.money(current_unit_margin * period_orders_count)
        if current_unit_margin is not None
        else None
    )
    if period_margin is None:
        period_roi = None
    elif period_purchase_value and period_purchase_value > 0:
        period_roi = unit_economics_1c.money(period_margin / period_purchase_value * 100)
    else:
        period_roi = 0.0
    current_unit_roi = (
        unit_economics_1c.money(current_unit_margin / purchase_price * 100)
        if current_unit_margin is not None
        and purchase_price is not None
        and purchase_price > 0
        and orders_count > 0
        else None
    )
    if closed_period_economics is not None:
        period_margin = closed_period_economics.get("margin")
        period_purchase_value = closed_period_economics.get("purchase_value")
        period_roi = closed_period_economics.get("roi")
    stock_state = unit_economics_1c.classify_stock_state(
        total_stock,
        stock_orders_count,
        product_reference.get("stock_status"),
        period_days=stock_loaded_days or stock_period_days,
    )
    age_today = sales_age_today or datetime.now(MOSCOW_TIMEZONE).date()
    first_sale_day = _optional_day(first_sale_at)
    card_created_at = str(product_reference.get("card_created_at") or "").strip() or None
    card_created_day = _optional_day(card_created_at)
    if first_sale_day is not None and first_sale_day <= age_today:
        sales_days = max((age_today - first_sale_day).days + 1, 1)
        is_new = sales_days <= NEW_PRODUCT_MAX_SALES_DAYS
    elif card_created_day is not None and card_created_day <= age_today:
        sales_days = 0
        is_new = (age_today - card_created_day).days < NEW_PRODUCT_MAX_SALES_DAYS
    else:
        sales_days = None
        is_new = False

    return {
        "id": f"{store_slug}:{article}",
        "data_errors": data_errors,
        "store_slug": store_slug,
        "store_name": store["name"],
        "store_initials": store["initials"],
        "store_color": store["color"],
        "store_text": store["text"],
        "article": article,
        "barcode": str(product.get("barcode") or "").strip(),
        "name": str(product.get("name") or article).strip(),
        "image_url": str(product.get("image_url") or "").strip(),
        "manager": str(product_reference.get("manager") or "").strip() or None,
        "rating": _price_value(reputation.get("rating")),
        "reviews_count": _optional_integer(reputation.get("reviews_count")),
        "sales_days": sales_days,
        "sales_started_at": first_sale_day.isoformat() if first_sale_day is not None else None,
        "card_created_at": card_created_at,
        "is_new": is_new,
        "glued_products": glued_products,
        "tag": str(product_reference.get("tag_raw") or "").strip() or None,
        "tag_data": {
            "goal_week": _price_value(product_reference.get("goal_week")),
            "goal_day": _price_value(product_reference.get("goal_day")),
            "status": str(product_reference.get("stock_status") or "").strip() or None,
            "ends": str(product_reference.get("stock_end_week") or "").strip() or None,
            "code": abc_code,
            "fact": _price_value(product_reference.get("fact_sales")),
            "plan": _price_value(product_reference.get("plan_sales")),
        },
        "advertising": {
            "spend": (
                period_advertising_spend
                if product_metrics.get("advertising_coverage") is None
                or product_metrics["advertising_coverage"]["days"] > 0
                else None
            ),
            "average_daily_spend": average_daily_advertising,
            "spend_per_order": advertising_per_unit,
            "impressions": int(product_metrics.get("impressions") or 0),
            "clicks": int(product_metrics.get("clicks") or 0),
            "ctr": unit_economics_1c.money(float(product_metrics.get("ctr") or 0))
            if product_metrics.get("advertising_coverage") is None
            or product_metrics["advertising_coverage"]["days"] > 0
            else None,
            "cpc": unit_economics_1c.money(float(product_metrics.get("cpc") or 0))
            if product_metrics.get("advertising_coverage") is None
            or product_metrics["advertising_coverage"]["days"] > 0
            else None,
            "drr": period_drr
            if product_metrics.get("drr_coverage") is None or product_metrics["drr_coverage"]["days"] > 0
            else None,
            "orders": int(product_metrics.get("orders_count") or 0),
            "sold": int(product_metrics.get("sold_count") or 0),
            "buyout_percent": period_buyout_percent,
            "raw_buyout_percent": product_metrics.get("raw_buyout_percent"),
            "buyout_default_applied": product_metrics.get("buyout_default_applied", False),
            "buyout_orders_count": int(product_metrics.get("buyout_orders_count") or 0),
            "buyout_period_from": product_metrics.get("buyout_period_from"),
            "buyout_period_to": product_metrics.get("buyout_period_to"),
            "buyout_updated_at": product_metrics.get("buyout_updated_at"),
            "orders_amount": unit_economics_1c.money(
                float(product_metrics.get("drr_orders_amount", product_metrics.get("orders_amount")) or 0)
            ),
            "drr_spend": unit_economics_1c.money(
                float(product_metrics.get("drr_spend", period_advertising_spend) or 0)
            ),
            "cancel_count": int(product_metrics.get("cancel_count") or 0),
            "cancel_amount": unit_economics_1c.money(float(product_metrics.get("cancel_amount") or 0)),
            "net_orders_count": int(product_metrics.get("net_orders_count") or 0),
            "net_orders_amount": unit_economics_1c.money(
                float(product_metrics.get("net_orders_amount") or 0)
            ),
            "funnel_updated_at": product_metrics.get("funnel_updated_at"),
            "funnel_source_version": product_metrics.get("funnel_source_version"),
            "funnel_vendor_code": product_metrics.get("funnel_vendor_code"),
            "funnel_product_name": product_metrics.get("funnel_product_name"),
            "buyout_orders_amount": unit_economics_1c.money(
                float(product_metrics.get("buyout_orders_amount") or 0)
            ),
            "buyout_cancel_count": int(product_metrics.get("buyout_cancel_count") or 0),
            "buyout_cancel_amount": unit_economics_1c.money(
                float(product_metrics.get("buyout_cancel_amount") or 0)
            ),
            "buyout_net_orders_count": int(product_metrics.get("buyout_net_orders_count") or 0),
            "buyout_net_orders_amount": unit_economics_1c.money(
                float(product_metrics.get("buyout_net_orders_amount") or 0)
            ),
            "buyout_source_version": product_metrics.get("buyout_source_version"),
            "period_from": product_metrics.get("period_from"),
            "period_to": product_metrics.get("period_to"),
            "period_days": int(product_metrics.get("period_days") or 7),
            "coverage": product_metrics.get("advertising_coverage"),
            "drr_coverage": product_metrics.get("drr_coverage"),
        },
        "economics_7d": {
            "turnover": (
                period_turnover
                if turnover_coverage is None or int(turnover_coverage.get("days") or 0) > 0
                else None
            ),
            "margin": period_margin,
            "roi": period_roi,
            "purchase_value": period_purchase_value,
            "orders": int(product_metrics.get("orders_count") or 0),
            "period_from": product_metrics.get("period_from"),
            "period_to": product_metrics.get("period_to"),
            "complete": (
                bool(closed_period_economics.get("complete"))
                if closed_period_economics is not None
                else period_margin is not None
            ),
            "messages": (closed_period_economics or {}).get("messages", []),
            "roi_purchase_value": (closed_period_economics or {}).get("roi_purchase_value"),
            "turnover_coverage": turnover_coverage,
            "margin_coverage": (
                closed_period_economics.get("coverage") if closed_period_economics is not None else None
            ),
            "roi_coverage": (
                closed_period_economics.get("coverage") if closed_period_economics is not None else None
            ),
        },
        "current_economics": {
            "margin": current_unit_margin,
            "roi": current_unit_roi,
            "orders": int(current_product_metrics.get("orders_count") or 0),
            "buyout_percent": current_buyout_percent,
            "purchase_value": purchase_price,
            "advertising_spend": current_advertising_spend,
            "period_from": current_product_metrics.get("period_from"),
            "period_to": current_product_metrics.get("period_to"),
            "complete": current_unit_margin is not None,
        },
        "stock": {
            "fbs": fbs_stock,
            "fbo": fbo_stock,
            "fulfillment": fulfillment_stock,
            "total": total_stock,
            "inbound": inbound_quantity,
            "inbound_partial": inbound_partial,
            "inbound_message": inbound_message,
            "days": stock_days,
            "orders_21d": stock_orders_count if stock_loaded_days else None,
            "average_daily_orders": average_daily_orders,
            "coverage": stock_coverage,
            "period_days": stock_period_days,
            "period_from": stock_order_metrics.get("period_from"),
            "period_to": stock_order_metrics.get("period_to"),
            "state": stock_state,
        },
        "price": {
            "base": _price_value(price_snapshot.get("seller_base_price")),
            "current": current_price,
            "club": _price_value(price_snapshot.get("club_discounted_price")),
            "with_spp": spp_price,
            "with_wallet": wallet_price,
            "snapshot_date": price_snapshot.get("day"),
            "window_days": price_snapshot.get("customer_price_window_days"),
            "orders_count": price_snapshot.get("customer_price_orders_count"),
            "retail_synced_at": price_snapshot.get("retail_synced_at"),
            "orders_synced_at": price_snapshot.get("orders_synced_at"),
        },
        "details": {
            "subject": category,
            "commission_scheme": ("СУ + доп. тариф WB" if has_subject_commission else None),
            "drr": None,
            "planned_advertising": None,
            "purchase_cost": purchase_price,
            "fulfillment_cost": fulfillment_cost,
            "vat_percent": vat_rate,
            "usn_percent": usn_rate,
            "osno_percent": osno_rate,
            "tax_system": effective_tax_system,
            "acquiring": unit_economics_1c.money(acquiring_percent),
            "acquiring_value": (current_unit_profit.get("acquiring") if current_unit_profit else None),
            "team_commission_percent": effective_team_commission,
            "buyout_percent": current_buyout_percent,
            "logistics_type": None,
            "actual_advertising": current_advertising_spend,
            "advertising_per_unit": current_advertising_per_unit,
            "retail_price_used": economics_retail_price,
            "customer_price_used": calculation_price,
            "customer_price_source": calculation_price_source,
            "average_order_price": average_customer_price,
            "delivery_wb_rub": product_settings.delivery_wb_rub,
            "return_cost_rub": product_settings.return_cost_rub,
            "volume_l": product_settings.volume_l,
            "paid_acceptance_cost": paid_acceptance_cost,
            "acceptance_coefficient": unit_economics_1c.money(float(acceptance_coefficient or 0)),
            "delivery_with_returns": current_logistics,
            "storage_wb_rub": product_settings.storage_wb_rub,
            "storage_days": turnover_days,
            "storage_sum": storage_sum,
            "irp_percent": None,
            "spp_price": spp_price,
            "subject_commission_percent": subject_commission_percent,
            "wb_extra_tariff_percent": extra_tariff_percent,
            "commission_percent": commission_percent,
            "commission_value": commission_value,
            "logistics": current_logistics,
            "vat_value": unit_economics_1c.money(tax_components["vat"]) if tax_components else None,
            "usn_value": unit_economics_1c.money(tax_components["usn"]) if tax_components else None,
            "osno_value": unit_economics_1c.money(tax_components["osno"]) if tax_components else None,
            "tax_value": unit_economics_1c.money(tax_components["total"]) if tax_components else None,
            "team_commission_value": (
                current_unit_profit.get("team_commission") if current_unit_profit else None
            ),
            "net_revenue": current_unit_profit.get("net_revenue") if current_unit_profit else None,
            "advertising_value": (current_unit_profit.get("advertising") if current_unit_profit else None),
            "margin_rub": current_unit_margin,
            "roi": current_unit_roi,
        },
        "product_settings": product_settings.model_dump(mode="json"),
        "history": history,
    }


def _unit_economics_1c_product_summary(product: dict) -> dict:
    """Keep only fields used by the product table and its client-side filters."""
    summary = {
        key: product.get(key)
        for key in (
            "id",
            "data_errors",
            "store_slug",
            "store_name",
            "article",
            "barcode",
            "name",
            "image_url",
            "rating",
            "reviews_count",
            "sales_days",
            "is_new",
            "tag",
            "tag_data",
        )
    }
    summary["advertising"] = {
        key: (product.get("advertising") or {}).get(key)
        for key in (
            "drr",
            "spend",
            "ctr",
            "cpc",
            "impressions",
            "clicks",
            "coverage",
            "drr_coverage",
            "buyout_percent",
            "period_from",
            "period_to",
            "orders_amount",
            "drr_spend",
        )
    }
    summary["economics_7d"] = {
        key: (product.get("economics_7d") or {}).get(key)
        for key in (
            "turnover",
            "margin",
            "roi",
            "turnover_coverage",
            "margin_coverage",
            "roi_coverage",
            "roi_purchase_value",
            "messages",
            "purchase_value",
            "complete",
        )
    }
    summary["current_economics"] = {
        key: (product.get("current_economics") or {}).get(key)
        for key in (
            "margin",
            "roi",
            "orders",
            "buyout_percent",
            "advertising_spend",
            "purchase_value",
            "period_to",
            "complete",
            "missing",
            "messages",
            "status",
            "issues",
            "day_profit",
            "expected_buyouts",
            "daily_complete",
            "daily_messages",
            "day_purchase_value",
        )
    }
    summary["price"] = {key: (product.get("price") or {}).get(key) for key in ("current", "with_spp")}
    summary["stock"] = {
        key: (product.get("stock") or {}).get(key)
        for key in (
            "fbs",
            "fbo",
            "fulfillment",
            "total",
            "days",
            "orders_21d",
            "average_daily_orders",
            "period_days",
            "state",
            "coverage",
            "inbound",
            "inbound_partial",
            "inbound_message",
        )
    }
    return summary


def _unit_economics_1c_price_warnings(store_slugs: tuple[str, ...]) -> list[dict]:
    states = {str(row["store_slug"]): row for row in db.list_unit_economics_1c_price_sync_states(store_slugs)}
    warnings: list[dict] = []
    for store_slug in store_slugs:
        state = states.get(store_slug)
        if state is not None and state.get("status") != "error":
            continue
        message = (
            str(state.get("error") or "цены обновились не полностью")
            if state is not None
            else "цены ещё не синхронизировались"
        )
        warnings.append(
            {
                "store_slug": store_slug,
                "store_name": STORES[store_slug]["name"],
                "status": str(state.get("status") or "error") if state else "error",
                "message": message,
            }
        )
    return warnings


def load_wb_calculator_products(
    store_slugs: tuple[str, ...],
    request_today: date,
    period_days: int,
    closed_period_from: date,
    closed_period_to: date,
    include_history: bool = False,
    detail_store: str = "",
    detail_article: str = "",
    include_inactive: bool = False,
) -> tuple[list[dict], list[dict]]:
    source_states = data_errors_repository.source_states(store_slugs)
    latest_rows = db.get_unit_economics_1c_latest_daily_prices(store_slugs)
    prices = {(str(row["store_slug"]), str(row["article"])): row for row in latest_rows}
    today = request_today
    history_from = (
        min(today - timedelta(days=20), closed_period_from) if include_history else closed_period_from
    )
    metrics = unit_economics_1c.load_product_metrics(
        store_slugs,
        period_days=period_days,
        today=closed_period_to,
        article=_nm_id(detail_article) if detail_article else None,
    )
    current_metrics = unit_economics_1c.load_product_metrics(
        store_slugs,
        period_days=1,
        today=today,
        article=_nm_id(detail_article) if detail_article else None,
    )
    chart_metrics = (
        unit_economics_1c.load_product_metrics(
            store_slugs, period_days=21, article=_nm_id(detail_article) if detail_article else None
        )
        if include_history
        else {}
    )
    chart_prices = (
        {
            str(row["day"]): row
            for row in db.get_unit_economics_1c_daily_price_history(detail_store, detail_article, 21)
        }
        if include_history
        else {}
    )
    saved_funnel_daily_rows = db.get_unit_economics_1c_funnel_daily_order_rows(
        store_slugs,
        history_from.isoformat(),
        today.isoformat(),
        article=_nm_id(detail_article) if detail_article else None,
    )
    source_coverage = wb_days(store_slugs, history_from.isoformat(), today.isoformat())
    advertising_days_by_store = {store: days["advertising"] for store, days in source_coverage.items()}
    daily_orders_by_product: dict[tuple[str, str], dict[str, dict]] = {}
    funnel_days_by_store = {store: days["orders"] for store, days in source_coverage.items()}
    for row in saved_funnel_daily_rows:
        daily_orders_by_product.setdefault(
            (str(row["store_slug"]), str(row["article"])),
            {},
        )[str(row["day"])] = row
    stock_order_metrics = unit_economics_1c.load_product_average_daily_orders(
        store_slugs,
        period_days=unit_economics_1c.STOCK_COVERAGE_PERIOD_DAYS,
    )
    settings = {item.store_slug: item for item in db.list_unit_economics_1c_cabinet_settings(store_slugs)}
    product_settings = {
        (item.store_slug, item.article): item
        for item in db.list_unit_economics_1c_product_settings(store_slugs)
    }
    product_references = {
        (str(item["store_slug"]), str(item["article"])): item
        for item in db.get_unit_economics_1c_product_reference_rows(store_slugs)
    }
    reputations = {
        (str(item["store_slug"]), _nm_id(item["article"])): item
        for item in db.get_unit_economics_1c_latest_product_reputation(store_slugs)
    }
    sales_starts = {
        (str(item["store_slug"]), _nm_id(item["article"])): str(item["first_sale_at"])
        for item in db.get_unit_economics_1c_product_sales_starts(store_slugs)
    }
    catalogs = {store_slug: db.get_stock_items(store_slug, "WB") for store_slug in store_slugs}
    inbound_by_store = {
        store_slug: stock_sheet_inbound.load(store_slug, "WB", catalog)
        for store_slug, catalog in catalogs.items()
    }
    catalogs = {store_slug: inbound.catalog for store_slug, inbound in inbound_by_store.items()}
    # Skip only products guaranteed to be hidden by the final activity
    # filter. Keep the full catalog for glue groups and direct detail views.
    report_catalogs = {}
    for store_slug, catalog in catalogs.items():
        inbound = inbound_by_store[store_slug]
        report_catalogs[store_slug] = [
            item
            for item in catalog
            if (
                str(item["article"]) == detail_article
                if detail_article
                else (
                    include_inactive
                    or any(
                        (_optional_integer(item.get(field)) or 0) > 0
                        for field in ("fbs_stock", "fbo_stock", "ff_available")
                    )
                    or (inbound.quantities.get(str(item["article"])) or 0) > 0
                    or (inbound.confirmed_quantities.get(str(item["article"])) or 0) > 0
                    or any(
                        (metrics.get((store_slug, _nm_id(item["article"]))) or {}).get(field)
                        for field in ("orders_amount", "cancel_amount")
                    )
                )
            )
        ]
    report_articles = tuple(
        sorted({str(item["article"]) for catalog in report_catalogs.values() for item in catalog})
    )
    saved_margin_snapshots = db.get_unit_economics_1c_daily_margin_snapshots(
        store_slugs,
        history_from.isoformat(),
        today.isoformat(),
        articles=report_articles,
        inputs_only=True,
    )
    margin_snapshots_by_product: dict[tuple[str, str], dict[str, dict]] = {}
    for row in saved_margin_snapshots:
        margin_snapshots_by_product.setdefault(
            (str(row["store_slug"]), str(row["article"])),
            {},
        )[str(row["day"])] = row
    glue_groups: dict[tuple[str, int], list[dict]] = {}
    for store_slug, catalog in catalogs.items():
        for item in catalog:
            article = str(item.get("article") or "")
            imt_id = _optional_integer((product_references.get((store_slug, article)) or {}).get("imt_id"))
            if imt_id is None:
                continue
            glue_groups.setdefault((store_slug, imt_id), []).append(
                {
                    "article": article,
                    "name": str(item.get("name") or article),
                }
            )
    history_to = today
    stock_history_rows = (
        db.get_daily_stock_history(
            store_slugs,
            "WB",
            (history_to - timedelta(days=20)).isoformat(),
            history_to.isoformat(),
            article=detail_article or None,
        )
        if include_history
        else []
    )
    stock_history = {}
    for item in stock_history_rows:
        key = (str(item["store_slug"]), str(item["article"]))
        stock_history.setdefault(key, {})[str(item["day"])] = item
    products: list[dict] = []
    for store_slug in store_slugs:
        cabinet = settings[store_slug]
        catalog = report_catalogs[store_slug]
        inbound = inbound_by_store[store_slug]
        for product in catalog:
            article = str(product.get("article") or "")
            if detail_article and article != detail_article:
                continue
            nm_id = _nm_id(article)
            reference = product_references.get((store_slug, article)) or {}
            effective_product_settings = product_settings.get((store_slug, article))
            if effective_product_settings is None:
                effective_product_settings = UnitEconomics1CProductSettings(
                    store_slug=store_slug,
                    article=article,
                )
            period_product_metrics = metrics.get((store_slug, nm_id))
            if period_product_metrics is None:
                period_product_metrics = unit_economics_1c.empty_product_metrics(
                    period_days=period_days,
                    today=closed_period_to,
                )
            current_product_metrics = current_metrics.get((store_slug, nm_id))
            if current_product_metrics is None:
                current_product_metrics = unit_economics_1c.empty_product_metrics(
                    period_days=1,
                    today=today,
                )
            history_product_metrics = chart_metrics.get((store_slug, nm_id))
            if history_product_metrics is None and include_history:
                history_product_metrics = unit_economics_1c.empty_product_metrics(
                    period_days=21,
                    today=today,
                )
            elif history_product_metrics is None:
                history_product_metrics = period_product_metrics
            period_product_metrics = unit_economics_1c.apply_buyout_default(
                period_product_metrics,
                cabinet.default_buyout_percent,
            )
            current_product_metrics = unit_economics_1c.apply_buyout_default(
                current_product_metrics,
                cabinet.default_buyout_percent,
            )
            history_product_metrics = unit_economics_1c.apply_buyout_default(
                history_product_metrics,
                cabinet.default_buyout_percent,
            )
            product_margin_snapshots = margin_snapshots_by_product.get((store_slug, article)) or {}
            live_snapshot = (
                unit_economics_1c_history.calculate_snapshot_row(
                    snapshot_day=today,
                    store_slug=store_slug,
                    article=article,
                    price_snapshot=prices.get((store_slug, article)) or {},
                    product_metrics=current_product_metrics,
                    product_settings=effective_product_settings,
                    product_reference=reference,
                    cabinet=cabinet,
                    captured_at=datetime.now(UTC).isoformat(),
                )
                if today.isoformat() not in product_margin_snapshots
                else None
            )
            first_sale_day = _optional_day(sales_starts.get((store_slug, nm_id)))
            turnover_coverage_from = closed_period_from
            if first_sale_day is not None and first_sale_day > turnover_coverage_from:
                turnover_coverage_from = first_sale_day
            turnover_coverage = _period_coverage(
                [
                    day_value
                    for day_value in funnel_days_by_store.get(store_slug, set())
                    if (
                        (parsed_day := _optional_day(day_value)) is not None
                        and turnover_coverage_from <= parsed_day <= closed_period_to
                    )
                ],
                closed_period_from,
                closed_period_to,
            )
            advertising_coverage = _period_coverage(
                advertising_days_by_store.get(store_slug, set()),
                closed_period_from,
                closed_period_to,
            )
            matching_days = set(turnover_coverage["dates"]) & set(advertising_coverage["dates"])
            drr_coverage = _period_coverage(matching_days, closed_period_from, closed_period_to)
            period_product_metrics["advertising_coverage"] = advertising_coverage
            period_product_metrics["drr_coverage"] = drr_coverage
            daily_period = [row for row in period_product_metrics.get("daily") or [] if isinstance(row, dict)]
            period_product_metrics["drr_spend"] = sum(
                float(row.get("advertising_spend") or 0)
                for row in daily_period
                if row.get("date") in matching_days
            )
            period_product_metrics["drr_orders_amount"] = sum(
                float(row.get("orders_amount") or 0)
                for row in daily_period
                if row.get("date") in matching_days
            )
            if not turnover_coverage["complete"]:
                for field, day_field in (
                    ("orders_amount", "orders_amount"),
                    ("cancel_amount", "cancel_amount"),
                    ("orders_count", "orders_count"),
                    ("cancel_count", "cancel_count"),
                ):
                    period_product_metrics[field] = sum(
                        float(row.get(day_field) or 0)
                        for row in daily_period
                        if row.get("date") in turnover_coverage["dates"]
                    )
                period_product_metrics["net_orders_amount"] = (
                    period_product_metrics["orders_amount"] - period_product_metrics["cancel_amount"]
                )
                period_product_metrics["net_orders_count"] = (
                    period_product_metrics["orders_count"] - period_product_metrics["cancel_count"]
                )
            product_daily_orders = daily_orders_by_product.get((store_slug, nm_id)) or {}
            history_day_economics: dict[str, dict] = {}
            for item in history_product_metrics.get("daily") or [] if include_history else []:
                if not isinstance(item, dict):
                    continue
                history_day = _optional_day(item.get("date"))
                if history_day is None:
                    continue
                history_day_economics[history_day.isoformat()] = _report_historical_economics(
                    orders_days=source_coverage[store_slug]["orders"],
                    advertising_days=source_coverage[store_slug]["advertising"],
                    date_from=history_day,
                    date_to=history_day,
                    daily_orders=product_daily_orders,
                    margin_snapshots=product_margin_snapshots,
                    live_day=today,
                    live_snapshot=live_snapshot,
                    live_unit_margin=(
                        _price_value(live_snapshot.get("unit_margin")) if live_snapshot else None
                    ),
                    live_purchase_price=(
                        _price_value(live_snapshot.get("purchase_price")) if live_snapshot else None
                    ),
                    daily_advertising={
                        history_day.isoformat(): max(
                            float(item.get("advertising_spend") or 0),
                            0.0,
                        )
                    },
                    fallback_buyout_percent=history_product_metrics.get("buyout_percent"),
                    ignore_deferred_cost_warnings=True,
                )
            period_daily_advertising = {
                str(item.get("date")): max(float(item.get("advertising_spend") or 0), 0.0)
                for item in period_product_metrics.get("daily") or []
                if isinstance(item, dict) and item.get("date")
            }
            closed_period_economics = _report_historical_economics(
                date_from=closed_period_from,
                date_to=closed_period_to,
                daily_orders=product_daily_orders,
                margin_snapshots=product_margin_snapshots,
                live_day=today,
                live_unit_margin=None,
                live_purchase_price=None,
                daily_advertising=period_daily_advertising,
                orders_days=source_coverage[store_slug]["orders"],
                advertising_days=source_coverage[store_slug]["advertising"],
                fallback_buyout_percent=period_product_metrics.get("buyout_percent"),
                allow_partial=True,
                ignore_deferred_cost_warnings=True,
            )
            inbound_quantity = inbound.quantities.get(article)
            inbound_partial = inbound_quantity is None or not inbound.available
            if inbound_partial:
                inbound_quantity = inbound.confirmed_quantities.get(article) or None
            inbound_message = (
                "Нет полного свежего снимка поставок: количество в пути не подтверждено."
                if not inbound.available
                else "Часть количеств в поставках неизвестна."
                if inbound_partial
                else "Отправлено, ещё не принято на склад FBO."
            )
            imt_id = _optional_integer(reference.get("imt_id"))
            glued_products = (
                [item for item in glue_groups.get((store_slug, imt_id), []) if item["article"] != article]
                if imt_id is not None
                else []
            )
            result = _unit_economics_1c_mock_product(
                store_slug=store_slug,
                product=product,
                source_states=source_states.get(store_slug, {}),
                price_snapshot=prices.get((store_slug, article)),
                acquiring_percent=cabinet.acquiring_percent,
                default_buyout_percent=cabinet.default_buyout_percent,
                product_metrics=period_product_metrics,
                current_product_metrics=current_product_metrics,
                closed_period_economics=closed_period_economics,
                turnover_coverage=turnover_coverage,
                history_product_metrics=history_product_metrics,
                history_day_economics=history_day_economics,
                history_prices_by_day=chart_prices,
                product_settings=effective_product_settings,
                acceptance_coefficient=cabinet.acceptance_coefficient,
                team_commission_percent=cabinet.team_commission_percent,
                vat_percent=cabinet.vat_percent,
                usn_percent=cabinet.usn_percent,
                osno_percent=cabinet.osno_percent,
                tax_system=cabinet.tax_system,
                product_reference=reference,
                wb_extra_tariff_percent=cabinet.wb_extra_tariff_percent,
                stock_history_by_day=stock_history.get((store_slug, article)),
                stock_order_metrics=stock_order_metrics.get((store_slug, nm_id)),
                reputation=reputations.get((store_slug, nm_id)),
                glued_products=glued_products,
                first_sale_at=sales_starts.get((store_slug, nm_id)),
                history_days=21 if include_history else 0,
                inbound_quantity=inbound_quantity,
                inbound_partial=inbound_partial,
                inbound_message=inbound_message,
            )
            today_key = today.isoformat()
            current_snapshot = product_margin_snapshots.get(today_key) or live_snapshot
            current_day = unit_economics_1c_history.report_day(
                today_key,
                current_snapshot,
                product_daily_orders.get(today_key) or {},
                current_product_metrics.get("spend"),
                orders_known=today_key in source_coverage[store_slug]["orders"],
                ads_known=today_key in source_coverage[store_slug]["advertising"],
                ignore_deferred_cost_warnings=True,
            )
            current_values, current_result = current_day["inputs"], dict(current_day["result"])
            count, spend, bought = (
                current_day["orders_count"],
                current_day["advertising_spend"],
                current_day["expected_buyouts"],
            )
            current_missing = list(current_result.get("missing", []))
            if spend is None:
                current_missing.append("advertising_spend")
            elif spend and (count is None or (count != 0 and not bought)):
                current_missing.append("orders_count" if count is None else "buyout_percent")
            if count == 0 and spend is not None and spend > 0:
                current_result.update(margin=-spend, roi=None)
                current_missing = []
            elif current_result.get("margin") is not None:
                if spend is not None and bought:
                    current_result["margin"] = unit_economics_1c.money(
                        current_result["margin"] - spend / bought
                    )
                basis = current_values.get("purchase_price")
                current_result["roi"] = (
                    unit_economics_1c.money(current_result["margin"] / basis * 100) if basis else None
                )
            annotate(current_result, current_missing)
            current_messages = (
                ["Не учтены / неизвестны: " + ", ".join(describe(current_missing, today_key))]
                if current_missing
                else []
            )
            result["current_economics"].update(
                margin=current_result.get("margin"),
                roi=current_result.get("roi"),
                orders=count,
                advertising_spend=spend,
                complete=current_result["complete"],
                status=current_result["status"],
                purchase_value=current_values.get("purchase_price"),
                buyout_percent=current_values.get("buyout_percent"),
                day_profit=current_day["profit"],
                expected_buyouts=bought,
                daily_complete=current_day["complete"],
                daily_messages=current_day["messages"],
                day_purchase_value=current_day["purchase_value"],
                missing=current_missing,
                messages=current_messages,
                issues={
                    "margin": current_messages,
                    "roi": current_messages
                    + (
                        ["Нет положительной закупочной цены для ROI"]
                        if not current_values.get("purchase_price")
                        else []
                    ),
                },
            )
            for key, value in (
                ("purchase_cost", "purchase_price"),
                ("fulfillment_cost", "fulfillment_cost"),
            ):
                result["details"][key] = current_values.get(value)
            if cabinet.updated_at is None:
                result["data_errors"].append("Не заполнены настройки кабинета WB")
            for label, coverage in (
                ("заказов", turnover_coverage),
                ("рекламы", advertising_coverage),
            ):
                if not coverage["complete"]:
                    result["data_errors"].append(
                        f"Не все дни {label} загружены: {', '.join(coverage['missing_dates'])}"
                    )
            if inbound_partial:
                result["data_errors"].append(inbound_message)
            stock_coverage = result["stock"].get("coverage")
            if stock_coverage is None:
                result["data_errors"].append("Нет дневных заказов для расчёта запаса в днях")
            elif not stock_coverage["complete"]:
                result["data_errors"].append(
                    "Не все дни заказов для запаса загружены: " + ", ".join(stock_coverage["missing_dates"])
                )
            if (
                include_inactive
                or detail_article
                or unit_economics_yandex.has_activity(result["stock"], result["economics_7d"]["turnover"])
            ):
                products.append(result)

    return products, _unit_economics_1c_price_warnings(store_slugs)
