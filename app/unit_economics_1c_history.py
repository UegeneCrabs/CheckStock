"""Immutable daily unit-margin snapshots used by period reports."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta

from app import db, unit_economics_1c
from app.domain import MOSCOW_TIMEZONE
from app.dto.unit_economics_1c import UnitEconomics1CProductSettings
from app.stores import STORES

logger = logging.getLogger(__name__)

CALCULATION_VERSION = 2


def _price_value(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _nm_id(article: object) -> str:
    return str(article or "").partition(" / ")[0].strip()


def _json_object(value: object) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def unit_margin_without_advertising(
    snapshot: dict,
    *,
    buyout_percent: float | None,
) -> float | None:
    """Resolve historical per-unit margin before advertising for a report day."""

    stored_margin = _price_value(snapshot.get("unit_margin"))
    if stored_margin is None:
        return None
    inputs = _json_object(snapshot.get("inputs_json"))
    effective_buyout_percent = (
        buyout_percent
        if buyout_percent is not None
        else _price_value(inputs.get("buyout_percent"))
    )
    if inputs:
        try:
            delivery_with_returns = unit_economics_1c.calculate_delivery_with_returns(
                inputs.get("delivery_wb_rub"),
                effective_buyout_percent or 0,
                inputs.get("return_cost_rub"),
                inputs.get("paid_acceptance_cost"),
            )
            recalculated = unit_economics_1c.calculate_unit_profit(
                retail_price=inputs.get("retail_price"),
                customer_price=inputs.get("customer_price"),
                acquiring_percent=inputs.get("acquiring_percent"),
                delivery_with_returns=delivery_with_returns,
                storage_wb_rub=inputs.get("storage_wb_rub"),
                turnover_days=inputs.get("turnover_days"),
                wb_commission_percent=inputs.get("commission_percent"),
                advertising_rub=0,
                purchase_price=inputs.get("purchase_price"),
                fulfillment_cost=inputs.get("fulfillment_cost"),
                team_commission_percent=inputs.get("team_commission_percent"),
                vat_percent=inputs.get("vat_percent"),
                usn_percent=inputs.get("usn_percent"),
                osno_percent=inputs.get("osno_percent"),
                tax_system=str(inputs.get("tax_system") or "usn"),
            )
        except (TypeError, ValueError):
            recalculated = None
        if recalculated is not None:
            return _price_value(recalculated.get("margin"))
    try:
        calculation_version = int(snapshot.get("calculation_version") or CALCULATION_VERSION)
    except (TypeError, ValueError):
        calculation_version = CALCULATION_VERSION
    if calculation_version >= 2:
        return stored_margin
    result = _json_object(snapshot.get("result_json"))
    legacy_advertising = _price_value(result.get("advertising"))
    if legacy_advertising is None:
        legacy_advertising = _price_value(inputs.get("advertising_per_unit")) or 0.0
    return round(stored_margin + legacy_advertising, 2)


def snapshot_buyout_percent(snapshot: dict | None) -> float | None:
    """Return the weekly buyout value captured for a historical report day."""

    if snapshot is None:
        return None
    value = _price_value(_json_object(snapshot.get("inputs_json")).get("buyout_percent"))
    return value if value is not None and value > 0 else None


def calculate_snapshot_row(
    *,
    snapshot_day: date,
    store_slug: str,
    article: str,
    price_snapshot: dict,
    product_metrics: dict,
    product_settings: UnitEconomics1CProductSettings,
    product_reference: dict,
    cabinet: object,
    captured_at: str,
) -> dict | None:
    """Calculate the same resolved per-unit margin that is shown for the current day."""

    spp_price = _price_value(price_snapshot.get("customer_price_with_spp"))
    retail_price = _price_value(price_snapshot.get("retail_price"))
    orders_count = max(_integer(product_metrics.get("orders_count")), 0)
    average_customer_price = (
        round(float(product_metrics.get("orders_amount") or 0) / orders_count, 2)
        if orders_count
        else None
    )
    customer_price = (
        spp_price
        if spp_price is not None
        else retail_price
        if retail_price is not None
        else average_customer_price
    )
    economics_retail_price = retail_price or _price_value(product_metrics.get("average_retail_price"))
    if economics_retail_price is None:
        economics_retail_price = customer_price
    if economics_retail_price is None:
        return None

    raw_buyout_percent = product_metrics.get("range_buyout_percent")
    if raw_buyout_percent is None or float(raw_buyout_percent) <= 0:
        raw_buyout_percent = product_metrics.get("buyout_percent")
    buyout_percent = round(min(max(float(raw_buyout_percent or 0), 0.0), 100.0), 2)
    paid_acceptance_cost = unit_economics_1c.calculate_paid_acceptance_cost(
        product_settings.volume_l,
        float(getattr(cabinet, "acceptance_coefficient", 0) or 0),
    )
    delivery_with_returns = unit_economics_1c.calculate_delivery_with_returns(
        product_settings.delivery_wb_rub,
        buyout_percent,
        product_settings.return_cost_rub,
        paid_acceptance_cost,
    )
    turnover_days = _integer(product_reference.get("turnover_days"), 21)
    purchase_price = _price_value(product_reference.get("purchase_price")) or 0.0
    fulfillment_cost = _price_value(product_reference.get("fulfillment_cost")) or 0.0
    source_team_commission = _price_value(product_reference.get("team_commission_percent"))
    team_commission_percent = (
        source_team_commission
        if source_team_commission is not None
        else round(float(getattr(cabinet, "team_commission_percent", 0) or 0), 2)
    )
    subject_commission_percent = _price_value(product_reference.get("subject_commission_percent")) or 0.0
    wb_extra_tariff_percent = round(
        max(float(getattr(cabinet, "wb_extra_tariff_percent", 0) or 0), 0.0),
        2,
    )
    commission_percent = round(subject_commission_percent + wb_extra_tariff_percent, 2)
    advertising_per_unit = unit_economics_1c.calculate_advertising_per_unit(
        float(product_metrics.get("spend") or 0),
        orders_count,
        buyout_percent,
    )
    tax_system = str(getattr(cabinet, "tax_system", "usn") or "usn").lower()
    effective_tax_system = "osno" if store_slug == "gogol" and tax_system == "osno" else "usn"
    vat_percent = max(float(getattr(cabinet, "vat_percent", 0) or 0), 0.0)
    usn_percent = max(float(getattr(cabinet, "usn_percent", 0) or 0), 0.0)
    osno_percent = max(float(getattr(cabinet, "osno_percent", 0) or 0), 0.0)

    result = unit_economics_1c.calculate_unit_profit(
        retail_price=economics_retail_price,
        customer_price=customer_price,
        acquiring_percent=float(getattr(cabinet, "acquiring_percent", 0) or 0),
        delivery_with_returns=delivery_with_returns,
        storage_wb_rub=product_settings.storage_wb_rub,
        turnover_days=turnover_days,
        wb_commission_percent=commission_percent,
        advertising_rub=0,
        purchase_price=purchase_price,
        fulfillment_cost=fulfillment_cost,
        team_commission_percent=team_commission_percent,
        vat_percent=vat_percent,
        usn_percent=usn_percent,
        osno_percent=osno_percent,
        tax_system=effective_tax_system,
    )
    if result is None:
        return None

    inputs = {
        "retail_price": economics_retail_price,
        "customer_price": customer_price,
        "customer_price_with_spp": spp_price,
        "average_customer_price": average_customer_price,
        "price_day": price_snapshot.get("day"),
        "price_updated_at": price_snapshot.get("updated_at"),
        "acquiring_percent": float(getattr(cabinet, "acquiring_percent", 0) or 0),
        "delivery_wb_rub": product_settings.delivery_wb_rub,
        "return_cost_rub": product_settings.return_cost_rub,
        "volume_l": product_settings.volume_l,
        "acceptance_coefficient": float(getattr(cabinet, "acceptance_coefficient", 0) or 0),
        "paid_acceptance_cost": paid_acceptance_cost,
        "delivery_with_returns": delivery_with_returns,
        "storage_wb_rub": product_settings.storage_wb_rub,
        "turnover_days": turnover_days,
        "subject_commission_percent": subject_commission_percent,
        "wb_extra_tariff_percent": wb_extra_tariff_percent,
        "commission_percent": commission_percent,
        "advertising_spend": round(float(product_metrics.get("spend") or 0), 2),
        "advertising_orders_count": orders_count,
        "advertising_per_unit": advertising_per_unit,
        "advertising_included_in_unit_margin": False,
        "buyout_percent": buyout_percent,
        "buyout_period_from": product_metrics.get("buyout_period_from"),
        "buyout_period_to": product_metrics.get("buyout_period_to"),
        "purchase_price": purchase_price,
        "fulfillment_cost": fulfillment_cost,
        "team_commission_percent": team_commission_percent,
        "vat_percent": vat_percent,
        "usn_percent": usn_percent,
        "osno_percent": osno_percent,
        "tax_system": effective_tax_system,
        "source_synced_at": product_reference.get("source_synced_at"),
        "product_settings_updated_at": product_settings.updated_at,
        "cabinet_settings_updated_at": getattr(cabinet, "updated_at", None),
    }
    return {
        "store_slug": store_slug,
        "article": article,
        "day": snapshot_day.isoformat(),
        "marketplace": "WB",
        "unit_margin": result["margin"],
        "purchase_price": purchase_price,
        "price_day": price_snapshot.get("day"),
        "calculation_version": CALCULATION_VERSION,
        "inputs_json": json.dumps(inputs, ensure_ascii=False, sort_keys=True),
        "result_json": json.dumps(result, ensure_ascii=False, sort_keys=True),
        "captured_at": captured_at,
    }


def save_daily_margin_snapshots(
    snapshot_day: date | None = None,
    *,
    store_slugs: tuple[str, ...] | None = None,
    overwrite: bool = False,
) -> dict:
    """Close one business day using values effective no later than that day."""

    day = snapshot_day or (datetime.now(MOSCOW_TIMEZONE).date() - timedelta(days=1))
    stores = tuple(
        store for store in (tuple(STORES) if store_slugs is None else store_slugs)
        if store in STORES
    )
    captured_at = datetime.now(UTC).isoformat()
    prices = {
        (str(row["store_slug"]), str(row["article"])): row
        for row in db.get_unit_economics_1c_daily_prices_as_of(stores, day.isoformat())
    }
    metrics = unit_economics_1c.load_product_metrics(
        stores,
        period_days=1,
        today=day,
    )
    cabinets = {item.store_slug: item for item in db.list_unit_economics_1c_cabinet_settings(stores)}
    product_settings = {
        (item.store_slug, item.article): item
        for item in db.list_unit_economics_1c_product_settings(stores)
    }
    references = {
        (str(row["store_slug"]), str(row["article"])): row
        for row in db.get_unit_economics_1c_product_reference_rows(stores)
    }
    rows: list[dict] = []
    skipped = 0
    for store_slug in stores:
        cabinet = cabinets[store_slug]
        for product in db.get_stock_items(store_slug, "WB"):
            article = str(product.get("article") or "").strip()
            if not article:
                continue
            settings = product_settings.get((store_slug, article)) or UnitEconomics1CProductSettings(
                store_slug=store_slug,
                article=article,
            )
            row = calculate_snapshot_row(
                snapshot_day=day,
                store_slug=store_slug,
                article=article,
                price_snapshot=prices.get((store_slug, article)) or {},
                product_metrics=(
                    metrics.get((store_slug, _nm_id(article)))
                    or unit_economics_1c.empty_product_metrics(today=day)
                ),
                product_settings=settings,
                product_reference=references.get((store_slug, article)) or {},
                cabinet=cabinet,
                captured_at=captured_at,
            )
            if row is None:
                skipped += 1
                continue
            rows.append(row)
    saved = db.save_unit_economics_1c_daily_margin_snapshots(rows, overwrite=overwrite)
    report = {
        "ok": True,
        "day": day.isoformat(),
        "candidates": len(rows),
        "saved": saved,
        "skipped_without_price": skipped,
        "overwrite": overwrite,
    }
    logger.info("unit_economics_1c_daily_margin_snapshot %s", report)
    return report
