"""Explain missing current unit profit and ROI independently of calculator scenarios."""

from app.yandex.economics_calculation import LABELS


def current_issues(state, daily):
    values, result = state["values"], state["result"]
    issues = []
    for key in result["missing"]:
        # Daily source availability is reported separately, even when prices/costs are missing too.
        if key in {"advertising", "orders_count"}:
            continue
        label = LABELS.get(key, key)
        if key == "buyout_percent" and values.get(key) == 0:
            continue
        if key == "buyer_price":
            missing = []
            if values.get("seller_price") is None:
                missing.append("цена продавца")
            if state["pricing"].get("spp_percent") is None:
                missing.append("последний процент СПП")
            reason = "Цена покупателя без Пэй: не получена с витрины."
            if missing:
                reason += " Для расчёта не хватает: " + ", ".join(missing) + "."
        elif key == "seller_price":
            reason = "Цена продавца: не загружена из API ЯМ."
        elif state["origins"].get(key) == "Нет тарифа за сегодня":
            reason = label + ": нет актуального тарифа за сегодня."
        elif key == "return_cost":
            dependencies = [
                label
                for field, label in (("length", "длина"), ("width", "ширина"), ("height", "высота"))
                if values.get(field) is None or values[field] <= 0
            ]
            reason = (
                label + ": не заданы " + ", ".join(dependencies) + "."
                if dependencies
                else "Не задано: " + label + "."
            )
        else:
            reason = "Не задано: " + label + "."
        issues.append(reason)

    issues.extend(daily["issues"])
    if values.get("buyout_percent") is not None and values["buyout_percent"] <= 0:
        issues.append("Выкуп равен 0%: прибыль на выкупленную единицу не определена.")
    if daily["spend"] is not None and daily["spend"] > 0 and daily["orders"] == 0:
        issues.append(
            "За сегодня есть расходы на рекламу, но заказов по модели "
            + state["scheme"]
            + " нет: нельзя рассчитать рекламу на одну выкупленную штуку."
        )
    issues = list(dict.fromkeys(issues))
    if result["margin"] is None and not issues:
        issues = result["messages"] or ["Недостаточно данных для расчёта маржи на одну штуку."]
    roi_issues = list(issues)
    if values.get("purchase_price") is not None and values["purchase_price"] <= 0:
        roi_issues.append("Закупочная цена равна 0 ₽: для ROI нужна положительная закупочная цена.")
    return {"margin": issues, "roi": roi_issues}
