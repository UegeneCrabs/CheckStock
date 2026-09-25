"""YM calculations, with explicit input provenance and no network/database side effects."""

from decimal import ROUND_HALF_UP, Decimal

VERSION = 15
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
        "returns": Decimal(0) if buyout == 100 or returns == 0 else returns * (1 - buyout / 100) if returns is not None and buyout is not None else None,
        "transit": amount("transit_cost"),
    }
    if version >= 13:
        delivery = amount("delivery_cost")
        costs["repeat_delivery"] = (
            Decimal(0) if buyout == 100 or delivery == 0 else delivery * (1 - buyout / 100) if delivery is not None and buyout is not None else None
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
    from app.economics.completeness import annotate

    values = {**values, **sheet_logistics(values, scenario=scenario, version=version)}
    summary = calculator_summary(values, version=version)
    missing = []

    def d(key):
        value = values.get(key)
        if value is None:
            missing.append(key)
        return Decimal(str(value)) if value is not None else None

    def mul(left, right):
        # A confirmed zero coefficient does not require an unrelated base.
        if left == 0 or right == 0:
            return Decimal(0)
        return left * right if left is not None and right is not None else None

    def rate(key):
        value = d(key)
        return value / 100 if value is not None else None

    price, purchase = d("seller_price"), d("purchase_price")
    q = rate("buyout_percent")
    vat_rate = d("vat_percent")
    usn_rate = rate("usn_percent")
    buyer = d("buyer_price") if vat_rate != 0 or usn_rate != 0 else values.get("buyer_price")
    buyer = Decimal(str(buyer)) if buyer is not None else None
    vat = Decimal(0) if vat_rate == 0 else buyer * vat_rate / (100 + vat_rate) if buyer is not None and vat_rate is not None else None
    usn = mul(buyer - vat if buyer is not None and vat is not None else None, usn_rate)
    loss_rate = rate("loss_percent")
    costs = {
        "commission": mul(price, rate("commission_percent")),
        "payment_acceptance": d("payment_acceptance"),
        "acquiring": mul(price, rate("acquiring_percent")),
        "purchase": purchase,
        **({"fulfillment": d("fulfillment_cost")} if version >= 14 else {}),
        "company_commission": mul(price, rate("company_commission_percent")),
        "vat": vat, "usn": usn,
        "loss": mul(price if version < 12 else purchase, loss_rate),
    }
    disposal_factor = loss_rate if version >= 13 else (1 - q if q is not None else None)
    costs["disposal"] = Decimal(0) if disposal_factor == 0 else mul(d("disposal_cost"), disposal_factor)
    if version >= 13 and values.get("logistics_total") is not None:
        costs["logistics"] = d("logistics_total")
    else:
        delivery = d("delivery_cost")
        costs.update(delivery=delivery, transit=d("transit_cost"))
        non_buyout = 1 - q if q is not None else None
        costs["returns"] = d("logistics_returns") if version >= 13 and values.get("logistics_returns") is not None else Decimal(0) if non_buyout == 0 else mul(d("return_cost"), non_buyout)
        if version >= 13:
            costs["repeat_delivery"] = d("repeat_delivery") if values.get("repeat_delivery") is not None else mul(delivery, non_buyout)
    if without_advertising:
        advertising = Decimal(0)
    elif values.get("advertising_mode", "actual") in {"plan", "weekly"}:
        if version >= 13 and values.get("advertising_basis") != "drr":
            advertising = d("advertising_per_buyout")
        else:
            advertising = mul(mul(price, rate("plan_drr")), q if version >= 15 else Decimal(1))
    elif advertising_spend == 0:
        advertising = Decimal(0)
    else:
        if advertising_spend is None:
            missing.append("advertising_spend")
        if orders_count is None:
            missing.append("orders_count")
        advertising = Decimal(str(advertising_spend)) / Decimal(str(orders_count)) / q if advertising_spend is not None and orders_count and q else None
    costs["advertising"] = advertising
    total = sum((value for value in costs.values() if value is not None), Decimal(0))
    margin = price - total if price is not None and price > 0 else None
    if margin is None and "seller_price" not in missing:
        missing.append("seller_price")
    # Retain the established current-day display for actual spend with no orders.
    basis = "ym_sheet_unit"
    if not without_advertising and values.get("advertising_mode", "actual") == "actual" and orders_count == 0 and advertising_spend is not None and advertising_spend > 0:
        margin = -Decimal(str(advertising_spend))
        missing = []
        basis = "ym_daily_without_orders"
    return annotate({
        **summary, "margin": margin if precise else money(margin) if margin is not None else None,
        "roi": money(margin / purchase * 100) if margin is not None and purchase and purchase > 0 and basis == "ym_sheet_unit" else None,
        "margin_percent": money(margin / buyer * 100) if margin is not None and buyer and buyer > 0 and basis == "ym_sheet_unit" else None,
        "costs": {key: money(value) if value is not None else None for key, value in costs.items()},
        "total_cost": money(total), "calculation_version": version, "basis": basis,
    }, missing)


def daily_profit(values, orders_count, advertising_spend, baseline, version):
    """Calculate daily profit without the removed withdrawal fee."""
    if orders_count is None:
        return None
    if orders_count == 0 or values.get("buyout_percent") == 0:
        return money(-Decimal(str(advertising_spend))) if advertising_spend is not None else None
    if baseline.get("margin") is None or values.get("buyout_percent") is None:
        return None
    bought = Decimal(str(orders_count)) * Decimal(str(values["buyout_percent"])) / 100
    if version < 10:
        return round(baseline["margin"] * float(bought) - (advertising_spend or 0), 2)
    result = calculate(values, without_advertising=True, precise=True, version=version)
    return money(result["margin"] * bought - Decimal(str(advertising_spend or 0)))


def break_even_prices(values, *, scenario=None):
    """Keep current costs and price ratios; find a non-loss price in steps of 10 RUB."""
    initial = calculate(values, scenario=scenario, precise=True)
    if initial["margin"] is None or initial.get("missing"):
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
    from app.economics.completeness import aggregate_days

    return aggregate_days(days, expected_dates, money)
