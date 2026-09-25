"""Explain missing current unit profit and ROI independently of calculator scenarios."""

from app.yandex.economics_calculation import LABELS


def current_issues(state, daily):
    values, result = state["values"], state["result"]
    issues = []
    for key in result["missing"]:
        # Daily source availability is reported separately, even when prices/costs are missing too.
        if key in {"advertising", "advertising_spend", "orders_count"}:
            continue
        label = LABELS.get(key, key)
        if key == "buyout_percent" and values.get(key) == 0:
            continue
        if key == "buyer_price":
            missing = []
            if values.get("seller_price") is None:
                missing.append("цена без СПП")
            if state["pricing"].get("spp_percent") is None:
                missing.append("последний процент СПП")
            reason = label + ": не получена с витрины."
            if missing:
                reason += " Для расчёта не хватает: " + ", ".join(missing) + "."
        elif key == "seller_price":
            reason = label + ": не загружена из API ЯМ."
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
    issues = list(dict.fromkeys(issues))
    if result["margin"] is None and not issues:
        issues = result["messages"] or ["Недостаточно данных для расчёта маржи на одну штуку."]
    roi_issues = list(issues)
    if daily["orders"] == 0 and daily["spend"] is not None and daily["spend"] > 0:
        roi_issues.append("Сегодня нет выкупленных товаров: ROI для расхода на рекламу не определяется.")
    if values.get("purchase_price") is not None and values["purchase_price"] <= 0:
        roi_issues.append("Закупочная стоимость равна 0 ₽: для ROI нужна положительная закупочная стоимость.")
    return {"margin": issues, "roi": roi_issues}
