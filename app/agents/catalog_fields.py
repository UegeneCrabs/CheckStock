"""Response field guide. Paths are JSON paths, [] denotes an array item."""

EXTRA_FIELDS = {
    "target-prices": "current_price:Текущая цена с кошельком, ₽:number|null|current_drr:Текущий ДРР, %:number|null|current_roi:Текущий ROI, %:number|null|target_drr:Цель ДРР, %:number|null|target_roi:Цель ROI, %:number|null|target_overridden:Цели заданы для товара:bool|cabinet_target_drr:Цель ДРР кабинета, %:number|null|cabinet_target_roi:Цель ROI кабинета, %:number|null|target_retail_price:Целевая цена без СПП, ₽:number|null|target_spp_price:Целевая цена с СПП, ₽:number|null|target_actual_roi:ROI при целевой цене, %:number|null|target_advertising_rub:Реклама при целевой цене, ₽:number|null|price_date:Дата цены:date|null|weekly:Показатели недели и полнота источников:object|current_warnings:Ограничения текущего расчёта:array|current_notes:Примечания к текущему расчёту:array|calculator:Параметры целевого калькулятора:object",
    "loss-products": "store:Код магазина:str|name:Название товара:str|orders_amount_rub:ТО заказов, ₽:number|null|advertising_spend_rub:Расход рекламы, ₽:number|null|orders_updated_at:Обновление заказов:datetime|null|buyouts_updated_at:Обновление выкупов:datetime|null",
    "products": "mp_product_id:Идентификатор продукта:str|null|image_url:Ссылка на изображение:str|null|mp_updated_at:Обновление каталога:datetime|null",
    "product-details": "mp_sku:Идентификатор площадки:str|null|mp_product_id:Идентификатор продукта:str|null|image_url:Ссылка на изображение:str|null|mp_updated_at:Обновление каталога:datetime|null|context.stock[].article:Артикул остатка:str|context.stock[].scheme:Схема хранения:str|context.stock[].quantity:Количество:number|null|context.stock[].warehouse_breakdown:Склады площадки:array",
    "current-economics": "name:Название товара:str|null|calculation_available:Доступен расчёт маржи:bool",
    "product-newness": "name:Название товара:str|null",
    "product-reputation": "name:Название товара:str|null",
    "product-tags": "name:Название товара:str|null",
    "advertising": "updated_at:Обновление источника:datetime|null",
    "advertising-campaigns": "campaign_id:Идентификатор кампании:str|campaign_status:Код статуса кампании:number|is_active:Кампания активна, код 9:bool|name:Название товара:str|null|updated_at:Время снимка кампании:datetime|context.available:Полный и свежий снимок доступен:bool|context.status_observed_at:Когда проверялся статус:datetime|context.unavailable_reason:Причина недоступности:str",
    "stocks": "source:Источник marketplace/fulfillment/transit:str|warehouse:Склад для фильтра склада:str|null|updated_at:Обновление остатка:datetime|null|warehouse_breakdown[].warehouse:Название склада:str|warehouse_breakdown[].quantity:Остаток на складе:number|warehouse_breakdown[].updated_at:Дата обновления склада:datetime|null|fbo_stock:Остаток FBO в summary:number|null|fbs_stock:Остаток FBS в summary:number|null|missing_components:Неизвестные компоненты сводки:array|context.labels:Подписи колонок для площадки:object",
    "stock-value": "scheme:Схема хранения:str|warehouse:Склад:str|null|source:Источник остатка:str|updated_at:Обновление остатка:datetime|null|warehouse_breakdown:Разбивка по складам:array",
    "stock-operations": "barcode:Штрихкод:str|null|name:Название товара:str|null|from_fulfillment:ФФ отправления:str|null|to_fulfillment:ФФ получения:str|null|from_marketplace:Исходная площадка:str|null|to_marketplace:Целевая площадка:str|null",
    "supplies": "preorder_id:Идентификатор предварительного заказа:str|supply_type:Тип поставки:str|null|is_urgent:Срочная поставка:bool|null|id:Идентификатор ручного плана:number|source:manual для ручного плана:str|origin:Откуда, ручной план:str|null|destination:Куда, ручной план:str|null|ready:Готовность ручного плана:bool|null",
    "prices": "club_discounted_price:Цена WB Клуба, ₽:number|null|updated_at:Обновление цены:datetime|null",
    "costs": "name:Название товара:str|null|goal_week:Цель недели:number|null|goal_day:Цель дня:number|null|fact_sales:Фактические продажи из справочника:number|null|plan_sales:План продаж из справочника:number|null",
    "profit": "store_slug:Код магазина:str|store_name:Название магазина:str|name:Название товара:str|null|subject:Предмет товара:str|null|manager:Менеджер:str|null|cancel_count:Количество отмен:number|null|net_orders_count:Заказы после отмен:number|null|buyout_count:Количество выкупов:number|null|buyout_amount:Сумма выкупов, ₽:number|null|buyout_percent:Выкуп, %:number|null|buyout_period_from:Начало периода выкупа:date|null|buyout_period_to:Конец периода выкупа:date|null|buyout_updated_at:Обновление выкупов:datetime|null|funnel_updated_at:Обновление воронки:datetime|null|funnel_period_from:Начало периода воронки:date|null|funnel_period_to:Конец периода воронки:date|null|impressions:Рекламные показы:number|null|clicks:Рекламные клики:number|null|ctr:CTR, %:number|null|cpc:Стоимость клика, ₽:number|null|drr:ДРР с выкупом, %:number|null|margin_missing_days:Дни без расчёта маржи:number|report_roi:ROI сайта, возможно неполный, %:number|null",
    "orders": "line_key:Ключ строки заказа:str|barcode:Штрихкод:str|null|name:Название товара:str|null|scheme:Схема fbo/fbs:str|substatus:Уточнение статуса:str|null|source_updated_at:Последнее изменение в источнике:datetime|null|cancelled_at:Дата отмены:datetime|null|sold_at:Дата продажи:datetime|null|returned_at:Дата возврата:datetime|null|cancelled_amount:Сумма отмен, ₽:number|null|sale_amount:Сумма продаж, ₽:number|null|return_amount:Сумма возвратов, ₽:number|null|currency:Код валюты:str|updated_at:Время сохранения строки:datetime",
    "inbound-supplies": "order_id:Номер заказа поставки:str|number:Номер документа:str|status:Исходный статус:str|status_label:Название статуса:str|warehouse:Склад назначения:str|transit_warehouse:Транзитный склад:str|planned_at:Плановая дата:str|created_at:Создание поставки:str|updated_at:Обновление поставки:str|checked_at:Последняя проверка:str|unavailable:Данные поставки недоступны:bool|warning:Ограничения поставки:str|barcode:Штрихкод:str|name:Название товара:str|sku:SKU:str|vendor_code:Артикул продавца:str|surplus_quantity:Излишек:number|null|defect_quantity:Брак:number|null",
    "supply-arrivals": "row:Номер строки реестра:number|store_slug:Код магазина:str|project:Проект:str|group:Группа:str|category:Категория:str|shipping:Способ доставки:str|warnings:Предупреждения строки:array",
    "economics-history": "date:Дата графика:date|label:Дата для отображения:str|messages:Недостающие данные:array|drr_percent:ДРР, %:number|null|orders_count:Число заказов:number|null|purchased_units:Расчётные выкупленные единицы:number|null|buyout_percent:Выкуп, %:number|null|fbs_units:Остаток FBS:number|null|fbo_units:Остаток FBO:number|null|fulfillment_units:Остаток ФФ:number|null|purchase_value:Закупочная стоимость заказанных товаров, ₽:number|null",
    "article-stores": "article:Запрошенный артикул:str|matches[].store:Код магазина:str|matches[].marketplace:Площадка:str|matches[].article:Артикул:str",
    "stores": "marketplace:Площадка по умолчанию:str",
    "capabilities": "read_only:Доступ только для чтения:bool|reports[].report:Код метода:str|reports[].path:Адрес метода:str|reports[].description:Назначение:str|reports[].filters:Поддерживаемые фильтры:array|unavailable:Неподдерживаемые разделы:array",
    "data-status": "sources[].source:Название источника:str|sources[].records:Число записей:number|sources[].updated_at:Последнее обновление:datetime|null|sources[].first_observed:Первая сохранённая дата:date|null|sources[].last_observed:Последняя сохранённая дата:date|null",
    "product-analysis": "rows[].store:Магазин:str|rows[].article:Артикул:str|rows[].name:Название:str|rows[].manager:Менеджер:str|null|rows[].orders_amount:ТО заказов, ₽:number|null|rows[].status:match или unknown:str|rows[].missing_data:Причины неопределённости:array|totals.checked:Проверено товаров:number|totals.matched:Подходящих товаров:number|totals.unknown:Недостаточно данных:number|totals.no_match:Не подошли под условие:number|complete:Проверка полная:bool|failed_stores:Магазины с ошибками:array",
    "profit-summary": "rows[].store:Магазин:str|rows[].name:Название магазина:str|rows[].turnover_change_rub:Изменение ТО, ₽:number|null|rows[].turnover_change_percent:Изменение ТО, %:number|null|rows[].turnover_comparison_partial:Сравнение неполное:bool",
    "stock-cost-report": "id:Номер операции в деталях:number|kind:Тип операции в деталях:str|created_at:Время операции в деталях:datetime|barcode:Штрихкод в деталях:str|null|name:Товар в деталях:str|null|from_marketplace:Откуда, площадка:str|null|to_marketplace:Куда, площадка:str|null|from_fulfillment:Откуда, ФФ:str|null|to_fulfillment:Куда, ФФ:str|null|is_fbs_transfer:Перемещение на FBS:bool|start_quantity:Начальный остаток в fbs_sales:number|moved_quantity:Перемещено в fbs_sales:number|end_quantity:Конечный остаток в fbs_sales:number",
}

