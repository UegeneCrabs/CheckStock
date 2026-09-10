"""Saved-state calculator, equivalent to the website's rounded input fields."""

import math

INPUT_LABELS = {
    "retail": "Цена без СПП, руб", "client": "Цена с СПП, руб", "wallet": "Цена с WB Кошельком, руб",
    "spp": "СПП, %", "walletPercent": "WB Кошелёк, %", "commission": "Комиссия WB, %",
    "commissionRub": "Комиссия WB, руб", "drr": "ДРР с выкупом, % (7 дней)",
    "buyoutPercent": "Процент выкупа", "advertisingRub": "Рекламные расходы на единицу, руб",
    "logistics": "Логистика, руб", "storage": "Ставка хранения, руб/день",
    "storageTotal": "Хранение, руб", "acquiringPercent": "Эквайринг, %", "acquiringRub": "Эквайринг, руб",
    "purchase": "Закупочная стоимость, руб", "team": "Комиссия компании, %", "teamRub": "Комиссия компании, руб",
    "fulfillment": "Затраты на ФФ, руб", "vat": "НДС, %", "vatRub": "Налог НДС, руб",
    "usn": "УСН, %", "osno": "ОСНО, %", "secondaryTaxRub": "Налог УСН/ОСНО, руб",
}


def number(value, fallback=None):
    if value is None or value == "":
        return fallback
    try:
        result = float(value)
        return result if math.isfinite(result) else fallback
    except (ValueError, TypeError):
        return fallback


def mul(a, b, divisor=1):
    return None if a is None or b is None or divisor == 0 else a * b / divisor


def subtract(a, *values):
    return None if a is None or any(v is None for v in values) else a - sum(values)


def calculator(product):
    details = product.get("details") or {}
    saved = product.get("product_settings") or {}
    price = product.get("price") or {}
    ads = product.get("advertising") or {}
    retail = number(price.get("current"))
    client = number(price.get("with_spp"), retail)
    wallet = number(price.get("with_wallet"))
    commission = number(details.get("commission_percent"))
    acquiring = number(details.get("acquiring"))
    team = number(details.get("team_commission_percent"))
    storage = number(saved.get("storage_wb_rub"))
    days = number(details.get("storage_days"))
    vat = number(details.get("vat_percent"))
    tax_system = "osno" if product.get("store_slug") == "gogol" and details.get("tax_system") == "osno" else "usn"
    wallet_percent = None
    if client is not None and client > 0 and wallet is not None and wallet > 0:
        wallet_percent = next((p for p in range(100) if client - math.ceil(client * p / 100) == math.floor(wallet + .5)), None)
    if wallet_percent is None and client is not None and client > 0 and wallet is not None:
        wallet_percent = max(0, (client - wallet) / client * 100)
    values = {
        "retail": retail, "client": client, "wallet": wallet,
        "spp": (retail - client) / retail * 100 if retail is not None and retail > 0 and client is not None else None,
        "walletPercent": wallet_percent, "commission": commission,
        "commissionRub": number(details.get("commission_value"), mul(retail, commission, 100)),
        "drr": number(ads.get("drr")),
        "buyoutPercent": number(ads.get("buyout_percent"), number(details.get("buyout_percent"))),
        "advertisingRub": number(ads.get("spend_per_order"), 0),
        "logistics": number(details.get("delivery_with_returns"), number(details.get("logistics"))),
        "storage": storage, "storageTotal": number(details.get("storage_sum"), mul(storage, days)),
        "acquiringPercent": acquiring, "acquiringRub": mul(retail, acquiring, 100),
        "purchase": number(details.get("purchase_cost")), "team": team,
        "teamRub": mul(retail, team, 100), "fulfillment": number(details.get("fulfillment_cost")),
        "vat": vat, "vatRub": number(details.get("vat_value"), mul(client, vat, 100 + vat) if vat is not None else None),
        "usn": number(details.get("usn_percent")), "osno": number(details.get("osno_percent")),
        "secondaryTaxRub": number(details.get(tax_system + "_value")),
    }
    values = {k: math.floor(v * 100 + .5) / 100 if v is not None else None for k, v in values.items()}
    current = max(0, values["retail"]) if values["retail"] is not None else None
    client = number(values["client"], current)
    client = max(0, client) if client is not None else None
    wallet = number(values["wallet"], client)
    wallet = max(0, wallet) if wallet is not None else None
    vat = number(values["vatRub"], mul(client, values["vat"], 100 + values["vat"]) if values["vat"] is not None else None)
    secondary = number(values["secondaryTaxRub"], mul(client if tax_system == "osno" else subtract(client, vat), values[tax_system], 100))
    tax = vat + secondary if vat is not None and secondary is not None else None
    purchase = number(values["purchase"], number(details.get("purchase_cost")))
    acquiring = number(values["acquiringRub"], mul(current, number(values["acquiringPercent"], number(details.get("acquiring"))), 100))
    commission = number(values["commissionRub"], mul(current, values["commission"], 100))
    team = number(values["teamRub"], mul(current, values["team"], 100))
    storage = number(values["storageTotal"], mul(values["storage"], days))
    revenue = subtract(current, acquiring, values["logistics"], storage, commission, values["advertisingRub"])
    margin = subtract(revenue, purchase, values["fulfillment"], team, tax)
    results = {"current": current, "sppPrice": client, "walletPrice": wallet, "netRevenue": revenue,
        "purchase": purchase, "fulfillment": values["fulfillment"], "acquiring": acquiring,
        "commission": commission, "teamCommission": team, "logistics": values["logistics"],
        "storage": storage, "vat": vat, "secondaryTax": secondary,
        "secondaryTaxLabel": "ОСНО" if tax_system == "osno" else "УСН", "tax": tax,
        "advertising": values["advertisingRub"], "margin": margin,
        "roi": margin / purchase * 100 if margin is not None and purchase is not None and purchase > 0 else None}
    return {"article": product["article"], "name": product.get("name"),
        "subject": details.get("subject"), "tax_system": tax_system,
        "inputs": values, "results": results,
        "input_labels": INPUT_LABELS,
        "result_labels": {"current": "Цена без СПП, руб", "sppPrice": "Цена с СПП, руб",
            "walletPrice": "Цена с WB Кошельком, руб", "netRevenue": "Выручка после расходов WB и рекламы на единицу, руб",
            "purchase": "Закупочная стоимость, руб", "fulfillment": "ФФ, руб", "acquiring": "Эквайринг, руб",
            "commission": "Комиссия WB, руб", "teamCommission": "Комиссия компании, руб",
            "logistics": "Логистика, руб", "storage": "Хранение, руб", "vat": "НДС, руб",
            "secondaryTax": "УСН/ОСНО, руб", "secondaryTaxLabel": "Название налога", "tax": "Все налоги, руб",
            "advertising": "Реклама на единицу, руб", "margin": "Чистая прибыль на единицу, руб",
            "roi": "ROI калькулятора, %", "target_price": "Целевая цена с WB Кошельком, руб"},
        "calculation_complete": margin is not None,
        "missing_inputs": [k for k in ("current", "purchase", "fulfillment", "acquiring", "commission", "teamCommission", "logistics", "storage", "tax", "advertising") if results[k] is None]}
