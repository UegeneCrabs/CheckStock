"""Yandex field contracts, separate from the WB calculation vocabulary."""

from app.agents.yandex_reports import ECONOMIC, SHARED

NOTES = {
    "advertising": "Сохранённая дневная реклама Яндекс Маркета. group_by=article или day. Суммы относятся к загруженным дням; пропуски указаны в context.missing_days, context.complete=false. Пустая выборка не подтверждает отсутствие расходов.",
    "prices": "Только текущие цены витрины: продавца, покупателя и с Яндекс Пэй. Фильтр дат не поддерживается; устаревшая цена может быть null.",
    "profit": "Расчёт сайта Яндекс Маркета за период. expected_buyout_amount — ожидаемый оборот выкупов, не подтверждённая выручка. При неполной марже margin и roi равны null; report_margin и report_roi сохраняют частичный расчёт сайта.",
    "profit-calculator": "Сохранённый калькулятор Яндекс Маркета на единицу товара. Содержит собственные тарифы, налоги, доставку и расходы на невыкуп. Несохранённые изменения браузера не учитываются.",
    "target-prices": "Расчёт целевых цен Яндекс Маркета за последние семь завершённых дней. target_price — цена с Яндекс Пэй; target_spp_price — цена покупателя. Метод не меняет цены.",
    "product-newness": "Признак новинки из таблицы Яндекс Маркета. Возраст в днях не предоставляется: sales_days=null, age_known=false.",
    "current-economics": "Текущая маржа на единицу, ROI и СПП из таблицы Яндекс Маркета. СПП = (цена продавца − цена покупателя) / цена продавца × 100; скидка Яндекс Пэй не включена. Это не прибыль за период и не калькулятор сценария.",
    "costs": "Текущие справочные значения Яндекс Маркета из 1С. Не подтверждают себестоимость прошлых дат.",
    "economics-history": "Последний 21 день графика Яндекс Маркета. Текущий день неполный; неизвестные значения остаются null.",
}
KEEP = {
    "prices": "article name",
    "current-economics": "article name margin_per_unit_rub roi_percent spp_percent as_of_date calculation_available has_source_errors",
    "profit-calculator": "article name",
    "costs": "article name manager purchase_price fulfillment_cost abc_code goal_week goal_day fact_sales plan_sales",
    "profit": "article store_slug store_name name subject manager orders_count orders_amount cancel_count cancel_amount net_orders_count net_orders_amount buyout_percent advertising_spend impressions clicks ctr cpc drr margin roi margin_complete report_margin report_roi funnel_period_from funnel_period_to",
    "target-prices": "article name target_price target_warnings",
    "economics-history": "article day date label orders_count turnover_rub buyout_percent advertising_rub drr_percent margin_rub margin_complete messages stock_units fbs_units fbo_units fulfillment_units",
}
EXTRA = {
    "prices": [
        ("seller_price", "Цена продавца", "number|null", "₽"),
        ("buyer_price", "Цена покупателя", "number|null", "₽"),
        ("pay_price", "Цена с Яндекс Пэй", "number|null", "₽"),
        ("status", "Статус проверки витрины", "str|null", ""),
        ("checked_at", "Последняя проверка", "datetime|null", ""),
        ("price_checked_at", "Последняя проверка цены", "datetime|null", ""),
    ],
    "costs": [("synced_at", "Обновление справочника", "datetime|null", "")],
    "profit": [
        ("expected_buyout_amount", "Ожидаемый оборот выкупов", "number|null", "₽"),
        (
            "buyout_orders_count",
            "Заказы, использованные для взвешенного процента выкупа",
            "number|null",
            "шт.",
        ),
        ("margin_orders_count", "Расчётные выкупленные единицы для маржи", "number|null", "шт."),
        ("purchase_value", "Закупочная стоимость в расчёте", "number|null", "₽"),
        ("roi_purchase_value", "База закупочной стоимости для ROI", "number|null", "₽"),
        ("purchase_complete", "Полнота закупочной стоимости", "bool", ""),
        ("margin_missing_days", "Даты без полного расчёта маржи", "array", ""),
        ("orders_missing_days", "Даты без данных заказов", "array", ""),
        ("ads_missing_days", "Даты без расходов рекламы", "array", ""),
        ("missing_parameters", "Недостающие параметры расчёта", "array", ""),
        ("messages", "Ограничения расчёта", "array", ""),
        ("status", "Статус расчёта сайта", "str", ""),
        ("advertising_per_unit", "Расход рекламы на расчётный выкуп", "number|null", "₽/шт."),
    ],
    "target-prices": [
        ("store_slug", "Код магазина", "str", ""),
        ("manager", "Менеджер", "str|null", ""),
        ("code", "ABC-класс", "str|null", ""),
        ("current_price", "Текущая цена с Яндекс Пэй", "number|null", "₽"),
        ("target_retail_price", "Целевая цена продавца", "number|null", "₽"),
        ("target_spp_price", "Целевая цена покупателя", "number|null", "₽"),
        ("target_actual_roi", "ROI при целевой цене", "number|null", "%"),
        ("target_roi", "Цель ROI", "number|null", "%"),
        ("target_drr", "Цель ДРР с выкупом", "number|null", "%"),
        ("current_roi", "ROI за период", "number|null", "%"),
        ("current_drr", "ДРР за период", "number|null", "%"),
        ("target_overridden", "Есть индивидуальная цель товара", "bool", ""),
        ("current_drr_warnings", "Ограничения ДРР", "array", ""),
    ],
    "profit-calculator": [
        ("inputs", "Сохранённые исходные значения калькулятора", "object", ""),
        ("results", "Результат калькулятора Яндекс Маркета", "object", ""),
        ("origins", "Происхождение каждого исходного значения", "object", ""),
        ("pricing", "Данные цен для расчёта", "object", ""),
        ("tariff", "Тариф и признак приблизительного расчёта", "object", ""),
        ("advertising", "Основание расчёта рекламы за неделю", "object", ""),
        ("results.margin", "Маржа на единицу", "number|null", "₽"),
        ("results.roi", "ROI на единицу", "number|null", "%"),
        ("results.costs", "Расходы по статьям", "object", "₽"),
        ("results.logistics", "Доставка, возвраты и транзит", "object", "₽"),
        ("results.messages", "Недостающие данные и ограничения", "array", ""),
    ],
}
INPUT_LABELS = {
    "seller_price": "Цена продавца",
    "buyer_price": "Цена покупателя",
    "pay_price": "Цена с Яндекс Пэй",
    "purchase_price": "Закупочная цена",
    "fulfillment_cost": "Расходы ФФ",
    "category_id": "ID категории",
    "category_name": "Категория",
    "length": "Длина упаковки",
    "width": "Ширина упаковки",
    "height": "Высота упаковки",
    "weight": "Вес",
    "volume_l": "Объём упаковки",
    "commission_percent": "Комиссия Маркета",
    "payment_acceptance": "Приём платежа",
    "acquiring_percent": "Перевод платежа",
    "delivery_cost": "Доставка выкупленного товара",
    "delivery_customer": "Доставка покупателю",
    "middle_mile": "Средняя миля",
    "delivery_other": "Прочая доставка",
    "logistics_total": "Логистика всего",
    "logistics_returns": "Логистика возвратов",
    "repeat_delivery": "Повторная доставка",
    "return_middle_mile": "Обратная средняя миля",
    "return_cost": "Расход на невыкуп",
    "transit_cost": "Транзит",
    "company_commission_percent": "Комиссия компании",
    "vat_percent": "НДС",
    "usn_percent": "УСН",
    "loss_percent": "Потери от закупочной цены",
    "disposal_cost": "Утилизация потерянного товара",
    "buyout_percent": "Выкуп",
    "plan_drr": "ДРР с выкупом",
    "advertising_spend": "Расход рекламы",
    "advertising_per_buyout": "Реклама на выкуп",
    "advertising_mode": "Режим рекламы",
    "campaign_id": "ID кампании магазина",
    "frequency": "Частота выплат",
    "payment_delay_weeks": "Отсрочка выплат в неделях",
}


