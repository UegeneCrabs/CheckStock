"""Compare scenarios from the same persisted calculator inputs."""

from datetime import date, datetime

from app.economics.wb import calculator
from app.economics.wb.standard_costs import number
from app.economics.wb.standard_logistics import logistics_with_returns as logistics_with_returns
from app.economics.wb.standard_storage import storage_per_unit
from app.repositories import wb_standard_costs

LABELS = dict(
    purchase="Закупка",
    fulfillment="Фулфилмент",
    advertising="Реклама",
    acquiring="Эквайринг",
    team="Комиссия компании",
    vat="НДС",
    tax="УСН / ОСНО",
    commission="Комиссия WB",
    logistics="Логистика",
    storage_total="Хранение",
)


def percent(base, rate):
    base, rate = number(base), number(rate)
    return base * rate / 100 if base is not None and rate is not None else None


def fallback(value, other):
    parsed = number(value)
    return parsed if parsed is not None else other


def calculate_scenario(values, *, retail, client, tax_system, days, shared_missing):
    missing = shared_missing + [LABELS[k] for k, v in values.items() if v is None]
    result = calculator.calculate(
        dict(
            retail=retail,
            client=client,
            purchase=values["purchase"],
            fulfillment=values["fulfillment"],
            advertisingRub=values["advertising"],
            acquiringRub=values["acquiring"],
            teamRub=values["team"],
            vatRub=values["vat"],
            secondaryTaxRub=values["tax"],
            commissionRub=values["commission"],
            logistics=values["logistics"],
            storageTotal=values["storage_total"],
        ),
        tax_system=tax_system,
        turnover_days=days,
        acquiring_percent=None,
        purchase_cost=values["purchase"],
    )
    if missing:
        result = dict(margin=None, roi=None)
    if values["purchase"] is not None and values["purchase"] <= 0:
        missing.append("Для ROI нужна положительная закупка")
    return {
        **values,
        **result,
        "retail": retail,
        "missing": missing,
        "complete": not missing,
        "total_cost": sum(values.values()) if all(v is not None for v in values.values()) else None,
    }


def compare(row, product):
    product = product or {}
    row["logistics_returns"] = logistics_with_returns(row, product)
    details, price = product.get("details") or {}, product.get("price") or {}
    ads, settings = product.get("advertising") or {}, product.get("product_settings") or {}
    retail = number(price.get("current"))
    client = number(price.get("with_spp"))
    if client is None:
        client = retail

    tax_system = details.get("tax_system", "usn")
    days, daily = number(details.get("storage_days")), number(settings.get("storage_wb_rub"))
    vat_rate = number(details.get("vat_percent"))
    vat = fallback(
        details.get("vat_value"),
        client * vat_rate / (100 + vat_rate)
        if client is not None and vat_rate is not None and vat_rate != -100
        else None,
    )
    costs = dict(
        purchase=number(details.get("purchase_cost")),
        fulfillment=number(details.get("fulfillment_cost")),
        advertising=number(ads.get("spend_per_order")),
        acquiring=percent(retail, details.get("acquiring")),
        team=percent(retail, details.get("team_commission_percent")),
        vat=vat,
        tax=number(details.get("osno_value" if tax_system == "osno" else "usn_value")),
        logistics=fallback(details.get("delivery_with_returns"), number(details.get("logistics"))),
        storage_total=fallback(
            details.get("storage_sum"), daily * days if daily is not None and days is not None else None
        ),
        commission=fallback(
            details.get("commission_value"), percent(retail, details.get("commission_percent"))
        ),
    )
    shared_missing = []
    if not product:
        shared_missing.append("Артикул отсутствует в локальной БД калькулятора")
    if retail is None or retail <= 0:
        shared_missing.append("Цена калькулятора")
    if "Не заполнены настройки кабинета WB" in product.get("data_errors", []):
        shared_missing.append("Не заполнены настройки кабинета WB")
        # Calculator defaults are not observed costs for an unconfigured cabinet.
        for key in ("acquiring", "team", "vat", "tax"):
            costs[key] = None
    coverage = ads.get("coverage")
    if coverage and not coverage.get("complete"):
        shared_missing.append("Неполные данные рекламы калькулятора")

    context = dict(
        retail=retail, client=client, tax_system=tax_system, days=days, shared_missing=shared_missing
    )
    current = calculate_scenario(costs, **context)
    row["storage_calculation"], storage_missing = storage_per_unit(row)
    standard = calculate_scenario(
        {
            **costs,
            "commission": percent(retail, row.get("commission_rate")),
            "logistics": row["logistics_returns"]["total"],
            "storage_total": row["storage_calculation"]["value"],
        },
        **context,
    )
    standard["missing"].extend(row["logistics_returns"]["missing"])
    standard["missing"].extend(storage_missing)
    delta = {
        key: round(standard[key] - current[key], 2)
        if standard[key] is not None and current[key] is not None
        else None
        for key in ("margin", "roi")
    }
    return standard, current, delta


def enrich(store, report):
    from app.web.routers.unit_economics import MOSCOW_TIMEZONE, load_wb_calculator_products

    start, end = date.fromisoformat(report["date_from"]), date.fromisoformat(report["date_to"])
    products, _ = load_wb_calculator_products(
        (store,),
        datetime.now(MOSCOW_TIMEZONE).date(),
        (end - start).days + 1,
        start,
        end,
        include_inactive=True,
    )
    by_article = {}
    for product in products:
        by_article.setdefault(str(product["article"]).partition(" / ")[0].strip(), []).append(product)
    photos = wb_standard_costs.photos(store)
    report.pop("calculator_inputs", None)
    for row in report["rows"]:
        matches = by_article.get(row["article"], [])
        product = matches[0] if len(matches) == 1 else None
        row["managers"] = sorted(
            {str(p.get("manager") or "").strip() for p in matches if str(p.get("manager") or "").strip()}
        )
        row["image_url"] = (product or {}).get("image_url") or photos.get(row["article"], "")
        economics = (product or {}).get("economics_7d") or {}
        row["calculator_turnover"] = {
            "value": number(economics.get("turnover")),
            "coverage": economics.get("turnover_coverage"),
        }
        row["profit"], row["current_profit"], row["delta"] = compare(row, product)
        details = (product or {}).get("details") or {}
        row["calculation_inputs"] = (
            {
                key: details.get(key)
                for key in (
                    "acquiring",
                    "team_commission_percent",
                    "vat_percent",
                    "usn_percent",
                    "osno_percent",
                    "tax_system",
                )
            }
            if "Не заполнены настройки кабинета WB" not in (product or {}).get("data_errors", [])
            else {}
        )
        row["calculation_inputs"]["client_price"] = ((product or {}).get("price") or {}).get("with_spp")
        if len(matches) > 1:
            for key in ("profit", "current_profit"):
                row[key]["missing"].append("Несколько размеров: требуется однозначное сопоставление")
        row["calculator_date"] = ((product or {}).get("price") or {}).get("snapshot_date")
    return report
