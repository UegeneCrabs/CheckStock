"""Human-readable reference shared by OpenAPI and the employee API catalog."""

# Fields describe the main response fields, not an exhaustive schema for every view.
DOCS = {
    "stores": (
        "Магазины",
        "Доступные магазины и площадки.",
        "slug:Код магазина:str|name:Название:str|marketplaces:Доступные площадки:array",
    ),
    "capabilities": (
        "Доступные возможности",
        "Методы, фильтры и разрешённые области доступа.",
        "reports:Методы:array|reports[].scopes:Магазины и площадки:array|notes:Ограничения:array",
    ),
    "article-stores": (
        "Поиск магазина по артикулу",
        "Помогает определить магазин; при нескольких совпадениях нужно выбрать магазин.",
        "status:Результат поиска:str|matches:Совпадения:array|warnings:Предупреждения:array",
    ),
    "data-status": (
        "Состояние данных",
        "Время обновления и охват сохранённых источников. Не гарантирует полноту данных.",
        "sources:Источники:array|warnings:Ограничения:array",
    ),
    "products": (
        "Каталог товаров",
        "Артикулы, названия и штрихкоды товаров магазина.",
        "article:Артикул:str|name:Название:str|barcode:Штрихкод:str|mp_sku:Идентификатор площадки:str",
    ),
    "product-details": (
        "Карточка товара",
        "Один товар и его текущие остатки. Требуется точный артикул.",
        "article:Артикул:str|name:Название:str|barcode:Штрихкод:str|context.stock:Остатки в контексте ответа:array",
    ),
    "product-newness": (
        "Новинки",
        "Признак новинки и наблюдаемый возраст продаж; это не гарантированный возраст карточки.",
        "article:Артикул:str|is_new:Новинка:bool|sales_days:Календарные дни от первой загруженной продажи:number|null|age_known:Возраст известен:bool",
    ),
    "product-reputation": (
        "Рейтинг и отзывы",
        "Сохранённый рейтинг товара и число отзывов.",
        "article:Артикул:str|rating:Рейтинг:number|null|reviews_count:Количество отзывов:number|null",
    ),
    "product-tags": (
        "ТЕГи и цели",
        "Текущие цели и метки сайта. Историческая привязка целей не подтверждается.",
        "article:Артикул:str|goal_week:Цель на неделю:number|null|goal_day:Цель на день:number|null|code:Код товара:str|null|status:Статус стока:str|null|ends:Когда закончится сток, исходный текст:str|null|fact:Факт прошлой недели:number|null|plan:План прошлой недели:number|null",
    ),
    "current-economics": (
        "Текущая экономика",
        "Расчёт на единицу товара из таблицы сайта, не прибыль за период.",
        "article:Артикул:str|margin_per_unit_rub:Маржа на единицу, ₽:number|null|roi_percent:ROI, %:number|null|spp_percent:СПП, %:number|null|as_of_date:Дата расчёта:date|null|has_source_errors:Ошибки источников:bool",
    ),
    "profit-calculator": (
        "Калькулятор прибыли",
        "Сохранённые параметры и расчёт калькулятора конкретного товара.",
        "article:Артикул:str|inputs:Параметры калькулятора:object|results:Результаты расчёта:object|labels:Подписи полей:object",
    ),
    "advertising": (
        "Реклама",
        "Расходы, показы и клики. group_by=day даёт разбивку по дням. Показы только рекламные.",
        "article:Артикул при группировке по товару:str|day:Дата при группировке по дням:date|spend:Расход, ₽:number|null|impressions:Показы:number|null|clicks:Клики:number|null|ctr_percent:CTR, %:number|null|cpc_rub:Цена клика, ₽:number|null",
    ),
    "advertising-campaigns": (
        "Рекламные кампании",
        "Товар в кампании и показатели рекламы. Статус кампании на момент обновления.",
        "article:Артикул:str|spend:Расход, ₽:number|null|impressions:Показы:number|null|clicks:Клики:number|null|ctr_percent:CTR, %:number|null|cpc_rub:Цена клика, ₽:number|null",
    ),
    "stocks": (
        "Текущие остатки",
        "Детальные остатки; view=summary возвращает сводку сайта. Общий остаток включает транзит.",
        "article:Артикул:str|scheme:Схема в details:str|quantity:Количество в details:number|null|warehouse_breakdown:Разбивка по складам в details:array|total:Общий остаток в summary:number|null|ff_available:Доступно на ФФ в summary:number|null|transit:В пути в summary:number|null",
    ),
    "stock-value": (
        "Стоимость остатков",
        "Оценка текущих остатков по известной закупочной цене.",
        "article:Артикул:str|quantity:Количество:number|null|purchase_price:Закупочная цена, ₽:number|null|stock_value_rub:Стоимость остатка, ₽:number|null",
    ),
    "stock-operations": (
        "Складские операции",
        "Движения склада за период без имён сотрудников и закупочных цен.",
        "id:Идентификатор операции:number|kind:Тип операции:str|created_at:Дата и время:datetime|article:Артикул:str|quantity:Количество:number",
    ),
    "supplies": (
        "План поставок",
        "Планируемые поставки WB и разрешённые ручные планы. Формат строк зависит от источника.",
        "supply_id:Номер поставки WB:str|supply_date:Дата поставки WB:date|warehouse_name:Склад WB:str|status:Статус WB:str|delivery_at:Дата ручного плана:datetime",
    ),
    "prices": (
        "Цены",
        "Последние цены либо сохранённые дневные снимки при указании периода.",
        "article:Артикул:str|day:Дата:date|seller_base_price:Базовая цена, ₽:number|null|retail_price:Розничная цена, ₽:number|null|customer_price_with_spp:Цена с СПП, ₽:number|null|customer_price_with_wallet:Цена с кошельком, ₽:number|null",
    ),
    "costs": (
        "Себестоимость и затраты",
        "Текущие справочные данные; не подтверждают закупочную цену прошлых дат.",
        "article:Артикул:str|manager:Менеджер:str|null|purchase_price:Закупочная цена, ₽:number|null|fulfillment_cost:Затраты ФФ, ₽:number|null|team_commission_percent:Комиссия компании, %:number|null|abc_code:ABC-класс:str|null",
    ),
    "profit": (
        "Юниточная прибыль",
        "Заказы, отмены, реклама и расчётная маржа за период. Оборот заказов не равен выручке от выкупов.",
        "article:Артикул:str|orders_count:Количество заказов:number|null|orders_amount:ТО заказов, ₽:number|null|cancel_amount:Сумма отмен, ₽:number|null|net_orders_amount:ТО после отмен, ₽:number|null|advertising_spend:Расходы рекламы, ₽:number|null|margin:Маржа при полном расчёте, ₽:number|null|roi:ROI при полном расчёте, %:number|null|margin_complete:Полнота маржи:bool|report_margin:Маржа сайта, может быть неполной:number|null",
    ),
    "target-prices": (
        "Целевые цены",
        "Рекомендации цены по закрытой неделе, без изменения цен на площадке.",
        "article:Артикул:str|target_price:Рекомендованная цена, ₽:number|null|target_warnings:Ограничения расчёта:array",
    ),
    "loss-products": (
        "Убыточные товары",
        "Расчётные убытки за период. Неполные расчёты исключаются.",
        "article:Артикул:str|estimated_profit_rub:Расчётная прибыль, ₽:number|orders_count:Заказы:number|null|roi_percent:ROI, %:number|null|excluded_incomplete_products:Число исключённых товаров, в корне ответа:number",
    ),
    "profit-summary": (
        "Сводка прибыли",
        "Сводные суммы по магазинам и сравнение равных периодов. Каждая строка содержит текущий и предыдущий периоды.",
        "rows:Результаты магазинов:array|warnings:Ограничения:array",
    ),
    "product-analysis": (
        "Анализ товаров",
        "Проверки стока без заказов, падения оборота, ДРР/ROI и целей. Нужные права зависят от сценария.",
        "rows:Найденные товары и метрики сценария:array|warnings:Ограничения:array",
    ),
    "stock-history": (
        "История остатков",
        "Дневные снимки FBO, FBS и ФФ без транзита. Отсутствие снимка не означает ноль.",
        "article:Артикул:str|day:Дата снимка:date|scheme:Схема fbo/fbs/ff:str|warehouse:Склад ФФ:str|null|quantity:Остаток:number|null|updated_at:Время снимка:datetime",
    ),
    "orders": (
        "Отдельные заказы",
        "Строки заказов по дате создания в МСК. Статус последний сохранённый, а не история его изменений.",
        "external_order_id:Номер заказа:str|article:Артикул:str|ordered_at:Время заказа:datetime|status:Статус:str|quantity:Заказанное количество:number|order_amount:Сумма заказа, ₽:number|cancelled_quantity:Отменённое количество:number|sold_quantity:Проданное количество:number|return_quantity:Возвращённое количество:number",
    ),
    "inbound-supplies": (
        "Состав поставок FBO",
        "Товары в поставках, приёмка и расхождения. status фильтрует нормализованный этап. Проверяйте свежесть источника.",
        "supply_id:Номер поставки:str|article:Артикул:str|null|stage:Этап поставки:str|quantity:Заявлено:number|null|accepted_quantity:Принято:number|null|ready_quantity:Готово к продаже:number|null|shortage_quantity:Недостача:number|null|items_missing:Состав неизвестен, если true:bool",
    ),
    "supply-arrivals": (
        "Расписание поступлений",
        "Общий реестр магазина, не только WB. Нужен доступ ко всем площадкам. Даты фильтруют фактический приход.",
        "order:Номер заказа поставки:str|warehouse:Склад ФФ:str|status:Статус:str|arrival:Фактический приход:date|null|volume:Объём:number|null|weight:Вес:number|null|boxes:Коробки:number|null",
    ),
    "stock-cost-report": (
        "Движение и закупочные цены",
        "Отчёт склада. cost_view выбирает сводку или детали. Формульные продажи FBS отличаются от фактических.",
        "article:Артикул в деталях:str|quantity:Количество в деталях:number|purchase_price:Закупочная цена в деталях, ₽:number|null|purchase_cost:Стоимость в деталях, ₽:number|null|context.reconciliation:Доступность расчёта FBS:array",
    ),
    "economics-history": (
        "Дневная история экономики",
        "Последний 21 день графика товара. Текущий день неполный; произвольный период не поддерживается.",
        "article:Артикул:str|day:Дата:date|margin_rub:Маржа, ₽:number|null|margin_complete:Полнота расчёта:bool|turnover_rub:ТО заказов, ₽:number|null|advertising_rub:Расходы рекламы, ₽:number|null|stock_units:Остаток:number|null",
    ),
}