def guide(name, base):
    if name == "loss-products":
        return {
            "description": "Убыточные товары по отчёту Яндекс Маркета: только полная отрицательная маржа за период. Неполные расчёты исключаются. Даты обновления заказов и выкупов не предоставляются (null).",
            "fields": [
                dict(f)
                for f in base["fields"]
                if f["name"] not in {"orders_updated_at", "buyouts_updated_at"}
            ],
        }
    if name not in ECONOMIC | SHARED:
        return None
    fields = [dict(f) for f in base["fields"] if name not in KEEP or f["name"] in KEEP[name].split()]
    additions = list(EXTRA.get(name, []))
    if name == "profit-calculator":
        from app.dto.yandex_economics import EconomicsValues

        for key in EconomicsValues.model_fields:
            text = key in {"category_name", "advertising_mode", "frequency"}
            unit = "%" if key.endswith("percent") or key == "plan_drr" else ""
            additions.append(
                ("inputs." + key, INPUT_LABELS[key], "str|null" if text else "number|null", unit)
            )
    for key, label, kind, unit in additions:
        fields.append(
            {
                "name": key,
                "path": "rows[]." + key,
                "description": label,
                "type": kind,
                "unit": unit,
                "notes": "Яндекс Маркет",
            }
        )
    return {"description": NOTES.get(name, base["description"]), "fields": fields}
