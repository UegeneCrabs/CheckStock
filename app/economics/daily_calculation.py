"""Daily snapshot formulas. Never resolve historical inputs from current settings."""

import math

from app.economics.completeness import annotate, describe, status
from app.economics.wb import calculations as wb
from app.yandex import economics_calculation as ym

# key, caption, unit. Only actual inputs are editable, never derived metrics.
COMMON_FIELDS = [
    ("purchase_price", "Закупка", "₽"), ("fulfillment_cost", "ФФ", "₽"),
    ("buyout_percent", "Выкуп", "%"), ("vat_percent", "НДС", "%"),
    ("usn_percent", "УСН", "%"), ("orders_count", "Заказы за день", "шт."),
    ("advertising_spend", "Реклама за день", "₽"),
]
WB_FIELDS = [
    ("retail_price", "Цена продавца", "₽"), ("customer_price", "Цена покупателя", "₽"),
    *COMMON_FIELDS[:2],
    ("subject_commission_percent", "Комиссия предмета", "%"),
    ("wb_extra_tariff_percent", "Доптариф WB", "%"),
    ("acquiring_percent", "Эквайринг", "%"),
    ("delivery_wb_rub", "Доставка WB", "₽"), ("return_cost_rub", "Возврат", "₽"),
    ("volume_l", "Объём", "л"), ("acceptance_coefficient", "Коэффициент приёмки", "×"),
    ("storage_wb_rub", "Хранение", "₽/день"), ("turnover_days", "Оборачиваемость", "дн."),
    ("team_commission_percent", "Комиссия компании", "%"),
    ("tax_system", "Налоговая система", ""), ("osno_percent", "ОСНО", "%"),
    *COMMON_FIELDS[2:],
]
YM_FIELDS = [
    ("seller_price", "Цена продавца", "₽"), ("buyer_price", "Цена покупателя", "₽"),
    *COMMON_FIELDS[:2],
    ("commission_percent", "Комиссия ЯМ", "%"), ("payment_acceptance", "Приём платежа", "₽"),
    ("acquiring_percent", "Перевод платежа", "%"),
    ("delivery_cost", "Доставка", "₽"), ("middle_mile", "Средняя миля (для возврата)", "₽"),
    ("transit_cost", "Транзит", "₽"),
    ("length", "Длина", "см"), ("width", "Ширина", "см"), ("height", "Высота", "см"),
    ("company_commission_percent", "Комиссия компании", "%"),
    ("loss_percent", "Потери", "%"), ("disposal_cost", "Утилизация", "₽"),
    *COMMON_FIELDS[2:],
]


def fields(marketplace):
    return WB_FIELDS if marketplace == "WB" else YM_FIELDS


def validate_change(marketplace, field, value):
    allowed = {key: unit for key, _, unit in fields(marketplace)}
    if field not in allowed:
        raise ValueError("Можно изменять только исходные параметры, не расчётные поля.")
    if field == "tax_system":
        if value not in {"usn", "osno"}:
            raise ValueError("Налоговая система: usn или osno.")
        return value
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Укажите число. Отсутствие данных не равно нулю.")
    if not math.isfinite(value) or value < 0 or value > 1_000_000_000:
        raise ValueError("Значение должно быть конечным числом от 0 до 1 000 000 000.")
    if allowed[field] == "%" and value > 100:
        raise ValueError("Процент должен быть от 0 до 100.")
    if field in {"orders_count", "turnover_days"} and int(value) != value:
        raise ValueError("Требуется целое число.")
    return value


