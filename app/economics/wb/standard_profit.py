"""Compare scenarios from the same persisted calculator inputs."""

from datetime import date, datetime

from app.dto.wb_standard_costs import (
    CalculationInputs,
    CalculatorProduct,
    CalculatorValues,
    Comparison,
    CostValues,
    MarginRoi,
    ReportRow,
    Scenario,
    Snapshot,
    Turnover,
)
from app.economics.wb import calculator
from app.economics.wb.standard_costs import number
from app.economics.wb.standard_logistics import logistics_with_returns
from app.economics.wb.standard_storage import storage_per_unit
from app.repositories import wb_standard_costs

COST_LABELS = (
    ("purchase", "Закупка"),
    ("fulfillment", "Фулфилмент"),
    ("advertising", "Реклама"),
    ("acquiring", "Эквайринг"),
    ("team", "Комиссия компании"),
    ("vat", "НДС"),
    ("tax", "УСН / ОСНО"),
    ("logistics", "Логистика"),
    ("storage_total", "Хранение"),
    ("commission", "Комиссия WB"),
)


def percent(base: object, rate: object) -> float | None:
    base, rate = number(base), number(rate)
    return base * rate / 100 if base is not None and rate is not None else None


def fallback(value: object, other: float | None) -> float | None:
    parsed = number(value)
    return parsed if parsed is not None else other


def calculate_scenario(
    values: CostValues,
    *,
    retail: float | None,
    client: float | None,
    tax_system: str,
    days: float | None,
    shared_missing: list[str],
) -> Scenario:
    missing = shared_missing + [label for key, label in COST_LABELS if getattr(values, key) is None]
    result = calculator.calculate(
        CalculatorValues(
            retail=retail,
            client=client,
            purchase=values.purchase,
            fulfillment=values.fulfillment,
            advertisingRub=values.advertising,
            acquiringRub=values.acquiring,
            teamRub=values.team,
            vatRub=values.vat,
            secondaryTaxRub=values.tax,
            commissionRub=values.commission,
            logistics=values.logistics,
            storageTotal=values.storage_total,
        ),
        tax_system=tax_system,
        turnover_days=days,
        acquiring_percent=None,
        purchase_cost=values.purchase,
    )
    if missing:
        result = MarginRoi(margin=None, roi=None)
    if values.purchase is not None and values.purchase <= 0:
        missing.append("Для ROI нужна положительная закупка")
    # Keep the original summation order and full precision of the cost values.
    costs = [getattr(values, key) for key in CostValues.model_fields]
    return Scenario(
        purchase=values.purchase,
        fulfillment=values.fulfillment,
        advertising=values.advertising,
        acquiring=values.acquiring,
        team=values.team,
        vat=values.vat,
        tax=values.tax,
        logistics=values.logistics,
        storage_total=values.storage_total,
        commission=values.commission,
        margin=result.margin,
        roi=result.roi,
        retail=retail,
        missing=missing,
        complete=not missing,
        total_cost=sum(costs) if all(v is not None for v in costs) else None,
    )


