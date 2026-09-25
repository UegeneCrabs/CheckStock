"""Completeness travels with numbers, including partial daily/period results."""

from decimal import Decimal

LABELS = {
    "retail_price": "цена продавца", "seller_price": "цена продавца",
    "customer_price": "цена покупателя", "buyer_price": "цена покупателя",
    "purchase_price": "закупочная цена", "fulfillment_cost": "фулфилмент",
    "subject_commission_percent": "комиссия предмета", "wb_extra_tariff_percent": "доптариф WB",
    "commission_percent": "комиссия маркетплейса", "acquiring_percent": "эквайринг",
    "delivery_wb_rub": "доставка WB", "return_cost_rub": "стоимость возврата",
    "delivery_with_returns": "логистика с возвратами", "storage_wb_rub": "хранение",
    "turnover_days": "оборачиваемость", "acceptance_coefficient": "коэффициент приёмки",
    "volume_l": "объём", "team_commission_percent": "комиссия компании",
    "company_commission_percent": "комиссия компании", "tax_system": "налоговая система",
    "vat_percent": "ставка НДС", "usn_percent": "ставка УСН", "osno_percent": "ставка ОСНО",
    "buyout_percent": "процент выкупа", "orders_count": "число заказов",
    "advertising_spend": "расходы на рекламу", "advertising_rub": "реклама на единицу",
    "advertising_per_buyout": "реклама на выкуп", "plan_drr": "плановый ДРР",
    "payment_acceptance": "приём платежа", "delivery_cost": "доставка",
    "return_cost": "обратная доставка", "middle_mile": "средняя миля",
    "transit_cost": "транзит", "loss_percent": "процент потерь",
    "disposal_cost": "утилизация", "snapshot": "дневной снимок параметров",
}


def describe(missing, day=None):
    return [LABELS.get(key, key) + (f" за {day}" if day else "") for key in dict.fromkeys(missing)]


def status(value, missing):
    return "Недостаточно данных" if value is None else "Неполный расчёт" if missing else "Полный расчёт"


def annotate(result, missing):
    missing = list(dict.fromkeys(missing))
    result.update(missing=missing, complete=not missing, status=status(result.get("margin"), missing),
                  messages=["Не учтены / неизвестны: " + ", ".join(describe(missing))] if missing else [])
    return result


def aggregate_days(rows, expected_dates, money):
    """Sum computable days; purchase coverage is independent of other expenses."""
    dates = sorted(set(expected_dates))
    present = {row["day"]: row for row in rows}
    known = [present[d] for d in dates if d in present]
    covered = [r for r in known if r.get("profit") is not None]
    purchases = [r for r in known if r.get("purchase_value") is not None]
    missing = {d: list(dict.fromkeys(present.get(d, {}).get("missing") or
               ([] if d in present and present[d].get("profit") is not None else ["snapshot"]))) for d in dates}
    missing = {d: keys for d, keys in missing.items() if keys}
    unavailable = [d for d in dates if present.get(d, {}).get("profit") is None]
    profit = sum((Decimal(str(r["profit"])) for r in covered), Decimal(0))
    purchase = sum((Decimal(str(r["purchase_value"])) for r in purchases), Decimal(0))
    # A ratio cannot use a denominator covering fewer days than its numerator.
    roi_basis_known = bool(covered) and all(r.get("purchase_value") is not None for r in covered)
    roi_basis = sum((Decimal(str(r["purchase_value"])) for r in covered), Decimal(0)) if roi_basis_known else None
    messages = ["Не учтены / неизвестны: " + ", ".join(describe(keys, d)) for d, keys in missing.items()]
    if unavailable:
        messages.append("В сумму не вошли дни без основы расчёта: " + ", ".join(unavailable))
    coverage = {"dates": [r["day"] for r in covered], "days": len(covered), "expected_days": len(dates),
                "complete": not missing, "missing_dates": sorted(missing), "missing_parameters": missing,
                "unavailable_dates": unavailable, "messages": messages,
                "period_from": covered[0]["day"] if covered else None,
                "period_to": covered[-1]["day"] if covered else None}
    margin = money(profit) if covered else None
    return {"margin": margin, "purchase_value": money(purchase) if purchases else None,
            "purchase_complete": len(purchases) == len(dates),
            "roi": money(profit / roi_basis * 100) if roi_basis and roi_basis > 0 else None,
            "roi_complete": not missing and roi_basis_known and bool(roi_basis),
            "roi_purchase_value": money(roi_basis) if roi_basis is not None else None,
            "complete": not missing, "status": status(margin, missing), "coverage": coverage,
            "missing_days": sorted(missing), "missing_parameters": missing, "messages": messages,
            "unavailable_days": unavailable,
            "unallocated_advertising": money(sum(float(r.get("advertising_spend") or 0) for r in known if r["day"] in unavailable))}