ENVELOPE = [
    ("store", "Магазин запроса", "str|null"),
    ("marketplace", "Площадка", "str"),
    ("currency", "Валюта RUB", "str"),
    ("timezone", "Часовой пояс расчётов", "str"),
    ("generated_at", "Время формирования ответа, не обновления источников", "datetime"),
    ("date_from", "Начало периода включительно", "date|null"),
    ("date_to", "Конец периода включительно", "date|null"),
    ("total_rows", "Строк во всей отфильтрованной выборке", "number"),
    ("next_offset", "Смещение следующей порции; null — конец", "number|null"),
    ("totals", "Итоги по выборке; состав зависит от метода", "object"),
    ("warnings", "Ограничения и предупреждения", "array"),
    ("sources", "Источники и свежесть данных", "array"),
    ("context", "Методика, подписи и условия расчёта", "object"),
]


def enrich(name, fields):
    import re

    def add(key, label, kind):
        if not any(f["name"] == key for f in fields):
            fields.append({"name": key, "description": label, "type": kind})

    for entry in re.split(r"\|(?=[\w.\[\]]+:)", EXTRA_FIELDS.get(name, "")):
        if entry:
            add(*entry.split(":", 2))
    if name == "advertising-campaigns":
        fields[:] = [f for f in fields if f["name"] != "cpc_rub"]  # not returned by this method
    if name == "profit-calculator":
        from app.agents.calculator import INPUT_LABELS, calculator

        sample = calculator({"article": "demo"})
        add("results.target_price", "Целевая цена с кошельком, ₽", "number|null")
        fields[:] = [f for f in fields if f["name"] != "labels"]
        for key, label in INPUT_LABELS.items():
            add("inputs." + key, label, "number|null")
        for key in sample["results"]:
            add(
                "results." + key,
                sample["result_labels"][key],
                "str" if key == "secondaryTaxLabel" else "number|null",
            )
        for key, label, kind in [
            ("input_labels", "Подписи параметров", "object"),
            ("result_labels", "Подписи результатов", "object"),
            ("calculation_complete", "Расчёт полный", "bool"),
            ("missing_inputs", "Недостающие параметры", "array"),
            ("tax_system", "Система налогообложения", "str"),
            ("name", "Название товара", "str|null"),
        ]:
            add(key, label, kind)
    if name == "profit-summary":
        for period in ("current", "previous"):
            for key, label, kind in [
                ("available", "Период доступен", "bool"),
                ("orders_amount", "ТО заказов, ₽", "number|null"),
                ("ordered_products_margin", "Маржа товаров с заказами, ₽", "number|null"),
                ("website_margin", "Полная маржа сайта, ₽", "number|null"),
                ("products_count", "Всего товаров", "number"),
                ("products_with_orders", "Товары с заказами", "number"),
                ("margin_included_products", "Товары с полной маржой", "number"),
                ("margin_excluded_products", "Товары вне расчёта маржи", "number"),
                ("orders_complete", "Заказы полные", "bool"),
                ("margin_complete", "Маржа полная", "bool"),
                ("data_status", "complete/partial/missing", "str"),
            ]:
                add(
                    "rows[]." + period + "." + key,
                    ("Текущий: " if period == "current" else "Предыдущий: ") + label,
                    kind,
                )
    if name == "stock-cost-report":
        for metric, label in [
            ("deliveries", "Поступления"),
            ("moved_in", "Перемещения внутрь"),
            ("moved_out", "Перемещения наружу"),
            ("shipped", "Отгрузки"),
            ("fbs_sales", "Продажи FBS по формуле"),
            ("fbs_actual_sales", "Фактические продажи FBS"),
        ]:
            for key, desc in [
                ("operations", "операций"),
                ("positions", "позиций"),
                ("units", "единиц"),
                ("cost", "стоимость, ₽"),
                ("missing_units", "единиц без цены"),
            ]:
                add(metric + "." + key, label + ": " + desc, "number")
        add("context.reconciliation[].available", "Хватает снимков для формулы FBS", "bool")
    root_methods = {
        "stores",
        "capabilities",
        "article-stores",
        "data-status",
        "profit-summary",
        "product-analysis",
    }
    for f in fields:
        key = f["name"]
        root = (
            name in root_methods
            or key.startswith("context.")
            or (name == "loss-products" and key == "excluded_incomplete_products")
        )
        f["path"] = ("[]." if name == "stores" else "" if root else "rows[].") + key
        text = f["description"]
        f["unit"] = "₽" if "₽" in text or "руб" in text else "%" if "%" in text else "—"
        f["notes"] = "null — данных недостаточно; не заменять нулём." if "null" in f["type"] else ""
        if name == "stocks":
            f["notes"] += " " + (
                "view=summary."
                if key
                in {
                    "total",
                    "ff_available",
                    "transit",
                    "fbo_stock",
                    "fbs_stock",
                    "missing_components",
                    "context.labels",
                }
                else "view=details."
                if key not in {"article"}
                else ""
            )
        if name == "stock-cost-report" and key.split(".")[0] in {
            "deliveries",
            "moved_in",
            "moved_out",
            "shipped",
            "fbs_sales",
            "fbs_actual_sales",
        }:
            f["notes"] += " cost_view=summary."
        if name == "profit-summary" and (".current." in key or ".previous." in key):
            f["notes"] += " При available=false показатели периода отсутствуют."
    return fields
