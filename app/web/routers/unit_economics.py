import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app import (
    db,
    unit_economics_1c,
    unit_economics_1c_history,
    unit_economics_1c_prices,
    unit_economics_1c_report_export,
)
from app import unit_economics_1c_source_data as unit_economics_1c_source
from app.access_control import accessible_stores, has_scope
from app.domain import MOSCOW_TIMEZONE
from app.dto.identity import Role, SectionAccessLevel, SectionName, coerce_user
from app.dto.unit_economics_1c import (
    UnitEconomics1CCabinetSettingsWebRequest,
    UnitEconomics1CColumnPreferencesRequest,
    UnitEconomics1CPriceChangeRequest,
    UnitEconomics1CProductSettings,
    UnitEconomics1CProductSettingsRequest,
)
from app.section_access import has_access as has_section_access
from app.stores import STORES
from app.sync_tracking import run_tracked
from app.web.downloads import _download_headers
from app.web.templating import fill_template, render_page

router = APIRouter()
logger = logging.getLogger(__name__)
PRICE_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ue1c-price")
NEW_PRODUCT_MAX_SALES_DAYS = 28
UNIT_ECONOMICS_COLUMNS_PREFERENCE_SCOPE = "unit_economics_1c.columns"
UNIT_ECONOMICS_COLUMN_GROUPS = (
    "product",
    "newness",
    "comments",
    "current",
    "actual",
    "advertising",
    "tag",
    "stock",
)


def _cabinet_settings_payload(store_slugs: tuple[str, ...]) -> list[dict]:
    settings = db.list_unit_economics_1c_cabinet_settings(store_slugs)
    return [
        {
            **item.model_dump(mode="json"),
            "store_name": STORES[item.store_slug]["name"],
            "store_initials": STORES[item.store_slug]["initials"],
            "store_color": STORES[item.store_slug]["color"],
            "store_text": STORES[item.store_slug]["text"],
        }
        for item in settings
    ]


