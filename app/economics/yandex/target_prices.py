"""Separate YM goals and target-price solver using the existing YM calculator."""

import json
from datetime import datetime, timedelta
from decimal import Decimal

from app.core.domain import MOSCOW_TIMEZONE
from app.dto.unit_economics_1c import DEFAULT_TARGET_ROI_BY_CODE
from app.economics.report_cache import reports_cache, user_key
from app.economics.report_sources import ReportSources
from app.repositories import yandex_economics as repository
from app.yandex import economics
from app.yandex.economics_advertising import apply_calculator_drr, product_drr, weekly_history
from app.yandex.economics_calculation import calculate, money

TARGET_SCOPE = "REPORT_TARGETS"
DEFAULTS = {
    "target_drr_percent": 8.0,
    "target_roi_percent": 50.0,
    "target_roi_by_code": DEFAULT_TARGET_ROI_BY_CODE,
}


def goals(store, article="", *, cached=None):
    rows = repository.all_settings(store) if cached is None else cached
    cabinet = {**DEFAULTS, **rows.get(("", TARGET_SCOPE), {}).get("values", {})}
    product = rows.get((article, TARGET_SCOPE), {"revision": 0, "values": {}})
    return cabinet, product


def save_goals(store, article, changes, revision, actor):
    return repository.save_settings(store, article, TARGET_SCOPE, changes, revision, actor)


def solve(values, drr, roi):
    """Keep costs/discounts; find the minimum kopeck price reaching the target ROI."""
    seller, buyer, pay = (values.get(key) for key in ("seller_price", "buyer_price", "pay_price"))
    if not seller or not buyer or buyer > seller:
        raise ValueError("Нет корректной пары цен до СПП и с СПП.")
    purchase = values.get("purchase_price")
    if not purchase or purchase <= 0:
        raise ValueError("Нет положительной закупочной стоимости для расчёта ROI.")
    ratio = Decimal(str(buyer)) / Decimal(str(seller))
    pay_ratio = Decimal(str(pay)) / Decimal(str(seller)) if pay and pay <= buyer else None
    target = Decimal(str(purchase)) * Decimal(str(roi)) / 100

    def at(cents):
        price = Decimal(cents) / 100
        scenario = {
            **values,
            "seller_price": float(price),
            "buyer_price": money(price * ratio),
            "pay_price": money(price * pay_ratio) if pay_ratio is not None else None,
            "advertising_mode": "plan",
            "advertising_basis": "drr",
            "plan_drr": drr,
            "advertising_per_buyout": float(price)
            * drr
            / 100
            * float(values.get("buyout_percent") or 0)
            / 100,
        }
        apply_calculator_drr(scenario, {}, {}, {"plan_drr": drr})
        result = calculate(scenario, precise=True)
        if result["margin"] is None:
            raise ValueError(" ".join(result["messages"]))
        return scenario, result

    low, high = 1, 100_000_000_000
    if at(high)[1]["margin"] < target:
        raise ValueError("Цель недостижима в пределах 1 млрд ₽: уменьшите ДРР или расходы.")
    while low < high:
        middle = (low + high) // 2
        if at(middle)[1]["margin"] >= target:
            high = middle
        else:
            low = middle + 1
    scenario, result = at(low)
    result["margin"] = money(result["margin"])
    return scenario, result


def calculate_row(row, state, cabinet, saved, weekly, *, overrides=None):
    values = state["values"]
    code = row.get("code") or ""
    cabinet_roi = cabinet["target_roi_by_code"].get(code, cabinet["target_roi_percent"])
    product = {**saved.get("values", {}), **(overrides or {})}
    drr = product.get("target_drr_percent", cabinet["target_drr_percent"])
    roi = product.get("target_roi_percent", cabinet_roi)
    notes = []
    if not weekly["complete"]:
        notes.append(f"Заказы и реклама загружены за {weekly['days']} из 7 дней.")
    if not row["margin_complete"]:
        notes.append("История прибыли неполная: " + ", ".join(row["margin_missing_days"]))
    if state.get("tariff", {}).get("approximate"):
        notes.append("Используется последний сохранённый тариф ЯМ.")
    result = {
        **{
            key: row.get(key)
            for key in (
                "store_slug",
                "store_name",
                "article",
                "name",
                "image_url",
                "manager",
                "code",
                "orders_amount",
            )
        },
        "current_price": values.get("pay_price"),
        "current_drr": weekly["drr"],
        "current_roi": row["roi"],
        "target_price": None,
        "target_retail_price": None,
        "target_spp_price": None,
        "target_actual_roi": None,
        "target_drr": drr,
        "target_roi": roi,
        "cabinet_target_drr": cabinet["target_drr_percent"],
        "cabinet_target_roi": cabinet_roi,
        "target_overridden": bool(saved.get("values")),
        "target_revision": saved.get("revision", 0),
        "weekly": weekly,
        "current_drr_warnings": [weekly["message"]] if weekly.get("message") else [],
        "current_drr_notes": notes,
        "current_warnings": [],
        "current_notes": notes,
        "target_warnings": list(notes),
        "calculator_values": values,
        "source_values": values,
    }
    if not values.get("pay_price") or values.get("pay_price", 0) > (values.get("buyer_price") or 0):
        result["target_warnings"].append("Нет корректной цены с Яндекс Пэй: цена с Пэй не рассчитана.")
    try:
        target_values, calculation = solve(values, drr, roi)
    except ValueError as error:
        result["target_warnings"].append(str(error))
        return result
    result.update(
        target_price=target_values["pay_price"],
        target_retail_price=target_values["seller_price"],
        target_spp_price=target_values["buyer_price"],
        target_actual_roi=calculation["roi"],
        target_advertising_rub=calculation["costs"]["advertising"],
        calculator_values=target_values,
        target_calculation=calculation,
    )
    return result


def load(stores, user, *, article="", overrides=None, scenario=None, today=None):
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    return reports_cache.get(
        ("ym-target", tuple(stores), user_key(user), today, article,
         json.dumps(overrides, sort_keys=True), json.dumps(scenario, sort_keys=True)),
        lambda: _load(stores, user, article=article, overrides=overrides, scenario=scenario, today=today),
    )


def _load(stores, user, *, article="", overrides=None, scenario=None, today=None):
    from app.economics.yandex.reports import load_rows

    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    start, end = today - timedelta(days=7), today - timedelta(days=1)
    sources = ReportSources()
    collected = {}
    report_rows = load_rows(
        stores, user, start, end, today=today, article=article, for_target_price=True,
        sources=sources, collected=collected,
    )
    caches = {store: economics.context(store, sources=sources) for store in stores}
    histories = {store: weekly_history(store, today, sources=sources) for store in stores}
    rows = []
    for row in report_rows:
        store, sku = row["store_slug"], row["article"]
        if article and sku != article:
            continue
        state = (
            economics.effective(store, sku, "FBY", state_cache=caches[store], estimate_tariff=True, scenario=scenario)
            if scenario
            else collected["states"][(store, sku)]
        )
        cabinet, saved = goals(store, sku, cached=caches[store]["settings"])
        weekly = product_drr(histories[store], sku, state["values"].get("buyout_percent"))
        rows.append(calculate_row(row, state, cabinet, saved, weekly, overrides=overrides))
    return {
        "ok": True,
        "marketplace": "YANDEX MARKET",
        "period_from": start.isoformat(),
        "period_to": end.isoformat(),
        "rows": rows,
    }
