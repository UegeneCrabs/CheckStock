"""YM calculations, with explicit input provenance and no network/database side effects."""

from decimal import ROUND_HALF_UP, Decimal

VERSION = 2
OPTIONAL_DEFAULTS = {
    "advertising_mode": "actual",
    "tax_base": "buyer",
    "frequency": "WEEKLY",
    "payment_delay_weeks": 0,
}
LABELS = {
    "seller_price": "Цена продавца",
    "buyer_price": "Цена покупателя",
    "purchase_price": "Закупочная цена",
    "fulfillment_cost": "Фулфилмент",
    "commission_percent": "Комиссия Маркета",
    "payment_acceptance": "Приём платежа",
    "payment_transfer_percent": "Перевод платежа",
    "delivery_cost": "Доставка",
    "return_cost": "Обратная доставка",
    "storage_per_day": "Хранение за день",
    "storage_days": "Дни хранения",
    "transit_cost": "Транзит",
    "other_percent": "Прочие расходы, %",
    "other_cost": "Прочие расходы, ₽",
    "tax_percent": "Налог",
    "capital_percent": "Стоимость капитала",
    "turnover_days": "Оборачиваемость",
    "loss_percent": "Потери",
    "disposal_cost": "Утилизация",
    "buyout_percent": "Выкуп",
    "plan_drr": "Плановый ДРР",
}


def money(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def resolve(*layers):
    values, provenance = dict(OPTIONAL_DEFAULTS), {}
    for name, layer in layers:
        for key, value in layer.items():
            if value is not None:
                values[key], provenance[key] = value, name
    return values, provenance


def calculate(values, *, advertising_spend=None, orders_count=None, without_advertising=False):
    """Preserve the YM sheet's AT and AY formulas; do not normalize these by q. DRR is defined against expected bought seller turnover."""
    required = [
        "seller_price",
        "purchase_price",
        "fulfillment_cost",
        "commission_percent",
        "payment_acceptance",
        "payment_transfer_percent",
        "delivery_cost",
        "return_cost",
        "storage_per_day",
        "storage_days",
        "transit_cost",
        "other_percent",
        "other_cost",
        "tax_percent",
        "capital_percent",
        "turnover_days",
        "loss_percent",
        "disposal_cost",
        "buyout_percent",
    ]
    if values.get("tax_base", "buyer") == "buyer":
        required.append("buyer_price")
    if not without_advertising and values.get("advertising_mode", "actual") == "plan":
        required.append("plan_drr")
    missing = [key for key in required if values.get(key) is None]
    if missing:
        return {
            "margin": None,
            "roi": None,
            "missing": missing,
            "messages": ["Не задано: " + LABELS.get(key, key) for key in missing],
            "costs": {},
        }

    def d(key):
        return Decimal(str(values.get(key, 0)))

    price, purchase, q = d("seller_price"), d("purchase_price"), d("buyout_percent") / 100
    if q <= 0:
        return {
            "margin": None,
            "roi": None,
            "missing": ["buyout_percent"],
            "messages": ["При нулевом выкупе прибыль на выкупленную единицу не определена."],
            "costs": {},
        }

    costs = {
        "commission": price * d("commission_percent") / 100,
        "payment_acceptance": d("payment_acceptance"),
        "payment_transfer": price * d("payment_transfer_percent") / 100,
        "delivery": d("delivery_cost"),
        "returns": d("return_cost") * (1 - q),
        "storage": d("storage_per_day") * d("storage_days"),
        "transit": d("transit_cost"),
        "purchase": purchase,
        "fulfillment": d("fulfillment_cost"),
        "other": price * d("other_percent") / 100 + d("other_cost"),
        "tax": (d("buyer_price") if values.get("tax_base", "buyer") == "buyer" else price)
        * d("tax_percent")
        / 100,
        "capital": purchase * d("capital_percent") / 100 * d("turnover_days") / 365,
        "loss": price * d("loss_percent") / 100,
        "disposal": d("disposal_cost") * (1 - q),
        "tariff_extra": d("tariff_extra"),
    }
    if without_advertising:
        advertising = Decimal(0)
    elif values.get("advertising_mode", "actual") == "plan":
        advertising = price * d("plan_drr") / 100
    elif advertising_spend is None or orders_count is None:
        return {
            "margin": None,
            "roi": None,
            "missing": ["advertising"],
            "messages": ["Нет полной рекламы и заказов за сегодня. Плановый расчёт доступен в калькуляторе."],
            "costs": {key: money(value) for key, value in costs.items()},
        }
    elif orders_count <= 0:
        if advertising_spend > 0:
            return {
                "margin": None,
                "roi": None,
                "missing": ["orders_count"],
                "messages": [
                    "Есть рекламные расходы, но нет заказов для распределения. Плановый расчёт доступен в калькуляторе."
                ],
                "costs": {key: money(value) for key, value in costs.items()},
            }
        advertising = Decimal(0)
    else:
        advertising = Decimal(str(advertising_spend)) / Decimal(str(orders_count)) / q
    costs["advertising"] = advertising
    margin = price - sum(costs.values())
    buyer = values.get("buyer_price")
    return {
        "margin": money(margin),
        "roi": money(margin / purchase * 100) if purchase > 0 else None,
        "margin_percent": money(margin / Decimal(str(buyer)) * 100) if buyer and buyer > 0 else None,
        "costs": {key: money(value) for key, value in costs.items()},
        "total_cost": money(sum(costs.values())),
        "missing": [],
        "messages": [],
        "calculation_version": VERSION,
        "basis": "ym_sheet_unit",
    }


def aggregate(days, expected_dates):
    present = {row["day"]: row for row in days}
    known = [present[day] for day in expected_dates if day in present]
    covered = [row for row in known if row.get("profit") is not None]
    missing = [day for day in expected_dates if day not in present or present[day].get("profit") is None]
    profit = sum(Decimal(str(row["profit"])) for row in covered)
    basis = sum(Decimal(str(row["purchase_value"])) for row in covered)
    unallocated_ads = sum(
        float(row.get("advertising_spend") or 0) for row in known if row.get("profit") is None
    )
    return {
        "margin": money(profit) if covered else None,
        "roi": money(profit / basis * 100) if covered and basis > 0 else None,
        "purchase_value": money(basis) if covered else None,
        "coverage": {
            "days": len(covered),
            "expected_days": len(expected_dates),
            "complete": not missing,
            "missing_dates": missing,
        },
        "unallocated_advertising": money(unallocated_ads),
        "complete": not missing,
    }
