"""YM calculations, with explicit input provenance and no network/database side effects."""

from decimal import ROUND_HALF_UP, Decimal

VERSION = 11
DERIVED_FIELDS = ("volume_l", "return_middle_mile", "return_cost")
REMOVED_FIELDS = {
    "payment_transfer_percent",
    "tariff_extra",
    "tax_base",
    "capital_percent",
    "turnover_days",
    "other_percent",
    "fulfillment_cost",
    "storage_per_day",
    "storage_days",
    "other_cost",
    "tax_percent",
}
CABINET_DEFAULTS = {"acquiring_percent": 1.6}
OPTIONAL_DEFAULTS = {
    **CABINET_DEFAULTS,
    "advertising_mode": "actual",
    "frequency": "WEEKLY",
    "payment_delay_weeks": 0,
}
LABELS = {
    "seller_price": "Цена без СПП",
    "buyer_price": "Цена с СПП",
    "purchase_price": "Закупочная стоимость",
    "commission_percent": "Комиссия YM, %",
    "payment_acceptance": "Приём платежа (Экваиринг 2)",
    "acquiring_percent": "Перевод платежа (Экваринг1)",
    "delivery_cost": "Логистика, руб",
    "return_cost": "Обратная доставка",
    "volume_l": "Объём товара (нужны положительные длина, ширина и высота упаковки)",
    "transit_cost": "Транзит",
    "company_commission_percent": "Комиссия компании, %",
    "vat_percent": "НДС, %",
    "usn_percent": "УСН, %",
    "loss_percent": "Потери",
    "disposal_cost": "Утилизация",
    "buyout_percent": "Процент выкупа",
    "plan_drr": "ДРР с выкупом",
}


