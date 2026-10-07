"""Additive accrual accounting. Unknown values never become confirmed zero."""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.dto.finance import FinanceEvent

VERSION = "yandex-finance-1"
SOURCES = ("realization", "services", "orders", "payments")
CATEGORIES = ("commission", "acquiring", "logistics", "storage", "advertising", "other", "unclassified")
LABELS = {
    "orders_turnover": "Оборот созданных заказов",
    "sold_count": "Реализовано, шт.",
    "returned_count": "Возвращено, шт.",
    "buyout_count": "Выкупы за вычетом возвратов, шт.",
    "seller_turnover": "Оборот в цене селлера за вычетом возвратов",
    "buyer_turnover": "Оборот в цене покупателя за вычетом возвратов",
    "commission": "Комиссия",
    "acquiring": "Эквайринг и перевод платежей",
    "logistics": "Логистика",
    "storage": "Хранение",
    "advertising": "Реклама",
    "other": "Прочие расходы",
    "unclassified": "Не классифицировано",
    "income": "Прочие начисления",
    "expenses": "Всего расходов Маркета",
    "accrual_result": "Результат расчётов с Маркетом по начислениям",
    "sold_cost": "Закупочная стоимость реализованных товаров",
    "returned_cost": "Восстановленная закупочная стоимость возвратов",
    "net_cost": "Закупочная стоимость за вычетом возвратов",
    "profit": "Маржинальная прибыль до налогов и внешних расходов",
    "margin_percent": "Маржинальность от оборота в цене покупателя, %",
    "payments": "Выплаты по данным Маркета",
}


def days(start: date, end: date):
    while start <= end:
        yield start
        start += timedelta(days=1)


def money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def contributions(event: FinanceEvent) -> dict[str, Decimal | None]:
    """A single operation's contribution, also used for drill-down selection."""
    # Round individual monetary contributions, so displayed days and details add up exactly.
    event = event.model_copy(
        update={
            key: Decimal(money(getattr(event, key))) if getattr(event, key) is not None else None
            for key in ("seller", "buyer", "amount", "cost")
        }
    )
    q = Decimal(event.quantity)
    if event.kind == "order":
        return {"orders_turnover": event.amount}
    if event.kind == "payment":
        return {"payments": event.amount}
    if event.kind in {"sale", "return"}:
        sign = Decimal(1 if event.kind == "sale" else -1)

        def negate(value):
            return sign * value if value is not None else None

        result = {
            "sold_count" if sign > 0 else "returned_count": q,
            "buyout_count": sign * q,
            "seller_turnover": negate(event.seller),
            "buyer_turnover": negate(event.buyer),
            "sold_cost" if sign > 0 else "returned_cost": event.cost,
            "net_cost": negate(event.cost),
            "accrual_result": negate(event.seller),
            "profit": negate(event.seller - event.cost)
            if event.seller is not None and event.cost is not None
            else None,
        }
        return result
    if event.kind == "income":
        return {"income": event.amount, "accrual_result": event.amount, "profit": event.amount}
    if event.kind in {"expense", "unknown"}:
        category = event.category if event.category in CATEGORIES else "unclassified"
        amount = event.amount if event.kind == "expense" else None
        return {
            category: amount,
            "expenses": amount,
            "accrual_result": -amount if amount is not None else None,
            "profit": -amount if amount is not None else None,
        }
    return {}


def calculate(events: list[FinanceEvent], coverage: dict[str, list[str]], *, preliminary=False) -> dict:
    totals = {key: Decimal(0) for key in LABELS if key != "margin_percent"}
    reasons = {key: [] for key in totals}
    deps = {key: {"services"} for key in totals}
    for key in (
        "sold_count",
        "returned_count",
        "buyout_count",
        "seller_turnover",
        "buyer_turnover",
        "sold_cost",
        "returned_cost",
        "net_cost",
    ):
        deps[key] = {"realization"}
    deps["orders_turnover"] = {"orders"}
    deps["payments"] = {"payments"}
    deps["income"] = {"income"}
    deps["accrual_result"] = {"realization", "services", "income"}
    deps["profit"] = {"realization", "services", "income"}
    for key, sources in deps.items():
        for source in sorted(sources):
            reasons[key].extend(coverage.get(source, []))
    for event in events:
        for key, value in contributions(event).items():
            if value is None:
                reasons[key].append(
                    f"Неизвестно значение: {event.article or event.category or event.kind}, {event.day}"
                )
            else:
                totals[key] += value
    result = {}
    for key, total in totals.items():
        missing = list(dict.fromkeys(reasons[key]))
        result[key] = {
            "label": LABELS[key],
            "value": None if missing else money(total),
            "known_value": money(total),
            "complete": not missing,
            "status": "partial" if missing else "preliminary" if preliminary else "complete",
            "reasons": missing,
        }
    percent_reasons = list(dict.fromkeys(reasons["profit"] + reasons["buyer_turnover"]))
    if totals["buyer_turnover"] <= 0:
        percent_reasons.append("Оборот покупателя должен быть положительным")
    percent = None if percent_reasons else money(totals["profit"] / totals["buyer_turnover"] * 100)
    result["margin_percent"] = {
        "label": LABELS["margin_percent"],
        "value": percent,
        "known_value": percent,
        "complete": not percent_reasons,
        "status": "partial" if percent_reasons else "preliminary" if preliminary else "complete",
        "reasons": percent_reasons,
    }
    return result