def _price_value(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
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
    expected_dates = {
        (date_from + timedelta(days=offset)).isoformat()
        for offset in range(expected_days)
    }
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
    fallback_buyout_percent: float | None = None,
    allow_partial: bool = False,
) -> dict:
    """Sum daily expected-buyout profit without substituting current values for history."""

    margin = 0.0
    purchase_value = 0.0
    calculated_buyouts = 0.0
    buyout_orders_count = 0
    weighted_buyout_percent = 0.0
    missing_days: list[str] = []
    covered_days: list[str] = []
    advertising_spend = 0.0
    expected_buyout_amount = 0.0
    daily_advertising = daily_advertising or {}
    current = date_from
    while current <= date_to:
        day_key = current.isoformat()
        daily_row = daily_orders.get(day_key) or {}
        orders_count = max(int(daily_row.get("orders_count") or 0), 0)
        snapshot = margin_snapshots.get(day_key)
        raw_buyout_percent = daily_row.get("buyout_percent")
        if raw_buyout_percent is None or float(raw_buyout_percent) <= 0:
            raw_buyout_percent = unit_economics_1c_history.snapshot_buyout_percent(snapshot)
            if raw_buyout_percent is None:
                raw_buyout_percent = fallback_buyout_percent
            if raw_buyout_percent is None or float(raw_buyout_percent) <= 0:
                raw_buyout_percent = 100.0
        buyout_percent = min(max(float(raw_buyout_percent), 0.0), 100.0)
        expected_buyouts = orders_count * buyout_percent / 100
        expected_buyout_amount += (
            max(float(daily_row.get("orders_amount") or 0), 0.0)
            * buyout_percent
            / 100
        )
        day_advertising = max(float(daily_advertising.get(day_key) or 0), 0.0)
        if snapshot is not None:
            unit_margin = unit_economics_1c_history.unit_margin_without_advertising(
                snapshot,
                buyout_percent=buyout_percent,
            )
            purchase_price = _price_value(snapshot.get("purchase_price"))
        elif current == live_day:
            unit_margin = live_unit_margin
            purchase_price = live_purchase_price
        else:
            unit_margin = None
            purchase_price = None
        if allow_partial:
            if unit_margin is None or purchase_price is None:
                missing_days.append(day_key)
            else:
                covered_days.append(day_key)
                weighted_buyout_percent += buyout_percent * orders_count
                buyout_orders_count += orders_count
                advertising_spend += day_advertising
                margin += unit_margin * expected_buyouts - day_advertising
                purchase_value += purchase_price * expected_buyouts
                calculated_buyouts += expected_buyouts
        else:
            weighted_buyout_percent += buyout_percent * orders_count
            buyout_orders_count += orders_count
            advertising_spend += day_advertising
            margin -= day_advertising
            if expected_buyouts > 0:
                if unit_margin is None or purchase_price is None:
                    missing_days.append(day_key)
                else:
                    margin += unit_margin * expected_buyouts
                    purchase_value += purchase_price * expected_buyouts
                    calculated_buyouts += expected_buyouts
            if unit_margin is not None and purchase_price is not None:
                covered_days.append(day_key)
        current += timedelta(days=1)

    coverage = _period_coverage(covered_days, date_from, date_to)
    complete = coverage["complete"] if allow_partial else not missing_days
    has_result = bool(covered_days) if allow_partial else complete
    rounded_margin = round(margin, 2) if has_result else None
    rounded_purchase = round(purchase_value, 2) if has_result else None
    roi = (
        round(rounded_margin / rounded_purchase * 100, 2)
        if has_result and rounded_purchase and rounded_purchase > 0
        else 0.0
        if has_result
        else None
    )
    return {
        "margin": rounded_margin,
        "purchase_value": rounded_purchase,
        "roi": roi,
        "orders": round(calculated_buyouts, 2),
        "advertising_spend": round(advertising_spend, 2),
        "expected_buyout_amount": round(expected_buyout_amount, 2),
        "drr": (
            round(advertising_spend / expected_buyout_amount * 100, 2)
            if expected_buyout_amount > 0
            else 100.0
            if advertising_spend > 0
            else 0.0
        ),
        "buyout_percent": (
            round(weighted_buyout_percent / buyout_orders_count, 2)
            if buyout_orders_count
            else None
        ),
        "buyout_orders_count": buyout_orders_count,
        "complete": complete,
        "missing_days": missing_days,
        "coverage": coverage,
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
) -> dict:
    """Build the 1C layout while keeping WB prices, orders and advertising real."""
    article = str(product.get("article") or "").strip()
    price_snapshot = price_snapshot or {}
    product_metrics = product_metrics or unit_economics_1c.empty_product_metrics()
    current_product_metrics = current_product_metrics or product_metrics
    history_product_metrics = history_product_metrics or product_metrics
    history_day_economics = history_day_economics or {}
    product_settings = product_settings or UnitEconomics1CProductSettings(
        store_slug=store_slug,
        article=article,
    )
    product_reference = product_reference or {}
    stock_history_by_day = stock_history_by_day or {}
    stock_order_metrics = stock_order_metrics or {}
    reputation = reputation or {}
    glued_products = glued_products or []
    spp_price = _price_value(price_snapshot.get("customer_price_with_spp"))
    wallet_price = _price_value(price_snapshot.get("customer_price_with_wallet"))
    current_price = _price_value(price_snapshot.get("retail_price"))
    orders_count = max(int(current_product_metrics.get("orders_count") or 0), 0)
    average_customer_price = (
        round(float(current_product_metrics.get("orders_amount") or 0) / orders_count, 2)
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
    stock_orders_count = max(int(stock_order_metrics.get("orders_count") or 0), 0)
    average_daily_orders = round(stock_orders_count / stock_period_days, 2)
    stock_days = unit_economics_1c.calculate_stock_coverage_days(
        total_stock,
        stock_orders_count,
        period_days=stock_period_days,
    )
    measured_buyout_percent = min(
        max(float(product_metrics.get("buyout_percent") or 0), 0.0),
        100.0,
    )
    raw_current_buyout_percent = current_product_metrics.get("range_buyout_percent")
    if raw_current_buyout_percent is None or float(raw_current_buyout_percent) <= 0:
        raw_current_buyout_percent = current_product_metrics.get("buyout_percent")
    current_buyout_percent = min(
        max(float(raw_current_buyout_percent or 0), 0.0),
        100.0,
    )
    store = STORES[store_slug]
    paid_acceptance_cost = unit_economics_1c.calculate_paid_acceptance_cost(
        product_settings.volume_l,
        acceptance_coefficient,
    )
    logistics = unit_economics_1c.calculate_delivery_with_returns(
        product_settings.delivery_wb_rub,
        measured_buyout_percent,
        product_settings.return_cost_rub,
        paid_acceptance_cost,
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
    if purchase_price is None:
        purchase_price = 0.0
    fulfillment_cost = _price_value(product_reference.get("fulfillment_cost"))
    if fulfillment_cost is None:
        fulfillment_cost = 0.0
    source_team_commission = _price_value(product_reference.get("team_commission_percent"))
    effective_team_commission = (
        source_team_commission if source_team_commission is not None else round(team_commission_percent, 2)
    )
    category = str(product_reference.get("category") or "").strip() or None
    subject_commission_percent = _price_value(product_reference.get("subject_commission_percent"))
    has_subject_commission = subject_commission_percent is not None
    if subject_commission_percent is None:
        subject_commission_percent = 0.0
    extra_tariff_percent = round(max(float(wb_extra_tariff_percent or 0), 0.0), 2)
    commission_percent = round(subject_commission_percent + extra_tariff_percent, 2)
    commission_value = (
        round(economics_retail_price * commission_percent / 100, 2)
        if economics_retail_price is not None
        else None
    )
    storage_sum = (
        round(turnover_days * product_settings.storage_wb_rub, 2) if turnover_days is not None else None
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
    buyout_ratio = measured_buyout_percent / 100
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
    for offset_from_start in range(history_period_days):
        offset = history_period_days - offset_from_start - 1
        day = history_end - timedelta(days=offset)
        day_metrics = metric_history.get(day.isoformat()) or {}
        day_ads = round(float(day_metrics.get("advertising_spend") or 0), 2)
        day_orders_amount = round(float(day_metrics.get("orders_amount") or 0), 2)
        day_orders_count = max(int(day_metrics.get("orders_count") or 0), 0)
        saved_day_economics = history_day_economics.get(day.isoformat())
        if saved_day_economics is not None:
            day_buyout_percent = _price_value(saved_day_economics.get("buyout_percent"))
            if day_buyout_percent is None:
                day_buyout_percent = 0.0
            purchased_units = round(day_orders_count * day_buyout_percent / 100, 2)
            day_drr = unit_economics_1c.calculate_drr_percent(
                day_ads,
                day_orders_amount,
                day_buyout_percent,
            )
            day_margin = saved_day_economics.get("margin")
            day_purchase_value = saved_day_economics.get("purchase_value")
        else:
            day_buyout_percent = measured_buyout_percent
            day_drr = unit_economics_1c.calculate_drr_percent(
                day_ads,
                day_orders_amount,
                day_buyout_percent,
            )
            purchased_units = round(day_orders_count * buyout_ratio, 2)
            day_unit_profit = unit_economics_1c.calculate_unit_profit(
                retail_price=economics_retail_price,
                customer_price=calculation_price,
                acquiring_percent=acquiring_percent,
                delivery_with_returns=logistics,
                storage_wb_rub=product_settings.storage_wb_rub,
                turnover_days=turnover_days,
                wb_commission_percent=commission_percent,
                advertising_rub=0,
                purchase_price=purchase_price,
                fulfillment_cost=fulfillment_cost,
                team_commission_percent=effective_team_commission,
                vat_percent=vat_rate,
                usn_percent=usn_rate,
                osno_percent=osno_rate,
                tax_system=effective_tax_system,
            )
            day_margin = (
                round(day_unit_profit["margin"] * purchased_units - day_ads, 2)
                if day_unit_profit is not None
                else None
            )
            day_purchase_value = (
                round(purchase_price * purchased_units, 2)
                if purchase_price is not None
                else None
            )
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
            "margin_rub": day_margin,
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

    period_orders_turnover = round(float(product_metrics.get("orders_amount") or 0), 2)
    period_turnover = round(
        period_orders_turnover - float(product_metrics.get("cancel_amount") or 0),
        2,
    )
    period_advertising_spend = round(float(product_metrics.get("spend") or 0), 2)
    average_daily_advertising = round(period_advertising_spend / period_days, 2)
    period_orders_count = max(int(product_metrics.get("orders_count") or 0), 0)
    period_buyout_percent = measured_buyout_percent
    if (
        closed_period_economics is not None
        and closed_period_economics.get("buyout_percent") is not None
    ):
        period_buyout_percent = min(
            max(float(closed_period_economics["buyout_percent"]), 0.0),
            100.0,
        )
    advertising_per_unit = unit_economics_1c.calculate_advertising_per_unit(
        period_advertising_spend,
        period_orders_count,
        period_buyout_percent,
    )
    period_drr = unit_economics_1c.calculate_drr_percent(
        period_advertising_spend,
        period_orders_turnover,
        period_buyout_percent,
    )
    period_purchase_value = (
        round(purchase_price * period_orders_count, 2) if purchase_price is not None else None
    )
    current_advertising_spend = round(
        float(current_product_metrics.get("spend") or 0),
        2,
    )
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
    period_margin = (
        round(current_unit_margin * period_orders_count, 2)
        if current_unit_margin is not None
        else None
    )
    if period_margin is None:
        period_roi = None
    elif period_purchase_value and period_purchase_value > 0:
        period_roi = round(period_margin / period_purchase_value * 100, 2)
    else:
        period_roi = 0.0
    current_unit_roi = (
        round(current_unit_margin / purchase_price * 100, 2)
        if current_unit_margin is not None and purchase_price > 0
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
        period_days=stock_period_days,
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
            "spend": period_advertising_spend,
            "average_daily_spend": average_daily_advertising,
            "spend_per_order": advertising_per_unit,
            "impressions": int(product_metrics.get("impressions") or 0),
            "clicks": int(product_metrics.get("clicks") or 0),
            "ctr": round(float(product_metrics.get("ctr") or 0), 2),
            "cpc": round(float(product_metrics.get("cpc") or 0), 2),
            "drr": period_drr,
            "orders": int(product_metrics.get("orders_count") or 0),
            "sold": int(product_metrics.get("sold_count") or 0),
            "buyout_percent": period_buyout_percent,
            "buyout_orders_count": int(product_metrics.get("buyout_orders_count") or 0),
            "buyout_period_from": product_metrics.get("buyout_period_from"),
            "buyout_period_to": product_metrics.get("buyout_period_to"),
            "buyout_updated_at": product_metrics.get("buyout_updated_at"),
            "orders_amount": round(float(product_metrics.get("orders_amount") or 0), 2),
            "cancel_count": int(product_metrics.get("cancel_count") or 0),
            "cancel_amount": round(float(product_metrics.get("cancel_amount") or 0), 2),
            "net_orders_count": int(product_metrics.get("net_orders_count") or 0),
            "net_orders_amount": round(float(product_metrics.get("net_orders_amount") or 0), 2),
            "funnel_updated_at": product_metrics.get("funnel_updated_at"),
            "funnel_source_version": product_metrics.get("funnel_source_version"),
            "funnel_vendor_code": product_metrics.get("funnel_vendor_code"),
            "funnel_product_name": product_metrics.get("funnel_product_name"),
            "buyout_orders_amount": round(
                float(product_metrics.get("buyout_orders_amount") or 0), 2
            ),
            "buyout_cancel_count": int(product_metrics.get("buyout_cancel_count") or 0),
            "buyout_cancel_amount": round(
                float(product_metrics.get("buyout_cancel_amount") or 0), 2
            ),
            "buyout_net_orders_count": int(
                product_metrics.get("buyout_net_orders_count") or 0
            ),
            "buyout_net_orders_amount": round(
                float(product_metrics.get("buyout_net_orders_amount") or 0), 2
            ),
            "buyout_source_version": product_metrics.get("buyout_source_version"),
            "period_from": product_metrics.get("period_from"),
            "period_to": product_metrics.get("period_to"),
            "period_days": int(product_metrics.get("period_days") or 7),
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
            "turnover_coverage": turnover_coverage,
            "margin_coverage": (
                closed_period_economics.get("coverage")
                if closed_period_economics is not None
                else None
            ),
            "roi_coverage": (
                closed_period_economics.get("coverage")
                if closed_period_economics is not None
                else None
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
            "days": stock_days,
            "orders_21d": stock_orders_count,
            "average_daily_orders": average_daily_orders,
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
            "acquiring": round(acquiring_percent, 2),
            "acquiring_value": (
                current_unit_profit.get("acquiring") if current_unit_profit else None
            ),
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
            "acceptance_coefficient": round(float(acceptance_coefficient or 0), 2),
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
            "vat_value": round(tax_components["vat"], 2) if tax_components else None,
            "usn_value": round(tax_components["usn"], 2) if tax_components else None,
            "osno_value": round(tax_components["osno"], 2) if tax_components else None,
            "tax_value": round(tax_components["total"], 2) if tax_components else None,
            "team_commission_value": (
                current_unit_profit.get("team_commission") if current_unit_profit else None
            ),
            "net_revenue": current_unit_profit.get("net_revenue") if current_unit_profit else None,
            "advertising_value": (
                current_unit_profit.get("advertising") if current_unit_profit else None
            ),
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
        for key in ("drr", "spend", "ctr", "cpc", "period_from", "period_to", "orders_amount")
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
            "period_to",
        )
    }
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
        )
    }
    return summary


def _unit_economics_1c_price_warnings(store_slugs: tuple[str, ...]) -> list[dict]:
    states = {
        str(row["store_slug"]): row
        for row in db.list_unit_economics_1c_price_sync_states(store_slugs)
    }
    warnings: list[dict] = []
    for store_slug in store_slugs:
        state = states.get(store_slug)
        if state is not None and state.get("status") == "ok":
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


@router.get("/sales/unit-economics-1c", response_class=HTMLResponse)
async def sales_unit_economics_1c(request: Request):
    accessible_store_slugs = accessible_stores(request.state.user, "WB")
    data_request = request.query_params.get("data") == "1"
    detail_store = (
        str(request.query_params.get("store") or "").strip().lower() if data_request else ""
    )
    detail_article = str(request.query_params.get("article") or "").strip() if data_request else ""
    if detail_article and detail_store not in accessible_store_slugs:
        return JSONResponse({"ok": False, "error": "Нет доступа к магазину"}, status_code=403)
    store_slugs = (detail_store,) if detail_article else accessible_store_slugs
    include_history = bool(detail_article)

    def load_products() -> tuple[list[dict], list[dict]]:
        latest_rows = db.get_unit_economics_1c_latest_daily_prices(store_slugs)
        prices = {(str(row["store_slug"]), str(row["article"])): row for row in latest_rows}
        today = datetime.now(MOSCOW_TIMEZONE).date()
        closed_period_to = today - timedelta(days=1)
        closed_period_from = closed_period_to - timedelta(days=6)
        history_from = today - timedelta(days=20) if include_history else closed_period_from
        metrics = unit_economics_1c.load_product_metrics(
            store_slugs,
            period_days=7,
            today=closed_period_to,
        )
        current_metrics = unit_economics_1c.load_product_metrics(
            store_slugs,
            period_days=1,
            today=today,
        )
        chart_metrics = (
            unit_economics_1c.load_product_metrics(store_slugs, period_days=21)
            if include_history
            else {}
        )
        saved_funnel_daily_rows = db.get_unit_economics_1c_funnel_daily_order_rows(
            store_slugs,
            history_from.isoformat(),
            today.isoformat(),
        )
        saved_margin_snapshots = db.get_unit_economics_1c_daily_margin_snapshots(
            store_slugs,
            history_from.isoformat(),
            closed_period_to.isoformat(),
        )
        daily_orders_by_product: dict[tuple[str, str], dict[str, dict]] = {}
        funnel_days_by_store: dict[str, set[str]] = {}
        for row in saved_funnel_daily_rows:
            funnel_days_by_store.setdefault(str(row["store_slug"]), set()).add(
                str(row["day"])
            )
            daily_orders_by_product.setdefault(
                (str(row["store_slug"]), str(row["article"])),
                {},
            )[str(row["day"])] = row
        margin_snapshots_by_product: dict[tuple[str, str], dict[str, dict]] = {}
        for row in saved_margin_snapshots:
            margin_snapshots_by_product.setdefault(
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
        glue_groups: dict[tuple[str, int], list[dict]] = {}
        for store_slug, catalog in catalogs.items():
            for item in catalog:
                article = str(item.get("article") or "")
                imt_id = _optional_integer(
                    (product_references.get((store_slug, article)) or {}).get("imt_id")
                )
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
            catalog = catalogs[store_slug]
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
                        period_days=7,
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
                product_margin_snapshots = (
                    margin_snapshots_by_product.get((store_slug, article)) or {}
                )
                live_snapshot = unit_economics_1c_history.calculate_snapshot_row(
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
                product_daily_orders = daily_orders_by_product.get((store_slug, nm_id)) or {}
                history_day_economics: dict[str, dict] = {}
                for item in history_product_metrics.get("daily") or [] if include_history else []:
                    if not isinstance(item, dict):
                        continue
                    history_day = _optional_day(item.get("date"))
                    if history_day is None:
                        continue
                    history_day_economics[history_day.isoformat()] = (
                        _report_historical_economics(
                            date_from=history_day,
                            date_to=history_day,
                            daily_orders=product_daily_orders,
                            margin_snapshots=product_margin_snapshots,
                            live_day=today,
                            live_unit_margin=(
                                _price_value(live_snapshot.get("unit_margin"))
                                if live_snapshot
                                else None
                            ),
                            live_purchase_price=(
                                _price_value(live_snapshot.get("purchase_price"))
                                if live_snapshot
                                else None
                            ),
                            daily_advertising={
                                history_day.isoformat(): max(
                                    float(item.get("advertising_spend") or 0),
                                    0.0,
                                )
                            },
                            fallback_buyout_percent=history_product_metrics.get(
                                "buyout_percent"
                            ),
                        )
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
                    fallback_buyout_percent=period_product_metrics.get("buyout_percent"),
                    allow_partial=True,
                )
                imt_id = _optional_integer(reference.get("imt_id"))
                glued_products = (
                    [item for item in glue_groups.get((store_slug, imt_id), []) if item["article"] != article]
                    if imt_id is not None
                    else []
                )
                products.append(
                    _unit_economics_1c_mock_product(
                        store_slug=store_slug,
                        product=product,
                        price_snapshot=prices.get((store_slug, article)),
                        acquiring_percent=cabinet.acquiring_percent,
                        product_metrics=period_product_metrics,
                        current_product_metrics=current_product_metrics,
                        closed_period_economics=closed_period_economics,
                        turnover_coverage=turnover_coverage,
                        history_product_metrics=history_product_metrics,
                        history_day_economics=history_day_economics,
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
                    )
                )

        return products, _unit_economics_1c_price_warnings(store_slugs)

    if data_request:
        if request.query_params.get("commissions") == "1":
            commissions = await run_in_threadpool(db.list_unit_economics_1c_wb_commissions)
            return JSONResponse(
                {
                    "ok": True,
                    "items": [
                        {
                            "category": item.get("category"),
                            "commission_percent": item.get("commission_percent"),
                        }
                        for item in commissions
                    ],
                }
            )
        products, price_warnings = await run_in_threadpool(load_products)
        if detail_article:
            if not products:
                return JSONResponse({"ok": False, "error": "Товар не найден"}, status_code=404)
            return JSONResponse({"ok": True, "product": products[0]})
        return JSONResponse(
            {
                "ok": True,
                "products": [_unit_economics_1c_product_summary(item) for item in products],
                "warnings": price_warnings,
            }
        )

    price_warnings = await run_in_threadpool(
        _unit_economics_1c_price_warnings,
        accessible_store_slugs,
    )
    price_alerts = [
        {
            "title": (
                f"Кабинет {warning['store_name']}: использована резервная цена"
                if warning["status"] == "fallback"
                else f"Кабинет {warning['store_name']}: цены не обновились"
            ),
            "text": warning["message"],
        }
        for warning in price_warnings
    ]
    unit_config = {
        "userKey": str(request.state.user["id"]),
        "columnPreferences": await run_in_threadpool(
            db.get_ui_preference,
            int(request.state.user["id"]),
            UNIT_ECONOMICS_COLUMNS_PREFERENCE_SCOPE,
        ),
        "stores": [
            {
                "slug": slug,
                "name": STORES[slug]["name"],
                "initials": STORES[slug]["initials"],
                "color": STORES[slug]["color"],
                "text": STORES[slug]["text"],
            }
            for slug in accessible_store_slugs
        ],
        "products": [],
        "productsEndpoint": "/sales/unit-economics-1c?data=1",
        "subjectCommissions": [],
        "commissionsEndpoint": "/sales/unit-economics-1c?data=1&commissions=1",
        "canEdit": has_section_access(
            request.state.user,
            SectionName.UNIT_ECONOMICS_1C,
            SectionAccessLevel.WRITE,
        ),
    }
    content = fill_template(
        "unit_economics_1c_content.html",
        unit_1c_config=json.dumps(unit_config, ensure_ascii=False).replace("</", "<\\/"),
    )
    return render_page(
        "CheckStock — Юнит-экономика 1С — Wildberries",
        "unit_1c_wb",
        content,
        request.state.user,
        content_class="content--unit-1c",
        alerts=price_alerts,
    )


@router.post("/api/unit-economics-1c/sync")
async def unit_economics_1c_sync(request: Request):
    store_slugs = accessible_stores(request.state.user, "WB")
    report = await run_in_threadpool(
        run_tracked,
        "unit_economics_1c_sync",
        "manual",
        lambda: unit_economics_1c.sync_stores(store_slugs),
    )
    return {"ok": all(item.get("ok") for item in report.values()), "report": report}


@router.post("/api/unit-economics-1c/prices/sync")
async def unit_economics_1c_prices_sync(request: Request):
    store_slugs = accessible_stores(request.state.user, "WB")
    report = await run_in_threadpool(
        run_tracked,
        "unit_economics_1c_sync",
        "manual",
        lambda: unit_economics_1c_prices.sync_stores(store_slugs),
    )
    return {"ok": all(item.get("ok") for item in report.values()), "report": report}


@router.post("/api/unit-economics-1c/prices")
async def unit_economics_1c_prices_submit(
    request: Request,
    payload: UnitEconomics1CPriceChangeRequest,
):
    if not has_section_access(
        request.state.user,
        SectionName.UNIT_ECONOMICS_1C,
        SectionAccessLevel.WRITE,
    ):
        return JSONResponse({"ok": False, "error": "Нет права изменять цены"}, status_code=403)

    changes_by_store: dict[str, list[dict]] = {}
    for item in payload.data:
        store_slug = item.store_slug.lower()
        if store_slug not in STORES:
            return JSONResponse({"ok": False, "error": f"Кабинет {store_slug} не найден"}, status_code=404)
        if not has_scope(request.state.user, store_slug, "WB"):
            return JSONResponse(
                {"ok": False, "error": f"Нет доступа к кабинету {STORES[store_slug]['name']}"},
                status_code=403,
            )
        changes_by_store.setdefault(store_slug, []).append(
            {
                "article": item.article,
                "target_price": item.target_price,
                "target_kind": item.target_kind,
            }
        )

    for store_slug, changes in changes_by_store.items():
        catalog = await run_in_threadpool(db.get_catalog_items, store_slug, "WB")
        catalog_articles = {str(item.get("article") or "").strip() for item in catalog}
        missing = next(
            (change["article"] for change in changes if change["article"] not in catalog_articles),
            None,
        )
        if missing is not None:
            return JSONResponse(
                {"ok": False, "error": f"Товар {missing} не найден в актуальном каталоге"},
                status_code=404,
            )

    user = dict(request.state.user)

    def submit() -> dict:
        reports: dict[str, dict] = {}
        accepted: list[dict] = []
        errors: list[dict] = []
        for store_slug, changes in changes_by_store.items():
            try:
                report = unit_economics_1c_prices.submit_price_changes(store_slug, changes)
                if report.get("sent"):
                    report = unit_economics_1c_prices.finalize_price_change_report(
                        store_slug,
                        report,
                    )
            except Exception as error:
                message = getattr(error, "friendly", None) or str(error) or type(error).__name__
                logger.warning(
                    "unit_economics_1c_price_submit_failed store=%s error=%s",
                    store_slug,
                    message,
                )
                report = {
                    "ok": False,
                    "sent": 0,
                    "accepted": [],
                    "errors": [
                        {
                            "product_id": f"{store_slug}:{change['article']}",
                            "article": change["article"],
                            "error": message,
                        }
                        for change in changes
                    ],
                }
            reports[store_slug] = report
            accepted.extend(report.get("accepted") or [])
            errors.extend(report.get("errors") or [])
            if report.get("sent"):
                db.log_action(
                    int(user["id"]),
                    str(user["full_name"]),
                    "unit_economics_1c_price_submit",
                    (
                        f"Переданы цены WB: {STORES[store_slug]['name']}, "
                        f"товаров {report['sent']}, uploadID {report.get('upload_id')}"
                    ),
                    datetime.now(UTC).isoformat(),
                )
        sync_errors = [
            {
                "store_slug": store_slug,
                "error": str(
                    (report.get("sync") or {}).get("error") or "Не удалось автоматически обновить цены в БД"
                ),
            }
            for store_slug, report in reports.items()
            if report.get("sent") and not report.get("price_data_refreshed")
        ]
        price_data_refreshed = not sync_errors and any(report.get("sent") for report in reports.values())
        return {
            "ok": not errors and not sync_errors,
            "accepted_count": len(accepted),
            "accepted_product_ids": [item["product_id"] for item in accepted],
            "accepted": accepted,
            "errors": errors,
            "sync_errors": sync_errors,
            "price_data_refreshed": price_data_refreshed,
            "reports": reports,
        }

    jobs = getattr(request.app.state, "unit_economics_1c_price_jobs", None)
    if jobs is None:
        jobs = {}
        request.app.state.unit_economics_1c_price_jobs = jobs
    cutoff = datetime.now(UTC) - timedelta(days=1)
    expired_job_ids = [
        existing_job_id
        for existing_job_id, existing_job in jobs.items()
        if existing_job.get("status") in {"success", "error"}
        and datetime.fromisoformat(existing_job["created_at"]) < cutoff
    ]
    for expired_job_id in expired_job_ids:
        jobs.pop(expired_job_id, None)
    tasks = getattr(request.app.state, "unit_economics_1c_price_tasks", None)
    if tasks is None:
        tasks = set()
        request.app.state.unit_economics_1c_price_tasks = tasks
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "queued",
        "user_id": int(request.state.user["id"]),
        "store_slugs": tuple(changes_by_store),
        "created_at": datetime.now(UTC).isoformat(),
        "result": None,
        "error": None,
    }
    jobs[job_id] = job

    def run_job() -> None:
        job["status"] = "running"
        try:
            result = submit()
        except Exception as error:
            logger.exception("unit_economics_1c_price_job_failed job_id=%s", job_id)
            job["status"] = "error"
            job["error"] = getattr(error, "friendly", None) or str(error) or type(error).__name__
            return
        job["result"] = result
        job["status"] = "success" if result.get("ok") else "error"
        if job["status"] == "error":
            errors = result.get("errors") or result.get("sync_errors") or []
            job["error"] = str((errors[0] or {}).get("error") or "WB не принял изменение цены")

    task = PRICE_JOB_EXECUTOR.submit(run_job)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return JSONResponse(
        {"ok": True, "job_id": job_id, "status": "queued"},
        status_code=202,
    )


@router.get("/api/unit-economics-1c/prices/jobs/{job_id}")
async def unit_economics_1c_price_job(request: Request, job_id: str):
    jobs = getattr(request.app.state, "unit_economics_1c_price_jobs", {})
    job = jobs.get(job_id)
    if job is None or int(job.get("user_id") or 0) != int(request.state.user["id"]):
        return JSONResponse({"ok": False, "error": "Задача не найдена"}, status_code=404)
    return {
        "ok": job["status"] != "error",
        "job_id": job_id,
        "status": job["status"],
        "result": job.get("result"),
        "error": job.get("error"),
    }


@router.post("/api/unit-economics-1c/prices/preview")
async def unit_economics_1c_prices_preview(
    request: Request,
    payload: UnitEconomics1CPriceChangeRequest,
):
    if not has_section_access(
        request.state.user,
        SectionName.UNIT_ECONOMICS_1C,
        SectionAccessLevel.WRITE,
    ):
        return JSONResponse({"ok": False, "error": "Нет права изменять цены"}, status_code=403)

    item = payload.data[0]
    store_slug = item.store_slug.lower()
    if store_slug not in STORES:
        return JSONResponse({"ok": False, "error": f"Кабинет {store_slug} не найден"}, status_code=404)
    if not has_scope(request.state.user, store_slug, "WB"):
        return JSONResponse(
            {"ok": False, "error": f"Нет доступа к кабинету {STORES[store_slug]['name']}"},
            status_code=403,
        )
    catalog = await run_in_threadpool(db.get_catalog_items, store_slug, "WB")
    if item.article not in {str(product.get("article") or "").strip() for product in catalog}:
        return JSONResponse(
            {"ok": False, "error": f"Товар {item.article} не найден в актуальном каталоге"},
            status_code=404,
        )
    change = {
        "article": item.article,
        "target_price": item.target_price,
        "target_kind": item.target_kind,
    }
    try:
        report = await run_in_threadpool(
            unit_economics_1c_prices.preview_price_changes,
            store_slug,
            [change],
        )
    except Exception as error:
        message = getattr(error, "friendly", None) or str(error) or type(error).__name__
        logger.warning(
            "unit_economics_1c_price_preview_failed store=%s error=%s",
            store_slug,
            message,
        )
        return JSONResponse({"ok": False, "error": message}, status_code=502)
    status_code = 200 if report.get("accepted") else 422
    return JSONResponse(report, status_code=status_code)


def _report_period(request: Request) -> tuple[date, date]:
    today = datetime.now(MOSCOW_TIMEZONE).date()
    raw_from = str(request.query_params.get("date_from") or "")
    raw_to = str(request.query_params.get("date_to") or "")
    try:
        date_to = date.fromisoformat(raw_to) if raw_to else today
        date_from = date.fromisoformat(raw_from) if raw_from else date_to - timedelta(days=6)
    except ValueError as error:
        raise ValueError("Период должен быть задан в формате ГГГГ-ММ-ДД") from error
    if date_from > date_to:
        raise ValueError("Начало периода не может быть позже окончания")
    if (date_to - date_from).days >= 366:
        raise ValueError("Максимальный период отчёта — 366 дней")
    return date_from, date_to


def _query_values(request: Request, name: str) -> tuple[str, ...]:
    values: list[str] = []
    for raw_value in request.query_params.getlist(name):
        for value in str(raw_value or "").split(","):
            normalized = value.strip()
            if normalized and normalized not in values:
                values.append(normalized)
    return tuple(values)


def _manager_identity_key(value: object) -> str:
    return " ".join(
        re.sub(r"[^0-9a-zа-я]+", " ", str(value or "").casefold().replace("ё", "е")).split()
    )


def _manager_matches_user(manager: str, user: object) -> bool:
    current_user = coerce_user(user)
    if current_user is None:
        return False
    return _manager_identity_key(manager) == _manager_identity_key(current_user.full_name)


def _json_mapping(value: object) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _report_daily_calculations(
    *,
    date_from: date,
    date_to: date,
    daily_orders: dict[str, dict],
    margin_snapshots: dict[str, dict],
    live_day: date,
    live_snapshot: dict | None,
    daily_advertising: dict[str, float],
    fallback_buyout_percent: float | None,
) -> list[dict]:
    """Expose the exact per-day inputs used to calculate unit margin."""

    result: list[dict] = []
    current = date_from
    while current <= date_to:
        day_key = current.isoformat()
        daily_row = daily_orders.get(day_key) or {}
        snapshot = margin_snapshots.get(day_key)
        if snapshot is None and current == live_day:
            snapshot = live_snapshot
        orders_count = max(int(daily_row.get("orders_count") or 0), 0)
        cancel_count = max(int(daily_row.get("cancel_count") or 0), 0)
        advertising_spend = round(max(float(daily_advertising.get(day_key) or 0), 0.0), 2)
        raw_buyout_percent = daily_row.get("buyout_percent")
        if raw_buyout_percent is None or float(raw_buyout_percent) <= 0:
            raw_buyout_percent = unit_economics_1c_history.snapshot_buyout_percent(snapshot)
            if raw_buyout_percent is None:
                raw_buyout_percent = fallback_buyout_percent
            if raw_buyout_percent is None or float(raw_buyout_percent) <= 0:
                raw_buyout_percent = 100.0
        buyout_percent = round(min(max(float(raw_buyout_percent), 0.0), 100.0), 2)
        expected_buyouts = round(orders_count * buyout_percent / 100, 2)
        advertising_per_unit = unit_economics_1c.calculate_advertising_per_unit(
            advertising_spend,
            orders_count,
            buyout_percent,
        )
        item = {
            "date": day_key,
            "available": False,
            "snapshot_available": snapshot is not None,
            "advertising_spend": advertising_spend,
            "orders_count": orders_count,
            "net_orders_count": max(orders_count - cancel_count, 0),
            "buyout_percent": buyout_percent,
            "expected_buyouts": expected_buyouts,
            "advertising_per_unit": advertising_per_unit,
            "vat_percent": None,
            "usn_percent": None,
            "customer_price": None,
            "retail_price": None,
            "acquiring_percent": None,
            "logistics": None,
            "storage": None,
            "commission_percent": None,
            "team_commission_percent": None,
            "fulfillment_cost": None,
            "purchase_price": None,
            "net_profit": None,
            "net_revenue": None,
            "vat_value": None,
            "usn_value": None,
        }
        if snapshot is None:
            result.append(item)
            current += timedelta(days=1)
            continue

        inputs = _json_mapping(snapshot.get("inputs_json"))
        vat_percent = _price_value(inputs.get("vat_percent"))
        usn_percent = _price_value(inputs.get("usn_percent"))
        retail_price = _price_value(inputs.get("retail_price"))
        customer_price = _price_value(inputs.get("customer_price_with_spp"))
        if customer_price is None:
            customer_price = _price_value(inputs.get("customer_price"))
        acquiring_percent = _price_value(inputs.get("acquiring_percent"))
        delivery_with_returns = unit_economics_1c.calculate_delivery_with_returns(
            inputs.get("delivery_wb_rub"),
            buyout_percent,
            inputs.get("return_cost_rub"),
            inputs.get("paid_acceptance_cost"),
        )
        calculation = unit_economics_1c.calculate_unit_profit(
            retail_price=retail_price,
            customer_price=customer_price,
            acquiring_percent=acquiring_percent,
            delivery_with_returns=delivery_with_returns,
            storage_wb_rub=_price_value(inputs.get("storage_wb_rub")),
            turnover_days=_optional_integer(inputs.get("turnover_days")),
            wb_commission_percent=_price_value(inputs.get("commission_percent")),
            advertising_rub=advertising_per_unit,
            purchase_price=_price_value(inputs.get("purchase_price")),
            fulfillment_cost=_price_value(inputs.get("fulfillment_cost")),
            team_commission_percent=_price_value(inputs.get("team_commission_percent")),
            vat_percent=vat_percent,
            usn_percent=usn_percent,
            osno_percent=_price_value(inputs.get("osno_percent")),
            tax_system=str(inputs.get("tax_system") or "usn"),
        )
        item.update(
            {
                "available": calculation is not None,
                "vat_percent": vat_percent,
                "usn_percent": usn_percent,
                "customer_price": customer_price,
                "retail_price": retail_price,
                "acquiring_percent": acquiring_percent,
                "logistics": round(delivery_with_returns, 2),
                "commission_percent": _price_value(inputs.get("commission_percent")),
                "team_commission_percent": _price_value(inputs.get("team_commission_percent")),
                "fulfillment_cost": _price_value(inputs.get("fulfillment_cost")),
                "purchase_price": _price_value(inputs.get("purchase_price")),
            }
        )
        if calculation is not None:
            item.update(
                {
                    "storage": calculation["storage"],
                    "net_profit": calculation["margin"],
                    "net_revenue": calculation["net_revenue"],
                    "vat_value": calculation["vat"],
                    "usn_value": calculation["usn"],
                }
            )
        result.append(item)
        current += timedelta(days=1)
    return result


def _aggregate_report_daily_calculations(rows: list[dict]) -> list[dict]:
    dates = sorted(
        {
            str(item.get("date"))
            for row in rows
            for item in row.get("daily_calculations") or []
            if item.get("date")
        }
    )
    unit_fields = (
        "vat_percent",
        "usn_percent",
        "customer_price",
        "retail_price",
        "acquiring_percent",
        "logistics",
        "storage",
        "commission_percent",
        "team_commission_percent",
        "fulfillment_cost",
        "purchase_price",
        "net_profit",
        "net_revenue",
        "vat_value",
        "usn_value",
    )
    result: list[dict] = []
    for day_key in dates:
        items = [
            item
            for row in rows
            for item in row.get("daily_calculations") or []
            if str(item.get("date")) == day_key
        ]
        orders_count = sum(int(item.get("orders_count") or 0) for item in items)
        expected_buyouts = round(sum(float(item.get("expected_buyouts") or 0) for item in items), 2)
        available_items = [item for item in items if item.get("available")]
        aggregate = {
            "date": day_key,
            "available": bool(available_items),
            "complete": len(available_items) == len(items),
            "covered_products": len(available_items),
            "product_count": len(items),
            "snapshot_available": all(bool(item.get("snapshot_available")) for item in items),
            "advertising_spend": round(
                sum(float(item.get("advertising_spend") or 0) for item in items),
                2,
            ),
            "orders_count": orders_count,
            "net_orders_count": sum(int(item.get("net_orders_count") or 0) for item in items),
            "expected_buyouts": expected_buyouts,
            "buyout_percent": (
                round(
                    sum(
                        float(item.get("buyout_percent") or 0)
                        * int(item.get("orders_count") or 0)
                        for item in items
                    )
                    / orders_count,
                    2,
                )
                if orders_count
                else None
            ),
            "advertising_per_unit": (
                round(
                    sum(float(item.get("advertising_spend") or 0) for item in items)
                    / expected_buyouts,
                    2,
                )
                if expected_buyouts
                else 0.0
            ),
        }
        for field in unit_fields:
            weighted = [
                (
                    float(item[field]),
                    float(item.get("expected_buyouts") or 0) or 1.0,
                )
                for item in available_items
                if item.get(field) is not None
            ]
            aggregate[field] = (
                round(sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted), 2)
                if weighted
                else None
            )
        result.append(aggregate)
    return result


def _unit_profit_report_totals(rows: list[dict]) -> dict:
    margin_complete = all(bool(row.get("margin_complete")) for row in rows)
    totals = {
        "orders_count": sum(int(row.get("orders_count") or 0) for row in rows),
        "orders_amount": round(sum(float(row.get("orders_amount") or 0) for row in rows), 2),
        "cancel_count": sum(int(row.get("cancel_count") or 0) for row in rows),
        "cancel_amount": round(sum(float(row.get("cancel_amount") or 0) for row in rows), 2),
        "net_orders_count": sum(int(row.get("net_orders_count") or 0) for row in rows),
        "net_orders_amount": round(sum(float(row.get("net_orders_amount") or 0) for row in rows), 2),
        "buyout_count": (
            sum(int(row.get("buyout_count") or 0) for row in rows)
            if any(row.get("buyout_count") is not None for row in rows)
            else None
        ),
        "buyout_amount": (
            round(sum(float(row.get("buyout_amount") or 0) for row in rows), 2)
            if any(row.get("buyout_amount") is not None for row in rows)
            else None
        ),
        "buyout_orders_count": sum(int(row.get("buyout_orders_count") or 0) for row in rows),
        "expected_buyout_amount": round(
            sum(float(row.get("expected_buyout_amount") or 0) for row in rows),
            2,
        ),
        "stock": sum(int(row.get("stock") or 0) for row in rows),
        "impressions": sum(int(row.get("impressions") or 0) for row in rows),
        "clicks": sum(int(row.get("clicks") or 0) for row in rows),
        "advertising_spend": round(
            sum(float(row.get("advertising_spend") or 0) for row in rows),
            2,
        ),
        "margin": (
            round(sum(float(row.get("margin") or 0) for row in rows), 2)
            if margin_complete
            else None
        ),
        "margin_orders_count": round(
            sum(float(row.get("margin_orders_count") or 0) for row in rows),
            2,
        ),
        "purchase_value": (
            round(sum(float(row.get("purchase_value") or 0) for row in rows), 2)
            if margin_complete
            else None
        ),
        "margin_complete": margin_complete,
        "margin_missing_days": sorted(
            {
                day
                for row in rows
                for day in row.get("margin_missing_days") or []
            }
        ),
    }
    totals["buyout_percent"] = (
        round(
            sum(
                float(row.get("buyout_percent") or 0)
                * int(row.get("buyout_orders_count") or 0)
                for row in rows
                if row.get("buyout_percent") is not None
            )
            / totals["buyout_orders_count"],
            2,
        )
        if totals["buyout_orders_count"]
        else None
    )
    totals["ctr"] = (
        round(totals["clicks"] / totals["impressions"] * 100, 2)
        if totals["impressions"]
        else 0.0
    )
    totals["cpc"] = (
        round(totals["advertising_spend"] / totals["clicks"], 2)
        if totals["clicks"]
        else 0.0
    )
    totals["drr"] = (
        round(totals["advertising_spend"] / totals["expected_buyout_amount"] * 100, 2)
        if totals["expected_buyout_amount"]
        else 100.0
        if totals["advertising_spend"]
        else 0.0
    )
    totals["roi"] = (
        round(float(totals["margin"]) / float(totals["purchase_value"]) * 100, 2)
        if margin_complete and totals["purchase_value"]
        else 0.0
        if margin_complete
        else None
    )
    return totals


def _unit_profit_category_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        subject = str(row.get("subject") or "Без предмета").strip() or "Без предмета"
        grouped.setdefault(subject, []).append(row)
    result: list[dict] = []
    for subject, products in grouped.items():
        totals = _unit_profit_report_totals(products)
        store_names = sorted({str(row.get("store_name") or "") for row in products if row.get("store_name")})
        store_slugs = sorted({str(row.get("store_slug") or "") for row in products if row.get("store_slug")})
        managers = sorted({str(row.get("manager") or "") for row in products if row.get("manager")})
        result.append(
            {
                **totals,
                "row_kind": "category",
                "name": subject,
                "article": None,
                "image_url": "",
                "subject": subject,
                "product_count": len(products),
                "store_slug": store_slugs[0] if len(store_slugs) == 1 else "all",
                "store_name": (
                    store_names[0] if len(store_names) == 1 else f"{len(store_names)} магазинов"
                ),
                "manager": (
                    managers[0]
                    if len(managers) == 1
                    else f"{len(managers)} менеджеров"
                    if managers
                    else None
                ),
                "daily_calculations": _aggregate_report_daily_calculations(products),
            }
        )
    result.sort(key=lambda row: (-float(row.get("orders_amount") or 0), str(row["name"]).casefold()))
    return result


def _validated_column_preferences(payload: object) -> dict | None:
    if not isinstance(payload, dict):
        return None
    valid_keys = set(UNIT_ECONOMICS_COLUMN_GROUPS)
    raw_order = payload.get("order")
    raw_hidden = payload.get("hidden")
    if not isinstance(raw_order, list) or not isinstance(raw_hidden, list):
        return None
    order: list[str] = []
    for value in raw_order:
        key = str(value)
        if key in valid_keys and key not in order:
            order.append(key)
    order.extend(key for key in UNIT_ECONOMICS_COLUMN_GROUPS if key not in order)
    hidden = [
        key
        for key in UNIT_ECONOMICS_COLUMN_GROUPS
        if key != "product" and key in {str(value) for value in raw_hidden}
    ]
    return {"order": order, "hidden": hidden}


def _unit_profit_report_filter_options(
    store_slugs: tuple[str, ...],
    current_user,
) -> dict:
    references = {
        (str(item["store_slug"]), str(item["article"])): item
        for item in db.get_unit_economics_1c_product_reference_rows(store_slugs)
    }
    available_managers = {
        str(reference.get("manager") or "").strip()
        for reference in references.values()
        if str(reference.get("manager") or "").strip()
    }
    allowed_manager_keys: set[str] | None = None
    if current_user is not None and current_user.role == Role.USER:
        allowed_manager_keys = {
            _manager_identity_key(manager)
            for manager in available_managers
            if _manager_matches_user(manager, current_user)
        }
    subjects: set[str] = set()
    managers: set[str] = set()
    articles: list[dict] = []
    for store_slug in store_slugs:
        for catalog_item in db.get_stock_items(store_slug, "WB"):
            article = str(catalog_item.get("article") or "")
            reference = references.get((store_slug, article)) or {}
            subject = str(reference.get("category") or "").strip() or "Без предмета"
            manager = str(reference.get("manager") or "").strip()
            manager_key = _manager_identity_key(manager)
            if allowed_manager_keys is not None and manager_key not in allowed_manager_keys:
                continue
            subjects.add(subject)
            if manager:
                managers.add(manager)
            articles.append(
                {
                    "article": article,
                    "name": str(catalog_item.get("name") or article),
                    "store_slug": store_slug,
                    "store_name": STORES[store_slug]["name"],
                    "image_url": str(catalog_item.get("image_url") or ""),
                }
            )
    return {
        "filters": {
            "subjects": sorted(subjects),
            "managers": sorted(managers),
            "articles": sorted(
                articles,
                key=lambda item: (item["name"].casefold(), item["article"]),
            ),
        },
        "manager_scope": {
            "restricted": allowed_manager_keys is not None,
            "matched": bool(allowed_manager_keys) if allowed_manager_keys is not None else True,
        },
    }


@router.get("/sales/unit-economics-1c/reports/unit-profit", response_class=HTMLResponse)
async def sales_unit_economics_1c_unit_profit_report(request: Request):
    store_slugs = accessible_stores(request.state.user, "WB")
    current_user = coerce_user(request.state.user)
    show_manager_filter = current_user is not None and current_user.role in {
        Role.ADMIN,
        Role.SUPERADMIN,
    }
    today = datetime.now(MOSCOW_TIMEZONE).date()
    config = {
        "stores": [{"slug": slug, "name": STORES[slug]["name"]} for slug in store_slugs],
        "defaultDateFrom": (today - timedelta(days=6)).isoformat(),
        "defaultDateTo": today.isoformat(),
    }
    content = fill_template(
        "unit_economics_1c_report_content.html",
        unit_1c_report_config=json.dumps(config, ensure_ascii=False).replace("</", "<\\/"),
        unit_1c_manager_filter=(
            '<div class="ue1cr-filter"><span>Менеджеры</span>'
            '<details class="ue1cr-multi" id="ue1cr-manager">\n'
            '                <summary id="ue1cr-manager-summary">Все менеджеры</summary>\n'
            '                <div class="ue1cr-multi-options" '
            'id="ue1cr-manager-options"></div>\n'
            "            </details></div>"
            if show_manager_filter
            else ""
        ),
    )
    return render_page(
        "CheckStock — Юниточная прибыль",
        "unit_1c_reports",
        content,
        request.state.user,
        content_class="content--unit-1c-report",
    )


@router.get("/api/unit-economics-1c/reports/unit-profit/filters")
async def unit_economics_1c_unit_profit_report_filters(request: Request):
    accessible = accessible_stores(request.state.user, "WB")
    selected_stores = tuple(value.lower() for value in _query_values(request, "store"))
    if not selected_stores or "all" in selected_stores:
        store_slugs = accessible
    elif any(store_slug not in accessible for store_slug in selected_stores):
        return JSONResponse({"ok": False, "error": "Нет доступа к кабинету"}, status_code=403)
    else:
        store_slugs = tuple(store_slug for store_slug in accessible if store_slug in selected_stores)
    result = await run_in_threadpool(
        _unit_profit_report_filter_options,
        store_slugs,
        coerce_user(request.state.user),
    )
    return {"ok": True, **result}


async def _unit_economics_1c_unit_profit_report_data(
    request: Request,
    *,
    for_export: bool = False,
):
    try:
        date_from, date_to = _report_period(request)
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=422)
    accessible = accessible_stores(request.state.user, "WB")
    selected_stores = tuple(value.lower() for value in _query_values(request, "store"))
    if not selected_stores or "all" in selected_stores:
        store_slugs = accessible
    elif any(store_slug not in accessible for store_slug in selected_stores):
        return JSONResponse({"ok": False, "error": "Нет доступа к кабинету"}, status_code=403)
    else:
        store_slugs = tuple(store_slug for store_slug in accessible if store_slug in selected_stores)
    selected_subjects = {value.casefold() for value in _query_values(request, "subject")}
    selected_managers = {
        _manager_identity_key(value) for value in _query_values(request, "manager") if value
    }
    selected_articles = {
        normalized
        for value in _query_values(request, "article")
        for normalized in (value.casefold(), _nm_id(value).casefold())
        if normalized
    }
    period_days = (date_to - date_from).days + 1
    current_user = coerce_user(request.state.user)
    include_daily_details = str(request.query_params.get("daily_details") or "").lower() in {
        "1",
        "true",
        "yes",
    }
    group_by = (
        "subject"
        if str(request.query_params.get("group_by") or "").strip().lower() == "subject"
        else "product"
    )
    if group_by == "subject":
        include_daily_details = False
    try:
        page = max(int(request.query_params.get("page") or 1), 1)
        requested_page_size = int(request.query_params.get("page_size") or 50)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "Некорректная страница отчёта"}, status_code=422)
    page_size = min(max(requested_page_size, 25), 100)

    def load_report() -> dict:
        latest_rows = db.get_unit_economics_1c_latest_daily_prices(store_slugs)
        prices = {(str(row["store_slug"]), str(row["article"])): row for row in latest_rows}
        metrics = unit_economics_1c.load_product_metrics(
            store_slugs,
            period_days=period_days,
            today=date_to,
        )
        saved_funnel_daily_rows = (
            db.get_unit_economics_1c_funnel_daily_order_rows(
                store_slugs,
                date_from.isoformat(),
                date_to.isoformat(),
            )
            if for_export
            else []
        )
        saved_margin_snapshots = db.get_unit_economics_1c_daily_margin_snapshots(
            store_slugs,
            date_from.isoformat(),
            date_to.isoformat(),
        )
        daily_orders_by_product: dict[tuple[str, str], dict[str, dict]] = {}
        for row in saved_funnel_daily_rows:
            daily_orders_by_product.setdefault(
                (str(row["store_slug"]), str(row["article"])),
                {},
            )[str(row["day"])] = row
        margin_snapshots_by_product: dict[tuple[str, str], dict[str, dict]] = {}
        for row in saved_margin_snapshots:
            margin_snapshots_by_product.setdefault(
                (str(row["store_slug"]), str(row["article"])),
                {},
            )[str(row["day"])] = row
        live_day = datetime.now(MOSCOW_TIMEZONE).date()
        live_metrics = (
            unit_economics_1c.load_product_metrics(
                store_slugs,
                period_days=1,
                today=live_day,
            )
            if date_from <= live_day <= date_to
            else {}
        )
        saved_funnel_weekly_rows = (
            db.get_unit_economics_1c_funnel_product_metrics(store_slugs) if for_export else []
        )
        stock_metrics = unit_economics_1c.load_product_average_daily_orders(
            store_slugs,
            period_days=unit_economics_1c.STOCK_COVERAGE_PERIOD_DAYS,
            today=date_to,
        )
        cabinets = {item.store_slug: item for item in db.list_unit_economics_1c_cabinet_settings(store_slugs)}
        product_settings = {
            (item.store_slug, item.article): item
            for item in db.list_unit_economics_1c_product_settings(store_slugs)
        }
        references = {
            (str(item["store_slug"]), str(item["article"])): item
            for item in db.get_unit_economics_1c_product_reference_rows(store_slugs)
        }
        rows: list[dict] = []
        daily_contexts: dict[tuple[str, str], dict] = {}
        subjects: set[str] = set()
        managers: set[str] = set()
        article_options: list[dict] = []
        available_managers = {
            str(reference.get("manager") or "").strip()
            for reference in references.values()
            if str(reference.get("manager") or "").strip()
        }
        allowed_manager_keys: set[str] | None = None
        if current_user is not None and current_user.role == Role.USER:
            allowed_manager_keys = {
                _manager_identity_key(manager)
                for manager in available_managers
                if _manager_matches_user(manager, current_user)
            }
        for store_slug in store_slugs:
            cabinet = cabinets[store_slug]
            for catalog_item in db.get_stock_items(store_slug, "WB"):
                article = str(catalog_item.get("article") or "")
                reference = references.get((store_slug, article)) or {}
                subject = str(reference.get("category") or "").strip() or "Без предмета"
                manager = str(reference.get("manager") or "").strip()
                manager_key = _manager_identity_key(manager)
                if allowed_manager_keys is not None and manager_key not in allowed_manager_keys:
                    continue
                subjects.add(subject)
                if manager:
                    managers.add(manager)
                article_options.append(
                    {
                        "article": article,
                        "name": str(catalog_item.get("name") or article),
                        "store_slug": store_slug,
                        "store_name": STORES[store_slug]["name"],
                        "image_url": str(catalog_item.get("image_url") or ""),
                    }
                )
                if selected_subjects and subject.casefold() not in selected_subjects:
                    continue
                if selected_managers and manager_key not in selected_managers:
                    continue
                if selected_articles and not {
                    article.casefold(),
                    _nm_id(article).casefold(),
                }.intersection(selected_articles):
                    continue
                period_product_metrics = metrics.get((store_slug, _nm_id(article)))
                if period_product_metrics is None:
                    period_product_metrics = unit_economics_1c.empty_product_metrics(
                        period_days=period_days,
                        today=date_to,
                    )
                effective_product_settings = product_settings.get((store_slug, article))
                if effective_product_settings is None:
                    effective_product_settings = UnitEconomics1CProductSettings(
                        store_slug=store_slug,
                        article=article,
                    )
                product = _unit_economics_1c_mock_product(
                    store_slug=store_slug,
                    product=catalog_item,
                    price_snapshot=prices.get((store_slug, article)),
                    acquiring_percent=cabinet.acquiring_percent,
                    product_metrics=period_product_metrics,
                    product_settings=effective_product_settings,
                    acceptance_coefficient=cabinet.acceptance_coefficient,
                    team_commission_percent=cabinet.team_commission_percent,
                    vat_percent=cabinet.vat_percent,
                    usn_percent=cabinet.usn_percent,
                    osno_percent=cabinet.osno_percent,
                    tax_system=cabinet.tax_system,
                    product_reference=reference,
                    wb_extra_tariff_percent=cabinet.wb_extra_tariff_percent,
                    stock_order_metrics=stock_metrics.get((store_slug, _nm_id(article))),
                    history_days=0,
                )
                advertising = product["advertising"]
                details = product["details"]
                product_daily_orders = (
                    daily_orders_by_product.get((store_slug, _nm_id(article))) or {}
                    if for_export
                    else {
                        str(item["date"]): item
                        for item in period_product_metrics.get("daily") or []
                        if isinstance(item, dict) and item.get("date")
                    }
                )
                product_daily_advertising = {
                    str(item.get("date")): max(float(item.get("advertising_spend") or 0), 0.0)
                    for item in period_product_metrics.get("daily") or []
                    if isinstance(item, dict) and item.get("date")
                }
                funnel_totals = {
                    "orders_count": sum(
                        int(item.get("orders_count") or 0)
                        for item in product_daily_orders.values()
                    ),
                    "orders_amount": round(
                        sum(float(item.get("orders_amount") or 0) for item in product_daily_orders.values()),
                        2,
                    ),
                    "cancel_count": sum(
                        int(item.get("cancel_count") or 0)
                        for item in product_daily_orders.values()
                    ),
                    "cancel_amount": round(
                        sum(float(item.get("cancel_amount") or 0) for item in product_daily_orders.values()),
                        2,
                    ),
                }
                funnel_totals["net_orders_count"] = (
                    funnel_totals["orders_count"] - funnel_totals["cancel_count"]
                )
                funnel_totals["net_orders_amount"] = round(
                    funnel_totals["orders_amount"] - funnel_totals["cancel_amount"],
                    2,
                )
                live_snapshot = None
                if date_from <= live_day <= date_to:
                    live_snapshot = unit_economics_1c_history.calculate_snapshot_row(
                        snapshot_day=live_day,
                        store_slug=store_slug,
                        article=article,
                        price_snapshot=prices.get((store_slug, article)) or {},
                        product_metrics=(
                            live_metrics.get((store_slug, _nm_id(article)))
                            or unit_economics_1c.empty_product_metrics(today=live_day)
                        ),
                        product_settings=effective_product_settings,
                        product_reference=reference,
                        cabinet=cabinet,
                        captured_at=datetime.now(UTC).isoformat(),
                    )
                historical_economics = _report_historical_economics(
                    date_from=date_from,
                    date_to=date_to,
                    daily_orders=product_daily_orders,
                    margin_snapshots=margin_snapshots_by_product.get((store_slug, article)) or {},
                    live_day=live_day,
                    live_unit_margin=(
                        _price_value(live_snapshot.get("unit_margin")) if live_snapshot else None
                    ),
                    live_purchase_price=(
                        _price_value(live_snapshot.get("purchase_price")) if live_snapshot else None
                    ),
                    daily_advertising=product_daily_advertising,
                    fallback_buyout_percent=period_product_metrics.get("buyout_percent"),
                )
                report_advertising_per_unit = (
                    round(
                        historical_economics["advertising_spend"]
                        / historical_economics["orders"],
                        2,
                    )
                    if historical_economics["orders"] > 0
                    else 0.0
                )
                daily_contexts[(store_slug, article)] = {
                    "daily_orders": product_daily_orders,
                    "margin_snapshots": (
                        margin_snapshots_by_product.get((store_slug, article)) or {}
                    ),
                    "live_day": live_day,
                    "live_snapshot": live_snapshot,
                    "daily_advertising": product_daily_advertising,
                    "fallback_buyout_percent": period_product_metrics.get("buyout_percent"),
                }
                rows.append(
                    {
                        "store_slug": store_slug,
                        "store_name": product["store_name"],
                        "article": article,
                        "name": product["name"],
                        "image_url": product["image_url"],
                        "subject": subject,
                        "manager": manager or None,
                        "orders_count": funnel_totals["orders_count"],
                        "orders_amount": funnel_totals["orders_amount"],
                        "cancel_count": funnel_totals["cancel_count"],
                        "cancel_amount": funnel_totals["cancel_amount"],
                        "net_orders_count": funnel_totals["net_orders_count"],
                        "net_orders_amount": funnel_totals["net_orders_amount"],
                        "buyout_percent": historical_economics["buyout_percent"],
                        "buyout_count": period_product_metrics.get("buyout_count"),
                        "buyout_amount": period_product_metrics.get("buyout_amount"),
                        "buyout_orders_count": historical_economics["buyout_orders_count"],
                        "buyout_orders_amount": funnel_totals["orders_amount"],
                        "buyout_cancel_count": advertising["buyout_cancel_count"],
                        "buyout_cancel_amount": advertising["buyout_cancel_amount"],
                        "buyout_net_orders_count": advertising["buyout_net_orders_count"],
                        "buyout_net_orders_amount": advertising["buyout_net_orders_amount"],
                        "buyout_period_from": advertising["buyout_period_from"],
                        "buyout_period_to": advertising["buyout_period_to"],
                        "buyout_updated_at": advertising["buyout_updated_at"],
                        "buyout_source_version": advertising["buyout_source_version"],
                        "funnel_updated_at": advertising["funnel_updated_at"],
                        "funnel_source_version": advertising["funnel_source_version"],
                        "funnel_vendor_code": advertising["funnel_vendor_code"],
                        "funnel_product_name": advertising["funnel_product_name"],
                        "funnel_period_from": advertising["period_from"],
                        "funnel_period_to": advertising["period_to"],
                        "stock": product["stock"]["total"],
                        "impressions": advertising["impressions"],
                        "clicks": advertising["clicks"],
                        "ctr": advertising["ctr"],
                        "cpc": advertising["cpc"],
                        "advertising_spend": historical_economics["advertising_spend"],
                        "advertising_per_unit": report_advertising_per_unit,
                        "expected_buyout_amount": historical_economics["expected_buyout_amount"],
                        "drr": historical_economics["drr"],
                        "retail_price": details["retail_price_used"],
                        "customer_price": details["customer_price_used"],
                        "customer_price_source": details["customer_price_source"],
                        "average_order_price": details["average_order_price"],
                        "acquiring_percent": details["acquiring"],
                        "acquiring_value": details["acquiring_value"],
                        "delivery_wb_rub": details["delivery_wb_rub"],
                        "return_cost_rub": details["return_cost_rub"],
                        "volume_l": details["volume_l"],
                        "acceptance_coefficient": details["acceptance_coefficient"],
                        "paid_acceptance_cost": details["paid_acceptance_cost"],
                        "logistics_buyout_percent": details["buyout_percent"],
                        "delivery_with_returns": details["delivery_with_returns"],
                        "storage_wb_rub": details["storage_wb_rub"],
                        "storage_days": details["storage_days"],
                        "storage_sum": details["storage_sum"],
                        "subject_commission_percent": details["subject_commission_percent"],
                        "wb_extra_tariff_percent": details["wb_extra_tariff_percent"],
                        "commission_percent": details["commission_percent"],
                        "commission_value": details["commission_value"],
                        "purchase_cost": details["purchase_cost"],
                        "fulfillment_cost": details["fulfillment_cost"],
                        "team_commission_percent": details["team_commission_percent"],
                        "team_commission_value": details["team_commission_value"],
                        "tax_system": details["tax_system"],
                        "vat_percent": details["vat_percent"],
                        "vat_value": details["vat_value"],
                        "usn_percent": details["usn_percent"],
                        "usn_value": details["usn_value"],
                        "osno_percent": details["osno_percent"],
                        "osno_value": details["osno_value"],
                        "tax_value": details["tax_value"],
                        "net_revenue": details["net_revenue"],
                        "margin_orders_count": historical_economics["orders"],
                        "margin": historical_economics["margin"],
                        "purchase_value": historical_economics["purchase_value"],
                        "roi": historical_economics["roi"],
                        "margin_complete": historical_economics["complete"],
                        "margin_missing_days": historical_economics["missing_days"],
                        "daily_calculations": [],
                    }
                )
        rows.sort(
            key=lambda row: (
                -float(row["orders_amount"] or 0),
                row["name"].casefold(),
                row["article"],
            )
        )
        totals = _unit_profit_report_totals(rows)
        category_rows = _unit_profit_category_rows(rows) if group_by == "subject" else []
        all_view_rows = category_rows if group_by == "subject" else rows
        total_count = len(all_view_rows)
        pagination_enabled = include_daily_details and group_by == "product" and not for_export
        total_pages = (
            max((total_count + page_size - 1) // page_size, 1)
            if pagination_enabled
            else 1
        )
        effective_page = min(page, total_pages) if pagination_enabled else 1
        if pagination_enabled:
            offset = (effective_page - 1) * page_size
            page_rows = all_view_rows[offset : offset + page_size]
        else:
            page_rows = all_view_rows

        if include_daily_details:
            if group_by == "subject":
                visible_subjects = {str(row.get("subject") or "") for row in page_rows}
                detail_products = [
                    row for row in rows if str(row.get("subject") or "") in visible_subjects
                ]
            else:
                detail_products = page_rows
            for row in detail_products:
                context = daily_contexts[(str(row["store_slug"]), str(row["article"]))]
                row["daily_calculations"] = _report_daily_calculations(
                    date_from=date_from,
                    date_to=date_to,
                    **context,
                )
            if group_by == "subject":
                detailed_categories = {
                    str(row["subject"]): row
                    for row in _unit_profit_category_rows(detail_products)
                }
                page_rows = [
                    detailed_categories.get(str(row.get("subject") or ""), row)
                    for row in page_rows
                ]
                if for_export:
                    category_rows = page_rows

        included_keys = {(row["store_slug"], _nm_id(row["article"])) for row in rows}
        funnel_daily_rows = (
            [
                row
                for row in saved_funnel_daily_rows
                if (str(row["store_slug"]), str(row["article"])) in included_keys
            ]
            if for_export
            else []
        )
        funnel_weekly_rows = (
            [
                row
                for row in saved_funnel_weekly_rows
                if (str(row["store_slug"]), str(row["article"])) in included_keys
            ]
            if for_export
            else []
        )
        return {
            "ok": True,
            "period_from": date_from.isoformat(),
            "period_to": date_to.isoformat(),
            "rows": rows if for_export else page_rows,
            "category_rows": category_rows if for_export else [],
            "totals": totals,
            "group_by": group_by,
            "daily_details": include_daily_details,
            "funnel_daily_rows": funnel_daily_rows,
            "funnel_weekly_rows": funnel_weekly_rows,
            "pagination": {
                "enabled": pagination_enabled,
                "page": effective_page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": total_pages,
            },
            "filters": {
                "subjects": sorted(subjects),
                "managers": sorted(managers),
                "articles": sorted(
                    article_options,
                    key=lambda item: (item["name"].casefold(), item["article"]),
                ),
            },
            "manager_scope": {
                "restricted": allowed_manager_keys is not None,
                "matched": bool(allowed_manager_keys) if allowed_manager_keys is not None else True,
            },
        }

    return await run_in_threadpool(load_report)


@router.get("/api/unit-economics-1c/reports/unit-profit")
async def unit_economics_1c_unit_profit_report_data(request: Request):
    return await _unit_economics_1c_unit_profit_report_data(request)


@router.get("/sales/unit-economics-1c/reports/unit-profit.xlsx")
async def unit_economics_1c_unit_profit_report_xlsx(request: Request):
    report = await _unit_economics_1c_unit_profit_report_data(request, for_export=True)
    if isinstance(report, JSONResponse):
        return report
    content, filename = await run_in_threadpool(
        unit_economics_1c_report_export.build_xlsx,
        report,
    )
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


@router.put("/api/unit-economics-1c/preferences/columns")
async def unit_economics_1c_column_preferences_save(
    request: Request,
    payload: UnitEconomics1CColumnPreferencesRequest,
):
    preferences = _validated_column_preferences(payload.model_dump(mode="json"))
    if preferences is None:
        return JSONResponse({"ok": False, "error": "Некорректные настройки колонок"}, status_code=422)
    saved = await run_in_threadpool(
        db.save_ui_preference,
        int(request.state.user["id"]),
        UNIT_ECONOMICS_COLUMNS_PREFERENCE_SCOPE,
        preferences,
    )
    return {"ok": True, "preferences": saved}


@router.put("/api/unit-economics-1c/product-settings/{store_slug}")
async def unit_economics_1c_product_settings_save(
    request: Request,
    store_slug: str,
    payload: UnitEconomics1CProductSettingsRequest,
):
    normalized_store = store_slug.lower()
    if normalized_store not in STORES:
        return JSONResponse({"ok": False, "error": "Кабинет не найден"}, status_code=404)
    if not has_scope(request.state.user, normalized_store, "WB"):
        return JSONResponse({"ok": False, "error": "Нет доступа к этому кабинету"}, status_code=403)
    if not has_section_access(
        request.state.user,
        SectionName.UNIT_ECONOMICS_1C,
        SectionAccessLevel.WRITE,
    ):
        return JSONResponse({"ok": False, "error": "Нет права изменять параметры"}, status_code=403)

    catalog = await run_in_threadpool(db.get_catalog_items, normalized_store, "WB")
    if payload.article not in {str(item.get("article") or "") for item in catalog}:
        return JSONResponse({"ok": False, "error": "Товар не найден в актуальном каталоге"}, status_code=404)

    updated_at = datetime.now(UTC).isoformat()
    user = request.state.user

    def save() -> dict:
        saved = db.save_unit_economics_1c_product_settings(
            normalized_store,
            payload,
            updated_at=updated_at,
            updated_by_user_id=int(user["id"]),
            updated_by_name=str(user["full_name"]),
        )
        cabinet = db.get_unit_economics_1c_cabinet_settings(normalized_store)
        acceptance = unit_economics_1c.calculate_paid_acceptance_cost(
            saved.volume_l,
            cabinet.acceptance_coefficient,
        )
        funnel_metric = next(
            (
                row
                for row in db.get_unit_economics_1c_funnel_product_metrics((normalized_store,))
                if str(row["article"]) == _nm_id(payload.article)
            ),
            {},
        )
        buyout_percent = min(max(float(funnel_metric.get("buyout_percent") or 0), 0.0), 100.0)
        delivery = unit_economics_1c.calculate_delivery_with_returns(
            saved.delivery_wb_rub,
            buyout_percent,
            saved.return_cost_rub,
            acceptance,
        )
        db.log_action(
            int(user["id"]),
            str(user["full_name"]),
            "unit_economics_1c_product_settings",
            f"Сохранены параметры товара {payload.article} ({STORES[normalized_store]['name']})",
            updated_at,
        )
        return {
            **saved.model_dump(mode="json"),
            "buyout_percent": buyout_percent,
            "paid_acceptance_cost": acceptance,
            "delivery_with_returns": delivery,
        }

    return {"ok": True, "settings": await run_in_threadpool(save)}


@router.get("/sales/unit-economics-1c/cabinet-settings", response_class=HTMLResponse)
async def sales_unit_economics_1c_cabinet_settings(request: Request):
    store_slugs = accessible_stores(request.state.user, "WB")
    settings = await run_in_threadpool(_cabinet_settings_payload, store_slugs)
    content = fill_template(
        "unit_economics_1c_cabinet_settings_content.html",
        cabinet_settings_config=json.dumps(
            {
                "marketplace": "WB",
                "canEdit": has_section_access(
                    request.state.user,
                    SectionName.UNIT_ECONOMICS_1C,
                    SectionAccessLevel.WRITE,
                ),
                "items": settings,
            },
            ensure_ascii=False,
        ).replace("</", "<\\/"),
    )
    return render_page(
        "CheckStock — Юнит-экономика 1С — Ввод данных по кабинетам",
        "unit_1c_settings",
        content,
        request.state.user,
        content_class="content--unit-1c-settings",
    )


@router.get("/api/unit-economics-1c/cabinet-settings")
async def unit_economics_1c_cabinet_settings_data(request: Request, marketplace: str = "WB"):
    if marketplace.upper() != "WB":
        return JSONResponse(
            {"ok": False, "error": "Пока поддерживаются только кабинеты WB"},
            status_code=400,
        )
    store_slugs = accessible_stores(request.state.user, "WB")
    settings = await run_in_threadpool(_cabinet_settings_payload, store_slugs)
    return {"ok": True, "marketplace": "WB", "items": settings}


@router.post("/api/unit-economics-1c/source-data/sync")
async def unit_economics_1c_source_data_sync(request: Request):
    current_user = coerce_user(request.state.user)
    if current_user is not None and current_user.access_profile is not None:
        return JSONResponse(
            {"ok": False, "error": "Общую загрузку данных 1С запускает администратор"},
            status_code=403,
        )
    if not has_section_access(
        request.state.user,
        SectionName.UNIT_ECONOMICS_1C,
        SectionAccessLevel.WRITE,
    ):
        return JSONResponse(
            {"ok": False, "error": "Нет права запускать загрузку себестоимости"},
            status_code=403,
        )
    store_slugs = accessible_stores(request.state.user, "WB")
    user = request.state.user

    def sync() -> dict:
        report = run_tracked(
            "unit_economics_1c_source_sync",
            "manual",
            unit_economics_1c_source.sync_all,
        )
        db.log_action(
            int(user["id"]),
            str(user["full_name"]),
            "unit_economics_1c_source_sync",
            f"Внепланово обновлены данные 1С из Google Sheets: {report['saved']} товаров",
            datetime.now(UTC).isoformat(),
        )
        return {
            "report": report,
            "items": _cabinet_settings_payload(store_slugs),
        }

    try:
        result = await run_in_threadpool(sync)
    except unit_economics_1c_source.SourceDataError as error:
        logger.warning("unit_economics_1c_manual_source_sync_failed error=%s", error)
        return JSONResponse(
            {"ok": False, "error": f"Не удалось загрузить данные из Google Sheets: {error}"},
            status_code=502,
        )
    return {"ok": True, **result}


@router.put("/api/unit-economics-1c/cabinet-settings/{store_slug}")
async def unit_economics_1c_cabinet_settings_save(
    request: Request,
    store_slug: str,
    payload: UnitEconomics1CCabinetSettingsWebRequest,
):
    normalized_store = store_slug.lower()
    if normalized_store not in STORES:
        return JSONResponse({"ok": False, "error": "Кабинет не найден"}, status_code=404)
    if not has_scope(request.state.user, normalized_store, "WB"):
        return JSONResponse(
            {"ok": False, "error": "Нет доступа к этому кабинету"},
            status_code=403,
        )
    updated_at = datetime.now(UTC).isoformat()
    user = request.state.user
    if normalized_store != "gogol" or payload.tax_system == "usn":
        effective_payload = payload.model_copy(update={"tax_system": "usn", "osno_percent": 0})
    else:
        effective_payload = payload.model_copy(update={"usn_percent": 0})

    def save() -> dict:
        settings = db.save_unit_economics_1c_cabinet_settings(
            normalized_store,
            effective_payload,
            updated_at=updated_at,
            updated_by_user_id=int(user["id"]),
            updated_by_name=str(user["full_name"]),
        )
        db.log_action(
            int(user["id"]),
            str(user["full_name"]),
            "unit_economics_1c_cabinet_settings",
            f"Сохранены параметры WB-кабинета {STORES[normalized_store]['name']}",
            updated_at,
        )
        return {
            **settings.model_dump(mode="json"),
            "store_name": STORES[normalized_store]["name"],
            "store_initials": STORES[normalized_store]["initials"],
            "store_color": STORES[normalized_store]["color"],
            "store_text": STORES[normalized_store]["text"],
        }

    settings = await run_in_threadpool(save)
    return {"ok": True, "settings": settings}


def _render_unit_economics_1c_placeholder(
    request: Request,
    *,
    title: str,
    active: str,
    logo: str,
    logo_class: str,
    heading: str,
) -> str:
    content = fill_template(
        "unit_economics_1c_placeholder_content.html",
        logo=logo,
        logo_class=logo_class,
        heading=heading,
    )
    return render_page(
        title,
        active,
        content,
        request.state.user,
        content_class="content--unit-1c",
    )


@router.get("/sales/unit-economics-1c/ozon", response_class=HTMLResponse)
async def sales_unit_economics_1c_ozon(request: Request):
    return _render_unit_economics_1c_placeholder(
        request,
        title="CheckStock — Юнит-экономика 1С — Ozon",
        active="unit_1c_ozon",
        logo="OZON",
        logo_class="ozon",
        heading="Раздел Ozon в разработке",
    )


@router.get("/sales/unit-economics-1c/yandex-market", response_class=HTMLResponse)
async def sales_unit_economics_1c_yandex(request: Request):
    return _render_unit_economics_1c_placeholder(
        request,
        title="CheckStock — Юнит-экономика 1С — Яндекс Маркет",
        active="unit_1c_yandex",
        logo="Я",
        logo_class="ym",
        heading="Раздел Яндекс Маркета в разработке",
    )