def money(value):
    # Spreadsheet/API floats can leave a binary tail just below half a kopeck.
    # Strip that tail for display only; profit still uses the unrounded costs.
    display_value = Decimal(format(Decimal(str(value)), ".15g"))
    return float(display_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def resolve(*layers):
    values, provenance = dict(OPTIONAL_DEFAULTS), {}
    for name, layer in layers:
        for key, value in layer.items():
            if key in REMOVED_FIELDS:
                continue
            if value is not None:
                values[key], provenance[key] = value, name
    return values, provenance


def sheet_logistics(values, *, scenario=None):
    """AO and AP + 15 from the sheet, independent of the successful-delivery API quote.

    Dimensions are in cm; volume is in litres, without rounding up. Legacy
    return settings are ignored.
    """
    result = dict.fromkeys(DERIVED_FIELDS)
    manual = {key: value for key, value in (scenario or {}).items() if value is not None}
    dimensions = [values.get(key) for key in ("length", "width", "height")]
    volume = None
    if "volume_l" in manual:
        volume = Decimal(str(manual["volume_l"]))
    elif all(value is not None and value > 0 for value in dimensions):
        length, width, height = (Decimal(str(value)) for value in dimensions)
        volume = length * width * height / 1000
    if volume is None:
        middle_mile = None
    elif volume <= 1:
        middle_mile = Decimal(80)
    elif volume <= 30:
        middle_mile = 80 + (volume - 1) * 9
    elif volume <= 200:
        middle_mile = 80 + 29 * 9 + (volume - 30) * 7
    else:
        middle_mile = 80 + 29 * 9 + 170 * 7 + (volume - 200) * 5
    if middle_mile is not None:
        middle_mile = min(middle_mile, Decimal(5500))
    if "return_middle_mile" in manual:
        middle_mile = Decimal(str(manual["return_middle_mile"]))
    result.update(
        volume_l=float(volume) if volume is not None else None,
        return_middle_mile=float(middle_mile) if middle_mile is not None else None,
        return_cost=manual.get("return_cost", float(middle_mile + 15) if middle_mile is not None else None),
    )
    return result


def calculate(
    values,
    *,
    advertising_spend=None,
    orders_count=None,
    without_advertising=False,
    scenario=None,
    precise=False,
):
    """YM unit profit with WB-style VAT and USN, without the removed fixed costs.

    AS returns and AX disposal multiply by the non-buyout fraction, without
    dividing by buyout. Planned advertising is seller price times DRR; only
    actual advertising is allocated over expected bought units. With precise=True,
    margin stays a Decimal for finding a price without hiding fractional losses.
    """
    values = {**values, **sheet_logistics(values, scenario=scenario)}
    required = [
        "seller_price",
        "buyer_price",
        "purchase_price",
        "commission_percent",
        "payment_acceptance",
        "acquiring_percent",
        "delivery_cost",
        "return_cost",
        "transit_cost",
        "company_commission_percent",
        "vat_percent",
        "usn_percent",
        "loss_percent",
        "disposal_cost",
        "buyout_percent",
    ]
    if not without_advertising and values.get("advertising_mode", "actual") in {"plan", "weekly"}:
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
    if q <= 0 and values.get("advertising_mode", "actual") == "actual":
        return {
            "margin": None,
            "roi": None,
            "missing": ["buyout_percent"],
            "messages": ["При нулевом выкупе прибыль на выкупленную единицу не определена."],
            "costs": {},
        }

    buyer = d("buyer_price")
    vat = buyer * d("vat_percent") / (100 + d("vat_percent"))
    usn = (buyer - vat) * d("usn_percent") / 100
    costs = {
        "commission": price * d("commission_percent") / 100,
        "payment_acceptance": d("payment_acceptance"),
        "acquiring": price * d("acquiring_percent") / 100,
        "delivery": d("delivery_cost"),
        "returns": d("return_cost") * (1 - q),
        "transit": d("transit_cost"),
        "purchase": purchase,
        "company_commission": price * d("company_commission_percent") / 100,
        "vat": vat,
        "usn": usn,
        "loss": price * d("loss_percent") / 100,
        "disposal": d("disposal_cost") * (1 - q),
    }
    if without_advertising:
        advertising = Decimal(0)
    elif values.get("advertising_mode", "actual") in {"plan", "weekly"}:
        advertising = price * d("plan_drr") / 100
    elif advertising_spend is None or orders_count is None:
        return {
            "margin": None,
            "roi": None,
            "missing": ["advertising"],
            "messages": [
                "Нет полной рекламы и заказов за сегодня. Для планового расчёта задайте «ДРР с выкупом» в калькуляторе."
            ],
            "costs": {key: money(value) for key, value in costs.items()},
        }
    elif orders_count <= 0:
        if advertising_spend > 0:
            return {
                "margin": None,
                "roi": None,
                "missing": ["orders_count"],
                "messages": [
                    "Есть рекламные расходы, но нет заказов для распределения. Для планового расчёта задайте «ДРР с выкупом» в калькуляторе."
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
        "margin": margin if precise else money(margin),
        "roi": money(margin / purchase * 100) if purchase > 0 else None,
        "margin_percent": (
            money(margin / Decimal(str(buyer)) * 100) if buyer and buyer > 0 else 0.0 if buyer == 0 else None
        ),
        "costs": {key: money(value) for key, value in costs.items()},
        "total_cost": money(sum(costs.values())),
        "missing": [],
        "messages": [],
        "calculation_version": VERSION,
        "basis": "ym_sheet_unit",
    }


def daily_profit(values, orders_count, advertising_spend, baseline, version):
    """Calculate daily profit without the removed withdrawal fee."""
    if baseline.get("margin") is None:
        return None
    bought = Decimal(str(orders_count)) * Decimal(str(values["buyout_percent"])) / 100
    if version < 10:
        return round(baseline["margin"] * float(bought) - advertising_spend, 2)
    if not bought:
        return money(-Decimal(str(advertising_spend)))
    result = calculate(
        {**values, "advertising_mode": "actual"},
        advertising_spend=advertising_spend,
        orders_count=orders_count,
        precise=True,
    )
    return money(result["margin"] * bought) if result["margin"] is not None else None


def break_even_prices(values, *, scenario=None):
    """Keep current costs and price ratios; find a non-loss price in steps of 10 RUB."""
    initial = calculate(values, scenario=scenario, precise=True)
    if initial["margin"] is None:
        raise ValueError("Для цены без убытка не хватает данных. " + " ".join(initial["messages"]))
    seller = Decimal(str(values["seller_price"]))
    if seller <= 0:
        raise ValueError("Для цены без убытка задайте положительную цену продавца.")
    buyer_factor = Decimal(str(values["buyer_price"])) / seller
    pay_factor = Decimal(str(values["pay_price"])) / seller if values.get("pay_price") is not None else None

    def margin(price, buyer):
        return calculate(
            {**values, "seller_price": price, "buyer_price": buyer}, scenario=scenario, precise=True
        )["margin"]

    # Search the actual 10-ruble price grid, including rounded buyer-side taxes.
    lower = 1
    upper = int(Decimal(1_000_000_000) / max(Decimal(1), buyer_factor, pay_factor or 0) / 10)

    def profit_at(step):
        price = Decimal(step * 10)
        return margin(price, Decimal(str(money(price * buyer_factor))))

    if upper < lower or profit_at(upper) < 0:
        raise ValueError("Цена без убытка недостижима в допустимом диапазоне. Уменьшите расходы или ДРР.")
    while lower < upper:
        middle = (lower + upper) // 2
        if profit_at(middle) >= 0:
            upper = middle
        else:
            lower = middle + 1
    price = Decimal(lower * 10)
    return {
        "seller_price": float(price),
        "buyer_price": money(price * buyer_factor),
        "pay_price": money(price * pay_factor) if pay_factor is not None else None,
    }


def aggregate(days, expected_dates):
    """Use the same saved days for profit and invested purchase cost, including partial periods."""
    expected_dates = sorted(set(expected_dates))
    present = {row["day"]: row for row in days}
    known = [present[day] for day in expected_dates if day in present]
    covered = [
        row for row in known if row.get("profit") is not None and row.get("purchase_value") is not None
    ]
    dates = [row["day"] for row in covered]
    missing = sorted(set(expected_dates).difference(dates))
    profit = sum(Decimal(str(row["profit"])) for row in covered)
    basis = sum(Decimal(str(row["purchase_value"])) for row in covered)
    unallocated_ads = sum(float(row.get("advertising_spend") or 0) for row in known if row["day"] in missing)
    return {
        "margin": money(profit) if covered else None,
        "roi": money(profit / basis * 100) if covered and basis > 0 else None,
        "purchase_value": money(basis) if covered else None,
        "coverage": {
            "dates": dates,
            "days": len(covered),
            "expected_days": len(expected_dates),
            "complete": not missing,
            "period_from": dates[0] if dates else None,
            "period_to": dates[-1] if dates else None,
            "missing_dates": missing,
        },
        "unallocated_advertising": money(unallocated_ads),
        "complete": not missing,
    }