def documentation(name):
    from app.agents.catalog_fields import enrich

    title, description, fields = DOCS[name]
    # Split on | only when followed by a new field (nullable types contain |null).
    import re

    entries = re.split(r"\|(?=[\w.\[\]]+:)", fields)
    result = {
        "title": title,
        "description": description,
        "fields": enrich(
            name,
            [
                dict(zip(("name", "description", "type"), entry.split(":", 2), strict=True))
                for entry in entries
            ],
        ),
    }
    from app.agents.yandex_catalog import guide

    yandex = guide(name, result)
    if yandex:
        result["marketplace_guides"] = {"YANDEX MARKET": yandex}
    return result


async def employee_catalog(user):
    from app.access.access_control import scope_pairs
    from app.agents.catalog_fields import ENVELOPE
    from app.agents.yandex_reports import SUPPORTED
    from app.web.routers.agent_analytics import action_schema
    from app.web.routers.agent_full import SPECS, capabilities

    schema = await action_schema()
    available = await capabilities(user)
    scopes = {item["report"]: item["scopes"] for item in available["reports"]}
    common = [{"store": store, "marketplace": mp} for store, mp in scope_pairs(user)]
    result = []
    for path, methods in schema["paths"].items():
        name = path.rsplit("/", 1)[-1]
        allowed = scopes.get(
            name, common if name in {"stores", "capabilities", "article-stores", "data-status"} else []
        )
        if not allowed:
            continue
        operation = methods["get"]
        result.append(
            {
                "name": name,
                "path": path,
                "operation_id": operation["operationId"],
                **documentation(name),
                "marketplace_support": {
                    "WB": True,
                    "YANDEX MARKET": name in SUPPORTED
                    or (name in SPECS and not SPECS[name][1])
                    or name in {"stores", "capabilities", "article-stores", "data-status", "loss-products"},
                },
                "scopes": allowed,
                "parameters": operation.get("parameters", []),
                "response_schema": operation.get("responses", {}).get("200", {}),
                "envelope_fields": [
                    {"path": key, "description": label, "type": kind} for key, label, kind in ENVELOPE
                ]
                if name in SPECS
                else [],
            }
        )
    return {"methods": result}