def compare(row: ReportRow, product: CalculatorProduct | None) -> Comparison:
    absent_product = product is None
    product = product or CalculatorProduct()
    logistics_returns = logistics_with_returns(row, product)
    details, price = product.details, product.price
    ads, settings = product.advertising, product.product_settings
    retail = number(price.current)
    client = number(price.with_spp)
    if client is None:
        client = retail

    tax_system = details.tax_system
    days, daily = number(details.storage_days), number(settings.storage_wb_rub)
    vat_rate = number(details.vat_percent)
    vat = fallback(
        details.vat_value,
        client * vat_rate / (100 + vat_rate)
        if client is not None and vat_rate is not None and vat_rate != -100
        else None,
    )
    costs = CostValues(
        purchase=number(details.purchase_cost),
        fulfillment=number(details.fulfillment_cost),
        # Match databaseCalculatorValues: missing advertising defaults to zero.
        advertising=fallback(ads.spend_per_order, 0.0),
        acquiring=percent(retail, details.acquiring),
        team=percent(retail, details.team_commission_percent),
        vat=vat,
        tax=number(details.osno_value if tax_system == "osno" else details.usn_value),
        logistics=fallback(details.delivery_with_returns, number(details.logistics)),
        storage_total=fallback(
            details.storage_sum, daily * days if daily is not None and days is not None else None
        ),
        commission=fallback(details.commission_value, percent(retail, details.commission_percent)),
    )
    shared_missing = []
    if absent_product:
        shared_missing.append("Артикул отсутствует в локальной БД калькулятора")
    if retail is None or retail <= 0:
        shared_missing.append("Цена калькулятора")
    if "Не заполнены настройки кабинета WB" in product.data_errors:
        shared_missing.append("Не заполнены настройки кабинета WB")
        costs = costs.model_copy(update={"acquiring": None, "team": None, "vat": None, "tax": None})

    current = calculate_scenario(
        costs, retail=retail, client=client, tax_system=tax_system, days=days, shared_missing=shared_missing
    )
    storage_calculation, storage_missing = storage_per_unit(row)
    standard_costs = costs.model_copy(
        update={
            "commission": percent(retail, row.commission_rate),
            "logistics": logistics_returns.total,
            "storage_total": storage_calculation.value,
        }
    )
    standard = calculate_scenario(
        standard_costs,
        retail=retail,
        client=client,
        tax_system=tax_system,
        days=days,
        shared_missing=shared_missing,
    )
    standard = standard.model_copy(
        update={"missing": standard.missing + logistics_returns.missing + tuple(storage_missing)}
    )
    delta = MarginRoi(
        margin=round(standard.margin - current.margin, 2)
        if standard.margin is not None and current.margin is not None
        else None,
        roi=round(standard.roi - current.roi, 2)
        if standard.roi is not None and current.roi is not None
        else None,
    )
    return Comparison(standard, current, delta, logistics_returns, storage_calculation)


def enrich(store: str, report: Snapshot) -> Snapshot:
    from app.core.domain import MOSCOW_TIMEZONE
    from app.economics.wb.catalogue import load_wb_calculator_products
    from app.economics.wb.standard_sources import calculator_product

    start, end = date.fromisoformat(report.date_from), date.fromisoformat(report.date_to)
    raw_products, _ = load_wb_calculator_products(
        (store,),
        datetime.now(MOSCOW_TIMEZONE).date(),
        (end - start).days + 1,
        start,
        end,
        include_inactive=True,
    )
    # The existing catalogue still exposes JSON-shaped records; adapt once on entry.
    products = [calculator_product(product) for product in raw_products]
    by_article: dict[str, list[CalculatorProduct]] = {}
    for product in products:
        by_article.setdefault(product.article.partition(" / ")[0].strip(), []).append(product)
    photos = {photo.article: photo.url for photo in wb_standard_costs.photos(store)}
    enriched = []
    for row in report.rows:
        matches = by_article.get(row.article, [])
        product = matches[0] if len(matches) == 1 else None
        managers = sorted({str(p.manager or "").strip() for p in matches if str(p.manager or "").strip()})
        image_url = (product.image_url if product else None) or photos.get(row.article, "")
        economics = product.economics_7d if product else None
        calculator_turnover = Turnover(
            value=number(economics.turnover) if economics else None,
            coverage=economics.turnover_coverage if economics else None,
        )
        comparison = compare(row, product)
        standard, current = comparison.standard, comparison.current
        details = product.details if product else None
        if product is None or "Не заполнены настройки кабинета WB" not in product.data_errors:
            calculation_inputs = CalculationInputs(
                acquiring=details.acquiring if details else None,
                team_commission_percent=details.team_commission_percent if details else None,
                vat_percent=details.vat_percent if details else None,
                usn_percent=details.usn_percent if details else None,
                osno_percent=details.osno_percent if details else None,
                tax_system=details.tax_system
                if details and "tax_system" in details.model_fields_set
                else None,
            )
        else:
            calculation_inputs = CalculationInputs()
        calculation_inputs = calculation_inputs.model_copy(
            update={"client_price": product.price.with_spp if product else None}
        )
        if len(matches) > 1:
            reason = ("Несколько размеров: требуется однозначное сопоставление",)
            standard = standard.model_copy(update={"missing": standard.missing + reason})
            current = current.model_copy(update={"missing": current.missing + reason})
        enriched.append(
            row.model_copy(
                update={
                    "managers": tuple(managers),
                    "image_url": image_url,
                    "calculator_turnover": calculator_turnover,
                    "profit": standard,
                    "current_profit": current,
                    "delta": comparison.delta,
                    "logistics_returns": comparison.logistics_returns,
                    "storage_calculation": comparison.storage_calculation,
                    "calculation_inputs": calculation_inputs,
                    "calculator_date": product.price.snapshot_date if product else None,
                }
            )
        )
    return report.model_copy(update={"rows": tuple(enriched)})
