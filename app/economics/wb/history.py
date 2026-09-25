"""Immutable daily unit-margin snapshots used by period reports."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime

from app import db
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.dto.unit_economics_1c import UnitEconomics1CProductSettings
from app.economics.daily_calculation import calculate as calculate_daily
from app.economics.wb import calculations as unit_economics_1c
from app.repositories import daily_economics

logger = logging.getLogger(__name__)

CALCULATION_VERSION = 3


def _price_value(value: object) -> float | None:
    if value is None:
        return None
    try:
        return unit_economics_1c.money(float(value))
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

    inputs = _json_object(snapshot.get("inputs_json"))
    if not inputs:
        return None
    # Only the captured day's inputs; no current buyout/default fallback.
    return calculate_daily("WB", inputs, snapshot.get("calculation_version"))[1].get("margin")


def report_day(day, snapshot, order, advertising, *, orders_known=False, ads_known=False):
    """One resolved day for the screen, report and export, with dated overlays."""
    inputs = _json_object((snapshot or {}).get("inputs_json"))
    overrides = (snapshot or {}).get("overrides") or {}
    count = overrides.get("orders_count", int(order.get("orders_count") or 0) if orders_known else None)
    spend = overrides.get("advertising_spend", advertising if ads_known else None)
    # Successful empty source days carry a real zero.
    if ads_known and spend is None:
        spend = 0.0
    values, result = calculate_daily("WB", {**inputs, "orders_count": count, "advertising_spend": spend}, (snapshot or {}).get("calculation_version"))
    missing = result["daily_missing"]
    from app.economics.completeness import describe

    return {"day": day, "inputs": values, "result": result, "profit": result["day_profit"],
            "purchase_value": result["purchase_value"], "orders_count": count,
            "advertising_spend": spend, "expected_buyouts": result["expected_buyouts"],
            "missing": missing, "complete": result["daily_complete"],
            "messages": ["Не учтены / неизвестны: " + ", ".join(describe(missing, day))] if missing else [],
            "snapshot_available": bool(snapshot)}


def snapshot_buyout_percent(snapshot: dict | None) -> float | None:
    """Return the configured-window buyout value captured for a report day."""

    if snapshot is None:
        return None
    value = _price_value(_json_object(snapshot.get("inputs_json")).get("buyout_percent"))
    return unit_economics_1c.money(min(max(value, 0.0), 100.0)) if value is not None else None


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
        unit_economics_1c.money(float(product_metrics.get("orders_amount") or 0) / orders_count) if orders_count else None
    )
    customer_price = spp_price if spp_price is not None else average_customer_price
    economics_retail_price = retail_price if retail_price is not None else _price_value(product_metrics.get("average_retail_price"))

    product_metrics = unit_economics_1c.apply_buyout_default(
        product_metrics,
        getattr(cabinet, "default_buyout_percent", None),
    )
    buyout_percent = product_metrics["buyout_percent"]
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
    turnover_days = _integer(product_reference["turnover_days"]) if product_reference.get("turnover_days") is not None else None
    purchase_price = _price_value(product_reference.get("purchase_price"))
    fulfillment_cost = _price_value(product_reference.get("fulfillment_cost"))
    source_team_commission = _price_value(product_reference.get("team_commission_percent"))
    team_commission_percent = (
        source_team_commission
        if source_team_commission is not None
        else unit_economics_1c.money(float(getattr(cabinet, "team_commission_percent", 0) or 0))
    )
    subject_commission_percent = _price_value(product_reference.get("subject_commission_percent"))
    wb_extra_tariff_percent = unit_economics_1c.money(
        max(float(getattr(cabinet, "wb_extra_tariff_percent", 0) or 0), 0.0)
    )
    commission_percent = unit_economics_1c.money(subject_commission_percent + wb_extra_tariff_percent) if subject_commission_percent is not None else None
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
        "advertising_spend": unit_economics_1c.money(float(product_metrics.get("spend") or 0)),
        "advertising_orders_count": orders_count,
        "advertising_per_unit": advertising_per_unit,
        "advertising_included_in_unit_margin": False,
        "buyout_percent": buyout_percent,
        "raw_buyout_percent": product_metrics["raw_buyout_percent"],
        "default_buyout_percent": product_metrics["default_buyout_percent"],
        "buyout_default_applied": product_metrics["buyout_default_applied"],
        "buyout_period_days": int(getattr(cabinet, "buyout_period_days", 14) or 14),
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
    # A DTO default is not evidence of a saved product setting. Keep real zeros.
    if product_settings.updated_at is None:
        for field in ("delivery_wb_rub", "return_cost_rub", "volume_l", "storage_wb_rub"):
            inputs[field] = None
    if getattr(cabinet, "updated_at", None) is None:
        for field in ("acquiring_percent", "acceptance_coefficient", "wb_extra_tariff_percent", "vat_percent", "usn_percent", "osno_percent", "tax_system"):
            inputs[field] = None
        if source_team_commission is None:
            inputs["team_commission_percent"] = None
    if product_metrics.get("raw_buyout_percent") is None and not product_metrics.get("buyout_default_applied"):
        inputs["buyout_percent"] = None
    if price_snapshot.get("day") and price_snapshot["day"] != snapshot_day.isoformat():
        # An old observation is not evidence of today's sale price.
        inputs["retail_price"] = None
        inputs["customer_price"] = None
        inputs["price_missing_reason"] = "Цена за день не загружена; последняя запись за " + str(price_snapshot["day"])
    inputs["orders_count"] = orders_count if product_metrics.get("snapshot_orders_known") else None
    inputs["advertising_spend"] = product_metrics.get("snapshot_advertising_spend")
    inputs, result = calculate_daily("WB", inputs, CALCULATION_VERSION)
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
        "raw_sources": {
            "prices": price_snapshot, "metrics": product_metrics, "reference": product_reference,
            "product_settings": product_settings.model_dump(mode="json"),
            "cabinet": cabinet.model_dump(mode="json") if hasattr(cabinet, "model_dump") else vars(cabinet),
        },
    }


def save_daily_margin_snapshots(
    snapshot_day: date | None = None,
    *,
    store_slugs: tuple[str, ...] | None = None,
    overwrite: bool = False,
) -> dict:
    """Observe the current business day; never backdate current configuration."""

    today = datetime.now(MOSCOW_TIMEZONE).date()
    day = snapshot_day or today
    if day != today:
        raise ValueError("Прошлые дни доступны только из сохранённых снимков. Используйте корректировку даты.")
    stores = tuple(
        store for store in (tuple(STORES) if store_slugs is None else store_slugs) if store in STORES
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
        (item.store_slug, item.article): item for item in db.list_unit_economics_1c_product_settings(stores)
    }
    references = {
        (str(row["store_slug"]), str(row["article"])): row
        for row in db.get_unit_economics_1c_product_reference_rows(stores)
    }
    from app.repositories.economics_coverage import wb_days

    coverage = wb_days(stores, day.isoformat(), day.isoformat())
    ads = {(str(r["store_slug"]), str(r["nm_id"])): r for r in db.get_unit_economics_1c_daily_advertising(stores, day.isoformat(), day.isoformat())}
    rows: list[dict] = []
    skipped = 0
    for store_slug in stores:
        cabinet = cabinets[store_slug]
        for product in db.get_catalog_items(store_slug, "WB"):
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
                    {**(metrics.get((store_slug, _nm_id(article)))
                    or unit_economics_1c.empty_product_metrics(today=day)),
                    "snapshot_orders_known": day.isoformat() in coverage[store_slug]["orders"],
                    "snapshot_advertising_spend": (ads.get((store_slug, _nm_id(article)), {}).get("spend", 0.0) if day.isoformat() in coverage[store_slug]["advertising"] else None)}
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
    saved = 0
    for row in rows:
        source = daily_economics.observation(
            json.loads(row["inputs_json"]), raw=row["raw_sources"], captured_at=captured_at,
            version=CALCULATION_VERSION,
            origins={
                key: "Настройки кабинета WB" if key == "team_commission_percent" and row["raw_sources"]["reference"].get(key) is None
                else "Google Sheets" if key in {"purchase_price", "fulfillment_cost", "team_commission_percent"}
                else "WB: дневная цена" if key in {"retail_price", "customer_price"}
                else "WB: дневные метрики" if key in {"orders_count", "advertising_spend"}
                else "Справочник комиссий WB" if key == "subject_commission_percent"
                else "Сохранённые настройки / справочник"
                for key in json.loads(row["inputs_json"])
            },
        )
        saved += int(daily_economics.capture(("WB", row["store_slug"], row["article"], row["day"]), source))
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
