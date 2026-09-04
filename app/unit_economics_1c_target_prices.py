"""Read-only target pricing using the WB calculator and seven completed Moscow days."""

import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from app import db
from app import unit_economics_1c as economics
from app.domain import MOSCOW_TIMEZONE
from app.dto.unit_economics_1c import UnitEconomics1CProductValues

_CURRENT_ROI_UNSET = object()


def number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def closed_week(today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    return today - timedelta(days=7), today - timedelta(days=1)


def weekly_metrics(
    days: list[str],
    orders: dict[str, dict],
    advertising: dict[str, dict],
    advertising_complete_days: set[str],
    buyout: dict,
    default_buyout_percent: float | None,
) -> dict:
    """Use matching observed days, never divide seven days of spend by three days of sales."""
    available = [
        day for day in days if day in orders and (day in advertising or day in advertising_complete_days)
    ]
    missing_orders = [day for day in days if day not in orders]
    missing_ads = [day for day in days if day not in advertising and day not in advertising_complete_days]
    spend = round(sum(number(advertising.get(day, {}).get("spend")) or 0 for day in available), 2)
    amount = round(sum(number(orders[day].get("orders_amount")) or 0 for day in available), 2)
    count = sum(int(number(orders[day].get("orders_count")) or 0) for day in available)
    effective = economics.resolve_buyout_percent(buyout.get("buyout_percent"), default_buyout_percent)
    used_default = not (number(buyout.get("buyout_percent")) or 0) and effective > 0
    warnings = []
    if len(available) != len(days):
        warnings.append(
            f"Данные за {len(available)} из {len(days)} дней. Суммируются только дни с данными и заказов, и рекламы."
        )
    if missing_orders:
        warnings.append("Нет данных заказов: " + ", ".join(missing_orders) + ".")
    if missing_ads:
        warnings.append("Нет данных рекламы: " + ", ".join(missing_ads) + ".")
    notes = list(warnings)
    if effective <= 0:
        warnings.append("Нет положительного процента выкупа WB и не задан выкуп по умолчанию.")
    elif used_default:
        warnings.append(f"Используется выкуп по умолчанию: {effective:g}%.")
        notes.append(warnings[-1])
    if amount <= 0 and not (available and spend == 0):
        warnings.append(
            "Нет оборота заказов: ДРР не определён."
            if spend <= 0
            else "Есть расходы на рекламу, но нет оборота заказов: ДРР не определён."
        )
    drr = 0.0 if available and spend == 0 and amount == 0 else None
    if amount > 0 and (effective > 0 or spend == 0):
        drr = economics.calculate_drr_percent(spend, amount, effective)
    advertising_per_unit = None
    if available and spend == 0:
        advertising_per_unit = 0.0
    elif available and count > 0 and effective > 0:
        advertising_per_unit = economics.calculate_advertising_per_unit(spend, count, effective)
    advertising_warnings = []
    if advertising_per_unit is None:
        if not available:
            advertising_warnings.append("Нет совместных данных заказов и рекламы для расчёта рекламы на штуку.")
        elif count <= 0:
            advertising_warnings.append("Есть расходы на рекламу, но нет заказов: рекламу на штуку рассчитать нельзя.")
        else:
            advertising_warnings.append("Нет положительного процента выкупа для расчёта рекламы на штуку.")
    return {
        "period_from": days[0],
        "period_to": days[-1],
        "days": len(available),
        "expected_days": len(days),
        "dates": available,
        "complete": len(available) == len(days),
        "missing_dates": [day for day in days if day not in available],
        "missing_orders_dates": missing_orders,
        "missing_advertising_dates": missing_ads,
        "spend": spend,
        "orders_amount": amount,
        "orders_count": count,
        "buyout_percent": effective,
        "buyout_default_applied": used_default,
        "drr": drr,
        "advertising_per_unit": advertising_per_unit,
        "advertising_warnings": advertising_warnings,
        "average_order_price": amount / count if count > 0 and amount > 0 else None,
        "notes": notes,
        "warnings": warnings,
    }


def load_weekly_metrics(store_slugs: tuple[str, ...], today: date | None = None) -> dict:
    start, end = closed_week(today)
    days = [(start + timedelta(days=offset)).isoformat() for offset in range(7)]
    orders = defaultdict(dict)
    advertising = defaultdict(dict)
    for row in db.get_unit_economics_1c_funnel_daily_order_rows(store_slugs, days[0], days[-1]):
        orders[(str(row["store_slug"]), str(row["article"]))][str(row["day"])] = row
    for row in db.get_unit_economics_1c_daily_advertising(store_slugs, days[0], days[-1]):
        advertising[(str(row["store_slug"]), str(row["nm_id"]))][str(row["day"])] = row
    complete = defaultdict(set)
    for state in db.list_unit_economics_1c_advertising_sync_states(store_slugs):
        if state.get("status") != "ok" or not state.get("last_success_at"):
            continue
        try:
            completed_before = (
                datetime.fromisoformat(state["last_success_at"]).astimezone(MOSCOW_TIMEZONE).date()
            )
        except (TypeError, ValueError):
            continue
        for day in days:
            if (
                str(state.get("period_from") or "") <= day <= str(state.get("period_to") or "")
                and day < completed_before.isoformat()
            ):
                complete[str(state["store_slug"])].add(day)
    buyouts = {
        (str(row["store_slug"]), str(row["article"])): row
        for row in db.get_unit_economics_1c_funnel_product_metrics(store_slugs)
    }
    cabinets = {item.store_slug: item for item in db.list_unit_economics_1c_cabinet_settings(store_slugs)}
    result = {}
    for slug in store_slugs:
        for product in db.get_stock_items(slug, "WB"):
            article = str(product.get("article") or "").partition(" / ")[0].strip()
            key = (slug, article)
            result[key] = weekly_metrics(
                days,
                orders[key],
                advertising[key],
                complete[slug],
                buyouts.get(key, {}),
                cabinets[slug].default_buyout_percent,
            )
    return result


def wallet_discount(client: float, wallet: float) -> float:
    for percent in range(100):
        if client - math.ceil(client * percent / 100) == math.floor(wallet + 0.5):
            return float(percent)
    return round((client - wallet) / client * 100, 2)


def calculate_target_advertising_rub(
    retail_price: float,
    target_drr_percent: float,
    buyout_percent: float,
) -> float:
    """Return the calculator advertising amount for the selected targets."""

    retail = max(float(retail_price or 0), 0.0)
    drr_ratio = min(max(float(target_drr_percent or 0), 0.0), 100.0) / 100
    buyout_ratio = min(max(float(buyout_percent or 0), 0.0), 100.0) / 100
    return round(retail * drr_ratio * buyout_ratio, 2)


def calculate_row(
    *,
    price: dict,
    reference: dict,
    product_settings,
    cabinet,
    weekly: dict,
    current_roi: float | None | object = _CURRENT_ROI_UNSET,
) -> dict:
    """Solve the calculator's price equation; never submit a marketplace price change."""
    retail = number(price.get("retail_price"))
    client = number(price.get("customer_price_with_spp"))
    wallet = number(price.get("customer_price_with_wallet"))
    purchase = number(reference.get("purchase_price"))
    commission = number(reference.get("subject_commission_percent"))
    product_target_drr = getattr(product_settings, "target_drr_percent", None)
    product_target_roi = getattr(product_settings, "target_roi_percent", None)
    target_drr = (
        product_target_drr if product_target_drr is not None else cabinet.target_drr_percent
    )
    target_roi = (
        product_target_roi if product_target_roi is not None else cabinet.target_roi_percent
    )
    warnings = []
    current_notes = list(weekly.get("notes", []))
    blockers = []
    if not retail or not client or client > retail:
        blockers.append("Нет корректной пары цен до СПП и с СПП: нельзя сохранить текущую скидку СПП.")
    if not purchase:
        blockers.append("Нет положительной текущей ЗЦ: ROI и целевая цена не рассчитываются.")
    if commission is None:
        blockers.append("Не загружена комиссия WB по предмету.")
    if product_settings is None:
        product_settings = UnitEconomics1CProductValues()
        note = "Логистика и хранение: используются значения по умолчанию (0 ₽), как в калькуляторе; настройки товара не сохранены."
        current_notes.append(note)
        warnings.append(note)
    if weekly["buyout_percent"] <= 0:
        blockers.append("Не задан процент выкупа для расчёта логистики с возвратами.")
    if weekly["buyout_default_applied"]:
        warnings.append(f"Выкуп по умолчанию: {weekly['buyout_percent']:g}%.")
    wallet_known = bool(client and wallet and wallet <= client)
    if not wallet_known:
        warnings.append("Нет корректной цены с кошельком: цену с СПП и кошельком рассчитать нельзя.")
    if price.get("day") and str(price["day"]) < weekly["period_to"]:
        warnings.append(f"Последняя сохранённая цена WB за {price['day']}.")
        current_notes.append(warnings[-1])
    fulfillment = number(reference.get("fulfillment_cost"))
    if fulfillment is None:
        fulfillment = 0.0
        warnings.append("Стоимость фулфилмента не загружена; используется 0, как в калькуляторе.")
        current_notes.append(warnings[-1])
    result = {
        "current_price": wallet if wallet_known else None,
        "current_drr": weekly["drr"],
        "current_drr_warnings": weekly["warnings"] if weekly["drr"] is None else [],
        "current_drr_notes": list(weekly.get("notes", [])),
        "current_roi": None if current_roi is _CURRENT_ROI_UNSET else current_roi,
        "target_price": None,
        "target_drr": target_drr,
        "target_roi": target_roi,
        "target_overridden": product_target_drr is not None or product_target_roi is not None,
        "cabinet_target_drr": cabinet.target_drr_percent,
        "cabinet_target_roi": cabinet.target_roi_percent,
        "target_retail_price": None,
        "target_spp_price": None,
        "target_actual_roi": None,
        "weekly": weekly,
        "price_date": price.get("day"),
        "current_warnings": list(dict.fromkeys(blockers + weekly.get("advertising_warnings", []))),
        "current_notes": list(dict.fromkeys(current_notes)),
        "target_warnings": warnings + blockers,
        "advertising_base": retail,
    }
    turnover_days = number(reference.get("turnover_days"))
    turnover_days = 21 if turnover_days is None else int(turnover_days)
    source_team = number(reference.get("team_commission_percent"))
    inputs = {
        "acquiring_percent": cabinet.acquiring_percent,
        "delivery_with_returns": economics.calculate_delivery_with_returns(
            product_settings.delivery_wb_rub,
            weekly["buyout_percent"],
            product_settings.return_cost_rub,
            economics.calculate_paid_acceptance_cost(
                product_settings.volume_l, cabinet.acceptance_coefficient
            ),
        ),
        "storage_wb_rub": product_settings.storage_wb_rub,
        "turnover_days": turnover_days,
        "wb_commission_percent": None if commission is None else commission + cabinet.wb_extra_tariff_percent,
        "purchase_price": purchase,
        "fulfillment_cost": fulfillment,
        "team_commission_percent": source_team
        if source_team is not None
        else cabinet.team_commission_percent,
        "vat_percent": cabinet.vat_percent,
        "usn_percent": cabinet.usn_percent,
        "osno_percent": cabinet.osno_percent,
        "tax_system": cabinet.tax_system if cabinet.store_slug == "gogol" else "usn",
    }
    result["calculator"] = {
        "retail": retail, "client": client, "wallet": wallet,
        "spp": None if not retail or client is None else round((retail - client) / retail * 100, 2),
        "wallet_percent": wallet_discount(client, wallet) if wallet_known else None,
        "drr": target_drr,
        "target_roi": target_roi,
        "target_overridden": product_target_drr is not None or product_target_roi is not None,
        "cabinet_target_drr": cabinet.target_drr_percent,
        "cabinet_target_roi": cabinet.target_roi_percent,
        "advertising_base": retail,
        "buyout_percent": weekly["buyout_percent"],
        "delivery_wb_rub": product_settings.delivery_wb_rub,
        "return_cost_rub": product_settings.return_cost_rub,
        "paid_acceptance_cost": economics.calculate_paid_acceptance_cost(
            product_settings.volume_l, cabinet.acceptance_coefficient
        ),
        "advertising_rub": None
        if retail is None
        else calculate_target_advertising_rub(
            retail,
            target_drr,
            weekly["buyout_percent"],
        ),
        **inputs,
    }
    if blockers:
        return result
    if current_roi is _CURRENT_ROI_UNSET and weekly["advertising_per_unit"] is not None:
        current = economics.calculate_unit_profit(
            retail_price=retail,
            customer_price=client,
            advertising_rub=weekly["advertising_per_unit"],
            **inputs,
        )
        result["current_roi"] = round(current["margin"] / purchase * 100, 2)
    spp_factor = client / retail
    tax_factor = economics.calculate_tax_components(
        spp_factor,
        inputs["vat_percent"],
        inputs["usn_percent"],
        inputs["osno_percent"],
        inputs["tax_system"],
    )["total"]
    revenue_factor_without_advertising = (
        1
        - (inputs["acquiring_percent"] + inputs["wb_commission_percent"] + inputs["team_commission_percent"])
        / 100
        - tax_factor
    )
    target_advertising_factor = target_drr / 100 * weekly["buyout_percent"] / 100
    revenue_factor = revenue_factor_without_advertising - target_advertising_factor
    if revenue_factor <= 0:
        result["target_warnings"].append(
            "Цель недостижима: комиссии, налоги и целевая реклама поглощают всю цену продажи."
        )
        return result
    target_margin = purchase * target_roi / 100
    required_retail = (
        purchase
        + target_margin
        + fulfillment
        + inputs["delivery_with_returns"]
        + inputs["storage_wb_rub"] * turnover_days
    ) / revenue_factor
    if not math.isfinite(required_retail) or required_retail > 1_000_000_000:
        result["target_warnings"].append("Расчётная цена превышает допустимый предел 1 млрд ₽.")
        return result
    center_cents = max(round(required_retail * 100), 1)
    candidates = []
    for retail_cents in range(max(center_cents - 150, 1), center_cents + 151):
        target_retail = retail_cents / 100
        target_client = max(math.floor(target_retail * spp_factor + 0.5), 1)
        advertising_rub = calculate_target_advertising_rub(
            target_retail,
            target_drr,
            weekly["buyout_percent"],
        )
        target_profit = economics.calculate_unit_profit(
            retail_price=target_retail,
            customer_price=target_client,
            advertising_rub=advertising_rub,
            **inputs,
        )
        if target_profit is None:
            continue
        actual_roi = round(target_profit["margin"] / purchase * 100, 2)
        candidates.append(
            (
                abs(actual_roi - target_roi),
                abs(retail_cents - required_retail * 100),
                target_client,
                target_retail,
                advertising_rub,
                actual_roi,
            )
        )
    if not candidates:
        result["target_warnings"].append("Не удалось подобрать цену по целевым ДРР и ROI.")
        return result
    _, _, target_client, target_retail, advertising_rub, target_actual_roi = min(candidates)
    discount = wallet_discount(client, wallet) if wallet_known else None
    target_wallet = (
        target_client - math.ceil(target_client * discount / 100) if discount is not None else None
    )
    result.update(
        {
            "target_price": target_wallet,
            "target_retail_price": target_retail,
            "target_spp_price": target_client,
            "target_actual_roi": target_actual_roi,
            "target_advertising_rub": advertising_rub,
        }
    )
    result["calculator"].update({
        "retail": target_retail,
        "client": target_client,
        "wallet": target_wallet,
        "spp": round((target_retail - target_client) / target_retail * 100, 2),
        "advertising_rub": advertising_rub,
    })
    return result
