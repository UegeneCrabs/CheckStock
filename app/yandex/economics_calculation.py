"""YM calculations, with explicit input provenance and no network/database side effects."""

from decimal import ROUND_HALF_UP, Decimal

VERSION = 14
DERIVED_FIELDS = ("volume_l", "return_middle_mile", "return_cost")
DELIVERY_FIELDS = ("delivery_customer", "middle_mile", "delivery_other")
REMOVED_FIELDS = {
    "payment_transfer_percent",
    "tariff_extra",
    "tax_base",
    "capital_percent",
    "turnover_days",
    "other_percent",
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
    "fulfillment_cost": "Затраты на ФФ",
    "commission_percent": "Комиссия YM, %",
    "payment_acceptance": "Приём платежа (Экваиринг 2)",
    "acquiring_percent": "Перевод платежа (Экваринг1)",
    "delivery_cost": "Доставка выкупленного товара",
    "return_cost": "Обратная доставка",
    "volume_l": "Объём товара (нужны положительные длина, ширина и высота упаковки)",
    "transit_cost": "Транзит",
    "company_commission_percent": "Комиссия компании, %",
    "vat_percent": "НДС, %",
    "usn_percent": "УСН, %",
    "loss_percent": "Потери от закупочной цены",
    "disposal_cost": "Утилизация",
    "buyout_percent": "Процент выкупа",
    "plan_drr": "ДРР с выкупом",
    "advertising_per_buyout": "Реклама на один выкуп",
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


def delivery_components(services):
    """Also extract the breakdown from quotes saved before version 13."""
    result = dict.fromkeys(DELIVERY_FIELDS, 0.0 if services else None)
    types = {
        "DELIVERY_TO_CUSTOMER": "delivery_customer",
        "MIDDLE_MILE": "middle_mile",
        "CROSSREGIONAL_DELIVERY": "delivery_other",
        "EXPRESS_DELIVERY": "delivery_other",
        "SORTING": "delivery_other",
    }
    for service in services:
        key = types.get(service.get("type"))
        if key:
            result[key] += float(service["amount"])
    return result


def sheet_logistics(values, *, scenario=None, version=VERSION):
    """Volume in litres; return delivery uses the quote's middle mile plus 15 RUB.

    The former volume-based return tariff is retained for historical versions.
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
    if version >= 13:
        middle_mile = values.get("middle_mile")
        middle_mile = Decimal(str(middle_mile)) if middle_mile is not None else None
    result.update(
        volume_l=float(volume) if volume is not None else None,
        return_middle_mile=float(middle_mile) if middle_mile is not None else None,
        return_cost=manual.get("return_cost", float(middle_mile + 15) if middle_mile is not None else None),
    )
    return result


def logistics_costs(values, *, version=VERSION):
    """The same expense lines used by profit and the logistics subtotal."""

    def amount(key):
        value = values.get(key)
        return Decimal(str(value)) if value is not None else None

    returns, buyout = amount("return_cost"), amount("buyout_percent")
    costs = {
        "delivery": amount("delivery_cost"),
        "returns": returns * (1 - buyout / 100) if returns is not None and buyout is not None else None,
        "transit": amount("transit_cost"),
    }
    if version >= 13:
        delivery = amount("delivery_cost")
        costs["repeat_delivery"] = (
            delivery * (1 - buyout / 100) if delivery is not None and buyout is not None else None
        )
        for field, cost in (("logistics_returns", "returns"), ("repeat_delivery", "repeat_delivery")):
            if values.get(field) is not None:
                costs[cost] = amount(field)
    return costs


def calculator_summary(values, *, version=VERSION):
    """Show known charges even when unrelated inputs prevent calculating profit."""
    logistics = logistics_costs(values, version=version)
    total = sum(logistics.values()) if all(value is not None for value in logistics.values()) else None
    if version >= 13 and values.get("logistics_total") is not None:
        total = Decimal(str(values["logistics_total"]))
    price, percent = values.get("seller_price"), values.get("commission_percent")
    commission = (
        Decimal(str(price)) * Decimal(str(percent)) / 100
        if price is not None and percent is not None
        else None
    )
    return {
        "commission_rub": money(commission) if commission is not None else None,
        "logistics": {
            **{key: money(value) if value is not None else None for key, value in logistics.items()},
            "total": money(total) if total is not None else None,
        },
    }


def calculate(
    values,
    *,
    advertising_spend=None,
    orders_count=None,
    without_advertising=False,
    scenario=None,
    precise=False,
    version=VERSION,
):
    """YM unit profit with WB-style VAT and USN, without the removed fixed costs.

    Returns and one repeat delivery multiply by the non-buyout fraction.
    Disposal multiplies by the loss fraction. Ads are allocated per expected
    bought unit, or entered as a per-unit expense / planned DRR in a scenario.
    Historical versions preserve their original formulas. With precise=True,
    margin stays a Decimal for finding a price without hiding fractional losses.
    """
    values = {**values, **sheet_logistics(values, scenario=scenario, version=version)}
    summary = calculator_summary(values, version=version)
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
    if version >= 14:
        required.append("fulfillment_cost")
    if version >= 13:
        if values.get("logistics_total") is not None:
            required = [
                key for key in required if key not in {"delivery_cost", "return_cost", "transit_cost"}
            ]
        elif values.get("logistics_returns") is not None:
            required.remove("return_cost")
    if not without_advertising and values.get("advertising_mode", "actual") in {"plan", "weekly"}:
        required.append("advertising_per_buyout" if version >= 13 else "plan_drr")
    missing = [key for key in required if values.get(key) is None]
    if missing:
        return {
            **summary,
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
            **summary,
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
        **(
            {"logistics": Decimal(str(values["logistics_total"]))}
            if version >= 13 and values.get("logistics_total") is not None
            else logistics_costs(values, version=version)
        ),
        "purchase": purchase,
        **({"fulfillment": d("fulfillment_cost")} if version >= 14 else {}),
        "company_commission": price * d("company_commission_percent") / 100,
        "vat": vat,
        "usn": usn,
        "loss": (price if version < 12 else purchase) * d("loss_percent") / 100,
        "disposal": d("disposal_cost") * (d("loss_percent") / 100 if version >= 13 else 1 - q),
    }
    if without_advertising:
        advertising = Decimal(0)
    elif values.get("advertising_mode", "actual") in {"plan", "weekly"}:
        advertising = (
            d("advertising_per_buyout")
            if version >= 13 and values.get("advertising_basis") != "drr"
            else price * d("plan_drr") / 100
        )
    elif advertising_spend is None or orders_count is None:
        return {
            **summary,
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
                **summary,
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
        **summary,
        "margin": margin if precise else money(margin),
        "roi": money(margin / purchase * 100) if purchase > 0 else None,
        "margin_percent": (
            money(margin / Decimal(str(buyer)) * 100) if buyer and buyer > 0 else 0.0 if buyer == 0 else None
        ),
        "costs": {key: money(value) for key, value in costs.items()},
        "total_cost": money(sum(costs.values())),
        "missing": [],
        "messages": [],
        "calculation_version": version,
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
        version=version,
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
