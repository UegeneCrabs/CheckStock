import math

SOURCE_LABELS = {
    "catalog": "Каталог WB",
    "fbs": "Остатки FBS",
    "fbo": "Остатки FBO",
    "ff": "Остатки ФФ",
    "unit_economics_1c_prices": "Цены WB",
    "unit_economics_1c_wallet": "Цены СПП и WB Кошелька",
    "unit_economics_1c_advertising": "Реклама WB",
    "unit_economics_1c_funnel": "Заказы и воронка WB",
    "unit_economics_1c_buyout": "Процент выкупа WB",
    "unit_economics_1c_source": "Данные 1С",
    "unit_economics_1c_classifications": "ABC-классификация",
    "unit_economics_1c_commissions": "Комиссии WB",
    "unit_economics_1c_categories": "Категории WB",
}


def _missing_number(value: object) -> bool:
    try:
        return value is None or not math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def product_errors(
    product: dict, prices: dict, reference: dict, metrics: dict, reputation: dict,
    source_states: dict[str, dict] | None = None,
) -> list[str]:
    """Inspect source values before display defaults hide missing data."""
    errors = []
    for values, fields in (
        (reference, {
            "purchase_price": "Не загружена себестоимость",
            "fulfillment_cost": "Не загружены затраты на ФФ",
            "subject_commission_percent": "Не загружена комиссия WB",
            "team_commission_percent": "Не загружен процент маркетинговых затрат 1С",
        }),
        (prices, {
            "retail_price": "Не загружена цена продажи",
            "customer_price_with_spp": "Не загружена цена с СПП",
            "customer_price_with_wallet": "Не загружена цена WB Кошелька",
        }),
        (product, {
            "fbs_stock": "Не загружены остатки FBS",
            "fbo_stock": "Не загружены остатки FBO",
            "ff_available": "Не загружены остатки ФФ",
        }),
        (reputation, {
            "rating": "Не загружен рейтинг WB",
            "reviews_count": "Не загружено количество отзывов WB",
        }),
    ):
        for field, message in fields.items():
            if _missing_number(values.get(field)):
                stock_scope = {"fbs_stock": "fbs", "fbo_stock": "fbo", "ff_available": "ff"}.get(field)
                if stock_scope and (source_states or {}).get(stock_scope, {}).get("ok"):
                    continue
                errors.append(message)
    for field, message in (
        ("category", "Не загружена категория WB"),
        ("abc_code", "Не загружена ABC-классификация"),
    ):
        if not str(reference.get(field) or "").strip():
            errors.append(message)
    if not metrics.get("funnel_updated_at"):
        errors.append("Не загружены заказы и воронка WB за выбранный период")
    if not metrics.get("buyout_updated_at"):
        errors.append("Не загружен процент выкупа WB")
    if source_states is not None:
        advertising = source_states.get("unit_economics_1c_advertising")
        if advertising is None:
            errors.append("Не загружены данные рекламы WB")
        for scope, state in source_states.items():
            if scope in SOURCE_LABELS and not state.get("ok"):
                errors.append(f"{SOURCE_LABELS[scope]}: ошибка обновления, данные могут быть устаревшими")
    return list(dict.fromkeys(errors))