def calculate(marketplace, values, version=None):
    """Keep unit margin without advertising; daily profit deducts daily advertising."""
    v = dict(values)
    if marketplace == "WB":
        required = [
            "retail_price", "purchase_price", "fulfillment_cost", "subject_commission_percent",
            "wb_extra_tariff_percent", "acquiring_percent", "delivery_wb_rub", "buyout_percent",
            "acceptance_coefficient", "storage_wb_rub", "team_commission_percent", "vat_percent", "tax_system",
        ]
        required.append("osno_percent" if v.get("tax_system") == "osno" else "usn_percent")
        if v.get("buyout_percent") != 100:
            required.append("return_cost_rub")
        if v.get("acceptance_coefficient") != 0:
            required.append("volume_l")
        if v.get("storage_wb_rub") != 0:
            required.append("turnover_days")
        missing = [key for key in required if v.get(key) is None]
        acceptance = (
            wb.calculate_paid_acceptance_cost(v.get("volume_l"), v.get("acceptance_coefficient"))
            if v.get("acceptance_coefficient") == 0 or all(v.get(k) is not None for k in ("volume_l", "acceptance_coefficient"))
            else None
        )
        q = v.get("buyout_percent")
        q = q / 100 if q is not None else None
        logistics_parts = [acceptance]
        logistics_parts.append(v["delivery_wb_rub"] * (2 - q) if v.get("delivery_wb_rub") is not None and q is not None else None)
        logistics_parts.append(0.0 if q == 1 else v["return_cost_rub"] * (1 - q) if v.get("return_cost_rub") is not None and q is not None else None)
        known_logistics = [value for value in logistics_parts if value is not None]
        delivery = wb.money(sum(known_logistics)) if known_logistics else None
        commission_parts = [v.get(k) for k in ("subject_commission_percent", "wb_extra_tariff_percent") if v.get(k) is not None]
        commission = sum(commission_parts) if commission_parts else None
        v.update(paid_acceptance_cost=acceptance,
                 delivery_with_returns=delivery if all(x is not None for x in logistics_parts) else None,
                 commission_percent=commission if len(commission_parts) == 2 else None)
        result = wb.calculate_unit_profit(
            retail_price=v.get("retail_price"), customer_price=v.get("customer_price"),
            acquiring_percent=v.get("acquiring_percent"), delivery_with_returns=delivery,
            storage_wb_rub=v.get("storage_wb_rub"), turnover_days=v.get("turnover_days"),
            wb_commission_percent=commission, advertising_rub=0, purchase_price=v.get("purchase_price"),
            fulfillment_cost=v.get("fulfillment_cost"), team_commission_percent=v.get("team_commission_percent"),
            vat_percent=v.get("vat_percent"), usn_percent=v.get("usn_percent"), osno_percent=v.get("osno_percent"),
            tax_system=v.get("tax_system"),
        )
        if not v.get("retail_price"):
            missing.append("retail_price")
        result = annotate(result or {"margin": None}, missing + [key for key in (result or {}).get("missing", []) if key not in {"delivery_with_returns", "commission_percent"}])
        result["roi"] = wb.money(result["margin"] / v["purchase_price"] * 100) if result["margin"] is not None and v.get("purchase_price", 0) else None
    else:
        version = version or ym.VERSION
        v.update(ym.sheet_logistics(v, version=version))
        result = ym.calculate(v, without_advertising=True, version=version)
    count, spend, buyout = (v.get(k) for k in ("orders_count", "advertising_spend", "buyout_percent"))
    bought = 0.0 if count == 0 else count * buyout / 100 if count is not None and buyout is not None else None
    daily_missing = [k for k in ("orders_count", "advertising_spend") if v.get(k) is None]
    if count != 0 and buyout is None:
        daily_missing.append("buyout_percent")
    daily_missing = list(dict.fromkeys((result.get("missing", []) if bought != 0 else []) + daily_missing))
    profit = None
    if bought == 0:
        # Zero orders are known; missing prices do not erase real daily spend.
        profit = -spend if spend is not None else None
    elif bought is not None and result.get("margin") is not None:
        profit = wb.money(result["margin"] * bought - (spend or 0)) if marketplace == "WB" else ym.daily_profit(v, count, spend, result, version)
    result.update(
        day_profit=profit, expected_buyouts=bought,
        purchase_value=0.0 if bought == 0 else wb.money(v["purchase_price"] * bought) if v.get("purchase_price") is not None and bought is not None else None,
        daily_missing=daily_missing, daily_complete=not daily_missing,
        daily_status=status(profit, daily_missing),
        daily_messages=["Не учтены / неизвестны: " + ", ".join(describe(daily_missing))] if daily_missing else [],
    )
    return v, result
