from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from app import db, rnp_analytics
from app.domain import MARKETPLACES, MOSCOW_TIMEZONE
from app.stores import STORES

MOSCOW = MOSCOW_TIMEZONE
MARKETPLACE_LABELS = {
    "WB": "Wildberries",
    "OZON": "Ozon",
    "YANDEX MARKET": "Яндекс Маркет",
}
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
FLOW_METRICS = (
    "orders_amount",
    "orders_count",
    "sales_amount",
    "sales_count",
    "cancellations_amount",
    "cancellations_count",
    "gross_profit",
)
CAMPAIGN_PREFIXES = (
    "unified",
    "manual_search",
    "manual_recommendations",
    "cpc_search",
)
CAMPAIGN_RAW_SUFFIXES = ("impressions", "clicks", "spend", "orders", "carts")
CAMPAIGN_RAW_METRICS = tuple(
    f"{prefix}_{suffix}" for prefix in CAMPAIGN_PREFIXES for suffix in CAMPAIGN_RAW_SUFFIXES
)
API_SUM_METRICS = (
    "traffic_clicks",
    "traffic_organic",
    "traffic_carts",
    "traffic_orders",
    "buyout_count",
    "return_count",
    "return_amount",
    "ad_spend",
    "ad_media",
    "ad_internal",
    "ad_external",
    "ad_impressions",
    "ad_clicks",
    "ad_carts",
    "ad_orders",
    "ad_sales_amount",
    "self_purchase_count",
    "self_purchase_amount",
    *CAMPAIGN_RAW_METRICS,
)
SNAPSHOT_METRICS = (
    "price_before_spp",
    "price_after_spp",
    "spp_percent",
    "stock_units",
    "stock_value",
    "stock_total",
    "stock_velocity_7d",
    "stock_turnover_days",
    "stock_depletion_date",
    "stock_to_client",
    "stock_from_client",
    "stock_regions",
    "rating",
    "reviews_count",
    "reviews_delta",
    "reviews_1",
    "reviews_2",
    "plan_orders_amount",
    "plan_orders_count",
    "plan_buyouts_amount",
    "plan_buyouts_count",
    "plan_ad_budget",
    "plan_drr",
    "plan_margin",
    "plan_roi",
    "plan_profit",
)
STORE_SUM_SNAPSHOT_METRICS = (
    "stock_units",
    "stock_value",
    "stock_total",
    "stock_to_client",
    "stock_from_client",
    "reviews_count",
    "reviews_delta",
    "reviews_1",
    "reviews_2",
    "plan_orders_amount",
    "plan_orders_count",
    "plan_buyouts_amount",
    "plan_buyouts_count",
    "plan_ad_budget",
    "plan_profit",
)
STORE_AVERAGE_SNAPSHOT_METRICS = (
    "price_before_spp",
    "price_after_spp",
    "spp_percent",
    "stock_velocity_7d",
    "stock_turnover_days",
    "rating",
    "plan_drr",
    "plan_margin",
    "plan_roi",
)
INTEGER_METRICS = {
    "orders_count",
    "sales_count",
    "cancellations_count",
    "traffic_clicks",
    "traffic_organic",
    "traffic_carts",
    "traffic_orders",
    "buyout_count",
    "return_count",
    "ad_impressions",
    "ad_clicks",
    "ad_carts",
    "ad_orders",
    "self_purchase_count",
    "stock_units",
    "stock_total",
    "stock_to_client",
    "stock_from_client",
    "reviews_count",
    "reviews_delta",
    "reviews_1",
    "reviews_2",
    "plan_orders_count",
    "plan_buyouts_count",
    *(
        f"{prefix}_{suffix}"
        for prefix in CAMPAIGN_PREFIXES
        for suffix in ("impressions", "clicks", "orders", "carts")
    ),
}
PROJECTABLE_METRICS = (*FLOW_METRICS, *API_SUM_METRICS)
RNP_WB_SALES_MAX_LOOKBACK_DAYS = 90
RNP_OTHER_SALES_MAX_LOOKBACK_DAYS = 35
RNP_DEFAULT_SALES_LOOKBACK_DAYS = 8
RNP_SALES_LOOKBACK_BUFFER_DAYS = 2


def _campaign_metric(metric_id: str, group: str, label: str) -> dict:
    return {
        "id": f"{metric_id}_impressions",
        "group": group,
        "label": label,
        "format": "integer",
        "hint": "Разбивка доступна для WB, когда рекламный API возвращает тип ставки и место показа",
        "children": (
            {"id": f"{metric_id}_impressions", "label": "Показы", "format": "integer"},
            {"id": f"{metric_id}_clicks", "label": "Клики", "format": "integer"},
            {"id": f"{metric_id}_ctr", "label": "CTR", "format": "percent"},
            {"id": f"{metric_id}_cpc", "label": "CPC", "format": "money"},
            {"id": f"{metric_id}_cpm", "label": "CPM", "format": "money"},
            {"id": f"{metric_id}_spend", "label": "Расход", "format": "money"},
            {"id": f"{metric_id}_orders", "label": "Заказы", "format": "integer"},
            {"id": f"{metric_id}_cr_order", "label": "CR в заказ", "format": "percent"},
            {"id": f"{metric_id}_carts", "label": "Корзины", "format": "integer"},
            {"id": f"{metric_id}_cr_cart", "label": "CR в корзину", "format": "percent"},
        ),
    }


METRICS = (
    {
        "id": "orders_amount",
        "group": "ЗАКАЗЫ",
        "label": "Заказы ₽",
        "format": "money",
        "children": (
            {"id": "orders_count", "label": "Заказы", "format": "integer"},
            {"id": "cancellations_count", "label": "Отмены заказов", "format": "integer"},
        ),
    },
    {
        "id": "sales_amount",
        "group": "ПРОДАЖИ",
        "label": "Продажи ₽",
        "format": "money",
        "children": (
            {"id": "sales_count", "label": "Продажи", "format": "integer"},
            {
                "id": "return_count",
                "label": "Возвраты",
                "format": "integer",
                "hint": "По данным воронки или отчёта возвратов маркетплейса",
            },
            {
                "id": "return_amount",
                "label": "Возвраты ₽",
                "format": "money",
                "hint": "Показывается, когда площадка отдаёт сумму возврата через API",
            },
        ),
    },
    {
        "id": "traffic_clicks",
        "group": "ТРАФИК",
        "label": "Клики",
        "format": "integer",
        "hint": "Переходы в карточку товара из API аналитики площадки",
        "children": (
            {"id": "traffic_clicks", "label": "Клики", "format": "integer"},
            {"id": "traffic_organic", "label": "Органика", "format": "integer"},
            {"id": "traffic_carts", "label": "Корзины", "format": "integer"},
            {"id": "traffic_orders", "label": "Заказы", "format": "integer"},
            {"id": "traffic_cr_cart", "label": "CR в корзину", "format": "percent"},
            {"id": "traffic_cr_order", "label": "CR в заказ", "format": "percent"},
            {"id": "traffic_cr_total", "label": "CR общий", "format": "percent"},
            {"id": "buyout_percent", "label": "% выкупа", "format": "percent"},
        ),
    },
    {
        "id": "ad_spend",
        "group": "РЕКЛАМА",
        "label": "Реклама",
        "format": "money",
        "hint": "Расходы рекламных кампаний из рекламного API площадки",
        "children": (
            {"id": "ad_drr_orders", "label": "ДРР", "format": "percent"},
            {"id": "ad_drr_sales", "label": "ДРР (продажи)", "format": "percent"},
            {"id": "ad_media", "label": "Медиа", "format": "money"},
            {"id": "ad_internal", "label": "Внутр. реклама", "format": "money"},
            {"id": "ad_external", "label": "Внеш. реклама", "format": "money"},
            {"id": "ad_impressions", "label": "Показы (РК)", "format": "integer"},
            {"id": "ad_clicks", "label": "Клики (РК)", "format": "integer"},
            {"id": "ad_carts", "label": "Корзины (РК)", "format": "integer"},
            {"id": "ad_orders", "label": "Заказы (РК)", "format": "integer"},
            {"id": "ad_ctr", "label": "CTR", "format": "percent"},
            {"id": "ad_cpc", "label": "CPC", "format": "money"},
            {"id": "ad_cpm", "label": "CPM", "format": "money"},
            {
                "id": "self_purchase_count",
                "label": "Самовыкупы",
                "format": "integer",
                "hint": "Поле сохранено; прямого достоверного показателя в подключённом API пока нет",
            },
            {
                "id": "self_purchase_amount",
                "label": "Самовыкупы ₽",
                "format": "money",
                "hint": "Поле сохранено; прямого достоверного показателя в подключённом API пока нет",
            },
        ),
    },
    _campaign_metric("unified", "ЕДИНАЯ СТАВКА · ПОИСК И ПОЛКИ", "Показы"),
    _campaign_metric("manual_search", "РУЧНАЯ СТАВКА · ПОИСК", "Показы"),
    _campaign_metric("manual_recommendations", "РУЧНАЯ СТАВКА · ПОЛКИ (РЕКОМЕНДАЦИИ)", "Показы"),
    _campaign_metric("cpc_search", "ОПЛАТА ЗА КЛИК · ПОИСК", "Показы"),
    {
        "id": "price_before_spp",
        "group": "ЦЕНЫ",
        "label": "Цена до СПП",
        "format": "money",
        "children": (
            {"id": "price_after_spp", "label": "Цена с СПП", "format": "money"},
            {"id": "spp_percent", "label": "СПП %", "format": "percent"},
        ),
    },
    {
        "id": "stock_units",
        "group": "ОСТАТКИ",
        "label": "Остаток шт",
        "format": "integer",
        "children": (
            {"id": "stock_units", "label": "Остаток шт", "format": "integer"},
            {"id": "stock_value", "label": "Остаток ₽", "format": "money"},
            {"id": "stock_total", "label": "Остаток итого", "format": "integer"},
            {"id": "stock_velocity_7d", "label": "Скорость 7Д", "format": "decimal"},
            {"id": "stock_turnover_days", "label": "Оборачиваемость", "format": "decimal"},
            {"id": "stock_depletion_date", "label": "Дата исчерпания", "format": "date"},
            {
                "id": "stock_to_client",
                "label": "В пути к клиенту",
                "format": "integer",
                "hint": "Поле сохранено; точное дневное значение доступно не во всех API",
            },
            {
                "id": "stock_from_client",
                "label": "В пути от клиента",
                "format": "integer",
                "hint": "Поле сохранено; точное дневное значение доступно не во всех API",
            },
            {"id": "stock_regions", "label": "Остатки по регионам", "format": "regions"},
        ),
    },
    {
        "id": "rating",
        "group": "РЕПУТАЦИЯ",
        "label": "Рейтинг",
        "format": "decimal",
        "children": (
            {"id": "reviews_count", "label": "Отзывы", "format": "integer"},
            {"id": "reviews_delta", "label": "Динамика отзывов", "format": "signed"},
            {
                "id": "reviews_1",
                "label": "1 звезда",
                "format": "integer",
                "hint": "Поле сохранено; официальный Seller API не отдаёт эту разбивку",
            },
            {
                "id": "reviews_2",
                "label": "2 звезды",
                "format": "integer",
                "hint": "Поле сохранено; официальный Seller API не отдаёт эту разбивку",
            },
        ),
    },
    {
        "id": "profit_after_ads",
        "group": "ФИНАНСЫ",
        "label": "Прибыль после ДРР",
        "format": "money",
        "hint": "Расчёт использует продажи, себестоимость и рекламные расходы, которые уже есть в базе",
        "children": (
            {"id": "margin_before_ads", "label": "Маржа до ДРР", "format": "percent"},
            {"id": "margin_after_ads", "label": "Маржа после ДРР", "format": "percent"},
            {"id": "roi", "label": "ROI", "format": "percent"},
            {"id": "profit_per_unit_before_ads", "label": "Прибыль/ед. до рекламы", "format": "money"},
            {"id": "profit_before_ads", "label": "Прибыль до ДРР", "format": "money"},
            {"id": "profit_after_ads", "label": "Прибыль после ДРР", "format": "money"},
        ),
    },
    {
        "id": "plan_orders_amount",
        "group": "ПЛАН",
        "label": "План заказы ₽",
        "format": "money",
        "hint": "Поля плана сохраняются по дням; до подключения источника отображается прочерк",
        "children": (
            {"id": "plan_completion", "label": "% выполнения", "format": "percent"},
            {"id": "plan_orders_count", "label": "План заказы", "format": "integer"},
            {"id": "plan_buyouts_amount", "label": "План выкупы ₽", "format": "money"},
            {"id": "plan_buyouts_count", "label": "План выкупы", "format": "integer"},
            {"id": "plan_ad_budget", "label": "План бюджет РК", "format": "money"},
            {"id": "plan_drr", "label": "План ДРР", "format": "percent"},
            {"id": "plan_margin", "label": "План маржа", "format": "percent"},
            {"id": "plan_roi", "label": "План ROI", "format": "percent"},
            {"id": "plan_profit", "label": "План прибыль", "format": "money"},
        ),
    },
)


def _number(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_month(value: str) -> tuple[date, date]:
    try:
        start = date.fromisoformat(f"{value}-01")
    except (TypeError, ValueError) as exc:
        raise ValueError("Укажите месяц в формате ГГГГ-ММ") from exc
    current = datetime.now(MOSCOW).date().replace(day=1)
    if start > current:
        raise ValueError("Нельзя выбрать будущий месяц")
    if start.year < current.year - 3:
        raise ValueError("Для РНП доступен период за последние три года")
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, end


def _normalize_marketplace(value: str) -> str:
    marketplace = str(value or "WB").upper()
    if marketplace not in MARKETPLACES:
        raise ValueError("Неизвестный маркетплейс")
    return marketplace


def sales_lookback_days(month: str, marketplace: str, today: date | None = None) -> int:
    maximum = (
        RNP_WB_SALES_MAX_LOOKBACK_DAYS if marketplace.upper() == "WB" else RNP_OTHER_SALES_MAX_LOOKBACK_DAYS
    )
    fallback = min(RNP_DEFAULT_SALES_LOOKBACK_DAYS, maximum)
    try:
        selected_month = date.fromisoformat(f"{month}-01")
    except ValueError:
        return fallback
    current_day = today or datetime.now(MOSCOW).date()
    return min(
        maximum,
        max(
            RNP_SALES_LOOKBACK_BUFFER_DAYS,
            (current_day - selected_month).days + RNP_SALES_LOOKBACK_BUFFER_DAYS,
        ),
    )


def _empty_day() -> dict:
    item = {
        "orders_amount": 0.0,
        "orders_count": 0,
        "sales_amount": 0.0,
        "sales_count": 0,
        "cancellations_amount": 0.0,
        "cancellations_count": 0,
        "gross_profit": None,
        "costed_sales_count": 0,
        "average_order": None,
    }
    item.update({metric: None for metric in API_SUM_METRICS})
    item.update({metric: None for metric in SNAPSHOT_METRICS})
    item.update(
        {
            "traffic_cr_cart": None,
            "traffic_cr_order": None,
            "traffic_cr_total": None,
            "buyout_percent": None,
            "ad_drr_orders": None,
            "ad_drr_sales": None,
            "ad_ctr": None,
            "ad_cpc": None,
            "ad_cpm": None,
            "margin_before_ads": None,
            "margin_after_ads": None,
            "roi": None,
            "profit_per_unit_before_ads": None,
            "profit_before_ads": None,
            "profit_after_ads": None,
            "plan_completion": None,
        }
    )
    for prefix in CAMPAIGN_PREFIXES:
        item.update(
            {
                f"{prefix}_ctr": None,
                f"{prefix}_cpc": None,
                f"{prefix}_cpm": None,
                f"{prefix}_cr_order": None,
                f"{prefix}_cr_cart": None,
            }
        )
    return item


def _ratio(numerator, denominator, multiplier: float = 100.0):
    if numerator is None or denominator in (None, 0):
        return None
    return round(_number(numerator) / _number(denominator) * multiplier, 2)


def _derive_metrics(item: dict) -> dict:
    if item.get("traffic_orders") is None:
        item["traffic_orders"] = _integer(item.get("orders_count"))
    if item.get("buyout_count") is None and item.get("sales_count") is not None:
        item["buyout_count"] = _integer(item.get("sales_count"))
    if item.get("buyout_percent") is None:
        item["buyout_percent"] = _ratio(item.get("buyout_count"), item.get("traffic_orders"))
    if item.get("traffic_clicks") is not None and item.get("ad_clicks") is not None:
        item["traffic_organic"] = max(
            _integer(item.get("traffic_clicks")) - _integer(item.get("ad_clicks")), 0
        )
    item["traffic_cr_cart"] = _ratio(item.get("traffic_carts"), item.get("traffic_clicks"))
    item["traffic_cr_order"] = _ratio(item.get("traffic_orders"), item.get("traffic_carts"))
    item["traffic_cr_total"] = _ratio(item.get("traffic_orders"), item.get("traffic_clicks"))
    item["ad_drr_orders"] = _ratio(item.get("ad_spend"), item.get("orders_amount"))
    item["ad_drr_sales"] = _ratio(item.get("ad_spend"), item.get("sales_amount"))
    item["ad_ctr"] = _ratio(item.get("ad_clicks"), item.get("ad_impressions"))
    item["ad_cpc"] = _ratio(item.get("ad_spend"), item.get("ad_clicks"), 1.0)
    item["ad_cpm"] = _ratio(item.get("ad_spend"), item.get("ad_impressions"), 1000.0)
    for prefix in CAMPAIGN_PREFIXES:
        item[f"{prefix}_ctr"] = _ratio(item.get(f"{prefix}_clicks"), item.get(f"{prefix}_impressions"))
        item[f"{prefix}_cpc"] = _ratio(item.get(f"{prefix}_spend"), item.get(f"{prefix}_clicks"), 1.0)
        item[f"{prefix}_cpm"] = _ratio(item.get(f"{prefix}_spend"), item.get(f"{prefix}_impressions"), 1000.0)
        item[f"{prefix}_cr_order"] = _ratio(item.get(f"{prefix}_orders"), item.get(f"{prefix}_clicks"))
        item[f"{prefix}_cr_cart"] = _ratio(item.get(f"{prefix}_carts"), item.get(f"{prefix}_clicks"))

    gross_profit = item.get("gross_profit")
    if gross_profit is not None:
        item["profit_before_ads"] = round(_number(gross_profit), 2)
        ad_spend = _number(item.get("ad_spend")) if item.get("ad_spend") is not None else 0.0
        item["profit_after_ads"] = round(_number(gross_profit) - ad_spend, 2)
        item["margin_before_ads"] = _ratio(gross_profit, item.get("sales_amount"))
        item["margin_after_ads"] = _ratio(item["profit_after_ads"], item.get("sales_amount"))
        item["profit_per_unit_before_ads"] = _ratio(gross_profit, item.get("sales_count"), 1.0)
        cost_of_sales = _number(item.get("sales_amount")) - _number(gross_profit)
        item["roi"] = _ratio(item["profit_after_ads"], cost_of_sales) if cost_of_sales > 0 else None
    item["plan_completion"] = _ratio(item.get("orders_amount"), item.get("plan_orders_amount"))
    return item


def _api_daily(rows: list[dict], article_key: bool = False) -> dict:
    result: dict = {}
    for source in rows:
        key = (
            (str(source.get("article") or ""), str(source.get("day") or ""))
            if article_key
            else str(source.get("day") or "")
        )
        target = result.setdefault(key, {})
        if article_key:
            for metric in (*API_SUM_METRICS, *SNAPSHOT_METRICS, "buyout_percent"):
                value = source.get(metric)
                if value is not None:
                    target[metric] = value
            continue
        for metric in (*API_SUM_METRICS, *STORE_SUM_SNAPSHOT_METRICS):
            value = source.get(metric)
            if value is None:
                continue
            if metric in INTEGER_METRICS:
                target[metric] = _integer(target.get(metric)) + _integer(value)
            else:
                target[metric] = _number(target.get(metric)) + _number(value)
        for metric in STORE_AVERAGE_SNAPSHOT_METRICS:
            value = source.get(metric)
            if value is None:
                continue
            target[f"__{metric}_sum"] = _number(target.get(f"__{metric}_sum")) + _number(value)
            target[f"__{metric}_count"] = _integer(target.get(f"__{metric}_count")) + 1
        raw_regions = source.get("stock_regions")
        if raw_regions:
            try:
                parsed = json.loads(str(raw_regions))
            except (TypeError, ValueError):
                parsed = {}
            if isinstance(parsed, dict):
                region_totals = target.setdefault("__stock_regions", {})
                for region, quantity in parsed.items():
                    region_totals[str(region)] = _integer(region_totals.get(str(region))) + _integer(quantity)
    if not article_key:
        for target in result.values():
            for metric in STORE_AVERAGE_SNAPSHOT_METRICS:
                count = _integer(target.pop(f"__{metric}_count", 0))
                total = _number(target.pop(f"__{metric}_sum", 0.0))
                if count:
                    target[metric] = round(total / count, 2)
            regions = target.pop("__stock_regions", None)
            if regions:
                target["stock_regions"] = json.dumps(regions, ensure_ascii=False, sort_keys=True)
    return result


def _merge_day(base: dict, api: dict | None = None) -> dict:
    item = dict(base)
    for metric, value in (api or {}).items():
        if value is not None:
            item[metric] = value
    return _derive_metrics(item)


def _normalize_daily(rows: list[dict], article_key: bool = False) -> dict:
    result: dict = {}
    for source in rows:
        key = (
            (str(source.get("article") or ""), str(source.get("day") or ""))
            if article_key
            else str(source.get("day") or "")
        )
        item = _empty_day()
        for metric in FLOW_METRICS:
            raw = source.get(metric)
            if metric == "gross_profit":
                item[metric] = round(_number(raw), 2) if raw is not None else None
            elif metric.endswith("_count"):
                item[metric] = _integer(raw)
            else:
                item[metric] = round(_number(raw), 2)
        item["costed_sales_count"] = _integer(source.get("costed_sales_count"))
        if source.get("return_count") is not None:
            item["return_count"] = _integer(source.get("return_count"))
        if source.get("return_amount") is not None:
            item["return_amount"] = round(_number(source.get("return_amount")), 2)
        if item["orders_count"]:
            item["average_order"] = round(item["orders_amount"] / item["orders_count"], 2)
        result[key] = item
    return result


def _summary(
    daily: dict[str, dict], days_in_month: int, elapsed_days: int, current_stock: int | None = None
) -> tuple[dict, dict]:
    fact = _empty_day()
    has_profit = False
    for day_key in sorted(daily):
        item = daily[day_key]
        for metric in FLOW_METRICS:
            value = item.get(metric)
            if metric == "gross_profit":
                if value is not None:
                    fact[metric] = _number(fact.get(metric)) + _number(value)
                    has_profit = True
            elif metric in INTEGER_METRICS:
                fact[metric] += _integer(value)
            else:
                fact[metric] += _number(value)
        for metric in API_SUM_METRICS:
            value = item.get(metric)
            if value is None:
                continue
            if fact.get(metric) is None:
                fact[metric] = 0 if metric in INTEGER_METRICS else 0.0
            if metric in INTEGER_METRICS:
                fact[metric] += _integer(value)
            else:
                fact[metric] += _number(value)
        for metric in SNAPSHOT_METRICS:
            value = item.get(metric)
            if value is not None:
                fact[metric] = value
        fact["costed_sales_count"] += _integer(item.get("costed_sales_count"))

    for metric in ("orders_amount", "sales_amount", "cancellations_amount"):
        fact[metric] = round(_number(fact[metric]), 2)
    fact["gross_profit"] = round(_number(fact["gross_profit"]), 2) if has_profit else None
    fact["average_order"] = (
        round(fact["orders_amount"] / fact["orders_count"], 2) if fact["orders_count"] else None
    )
    if fact.get("stock_units") is None:
        fact["stock_units"] = current_stock
    if fact.get("stock_total") is None:
        fact["stock_total"] = current_stock
    _derive_metrics(fact)

    ratio = days_in_month / max(elapsed_days, 1)
    forecast = dict(fact)
    if elapsed_days < days_in_month:
        for metric in PROJECTABLE_METRICS:
            value = fact.get(metric)
            if value is None:
                continue
            projected = _number(value) * ratio
            forecast[metric] = int(round(projected)) if metric in INTEGER_METRICS else round(projected, 2)
        forecast["average_order"] = fact["average_order"]
        _derive_metrics(forecast)
    return fact, forecast


def _sync_state(store_slug: str, marketplace: str) -> dict:
    states = db.get_sales_sync_states(marketplace, store_slug)
    state = states[0] if states else None
    if not state:
        return {
            "status": "waiting",
            "label": "Продажи ещё не синхронизировались",
            "last_success_at": None,
            "error": None,
        }
    if state.get("ok"):
        return {
            "status": "ready",
            "label": "Факты продаж обновлены",
            "last_success_at": state.get("last_success_at"),
            "error": None,
        }

    raw_error = str(state.get("error") or "")
    if marketplace == "YANDEX MARKET" and "403" in raw_error:
        error = (
            "Ключ Яндекс Маркета не имеет нужных прав. Добавьте «Просмотр информации "
            "о заказах» и finance-and-accounting (либо all-methods:read-only) — после "
            "этого заказы и аналитика подтянутся автоматически."
        )
    else:
        error = raw_error[:260] or "Последнее обновление завершилось с ошибкой"
    return {
        "status": "warning",
        "label": "Есть проблема с обновлением фактов",
        "last_success_at": state.get("last_success_at"),
        "error": error,
    }


def _metric_sync_state(store_slug: str, marketplace: str) -> list[dict]:
    rows = {str(row["source"]): row for row in rnp_analytics.get_states(store_slug, marketplace)}
    expected = ("snapshot", "funnel", "advertising") if marketplace == "WB" else ("snapshot", "funnel")
    labels = {
        "snapshot": "Ежедневный снимок",
        "funnel": "Воронка",
        "advertising": "Реклама",
    }
    result = []
    for source in expected:
        row = rows.get(source)
        if not row:
            result.append(
                {
                    "source": source,
                    "label": labels[source],
                    "status": "waiting",
                    "message": "Ещё не загружено из API",
                    "last_success_at": None,
                }
            )
            continue
        status = str(row.get("status") or "waiting")
        message = str(row.get("error") or "")
        if marketplace == "OZON" and source == "funnel" and status == "success":
            status = "partial"
            message = (
                "Seller API Ozon отдаёт заказы по товару, но метрики кликов и корзин "
                "в этом методе больше недоступны; недостоверные значения не показываем"
            )
        if marketplace == "YANDEX MARKET" and status == "error" and "403" in message:
            message = "Для аналитики добавьте ключу право finance-and-accounting или all-methods:read-only"
        result.append(
            {
                "source": source,
                "label": labels[source],
                "status": status,
                "message": message,
                "last_success_at": row.get("last_success_at"),
                "rows": _integer(row.get("rows_received")),
            }
        )
    if marketplace != "WB":
        result.append(
            {
                "source": "advertising",
                "label": "Реклама",
                "status": "unavailable",
                "message": ("Нужен отдельный рекламный API-токен площадки; Seller API его не заменяет"),
                "last_success_at": None,
            }
        )
    return result


def dashboard(
    month: str, marketplace: str, store_slug: str, search: str = "", limit: int = 25, offset: int = 0
) -> dict:
    marketplace = _normalize_marketplace(marketplace)
    if store_slug not in STORES:
        raise ValueError("Неизвестный магазин")
    start, end = _parse_month(month)
    limit = min(max(_integer(limit, 25), 1), 50)
    offset = max(_integer(offset), 0)
    today = datetime.now(MOSCOW).date()
    days_in_month = (end - start).days
    elapsed_days = days_in_month if end <= today else min(today.day, days_in_month)

    catalog = db.get_rnp_catalog_page(
        store_slug,
        marketplace,
        start.isoformat(),
        end.isoformat(),
        search=search,
        limit=limit,
        offset=offset,
    )
    articles = [str(item["article"]) for item in catalog["items"]]
    product_daily = _normalize_daily(
        db.get_rnp_product_daily(
            store_slug,
            marketplace,
            start.isoformat(),
            end.isoformat(),
            articles,
        ),
        article_key=True,
    )
    store_daily = _normalize_daily(
        db.get_rnp_daily_totals(store_slug, marketplace, start.isoformat(), end.isoformat())
    )
    product_api_daily = _api_daily(
        rnp_analytics.get_daily(
            store_slug,
            marketplace,
            start.isoformat(),
            end.isoformat(),
            articles,
        ),
        article_key=True,
    )
    store_api_daily = _api_daily(
        rnp_analytics.get_daily(
            store_slug,
            marketplace,
            start.isoformat(),
            end.isoformat(),
            None,
        )
    )
    strategies = db.get_rnp_strategies(store_slug, marketplace, articles)
    logs = db.get_rnp_action_logs(
        store_slug,
        marketplace,
        start.isoformat(),
        end.isoformat(),
        articles,
    )
    logs_by_article: dict[str, dict[str, list[dict]]] = {}
    for row in logs:
        by_day = logs_by_article.setdefault(str(row["article"]), {})
        by_day.setdefault(str(row["action_date"]), []).append(row)

    day_items = []
    cursor = start
    while cursor < end:
        day_items.append(
            {
                "date": cursor.isoformat(),
                "day": cursor.day,
                "weekday": WEEKDAYS[cursor.weekday()],
                "weekend": cursor.weekday() >= 5,
                "today": cursor == today,
                "future": cursor > today,
            }
        )
        cursor += timedelta(days=1)

    products = []
    for row in catalog["items"]:
        article = str(row["article"])
        daily = {
            day["date"]: _merge_day(
                product_daily.get((article, day["date"]), _empty_day()),
                product_api_daily.get((article, day["date"])),
            )
            for day in day_items
        }
        current_stock = _integer(row.get("current_stock"))
        buyer_price = row.get("buyer_price")
        discounted_price = row.get("discounted_price")
        current_price = buyer_price if buyer_price is not None else discounted_price
        if start <= today < end:
            today_item = daily[today.isoformat()]
            if today_item.get("stock_units") is None:
                today_item["stock_units"] = current_stock
            if today_item.get("stock_total") is None:
                today_item["stock_total"] = current_stock
            if today_item.get("price_before_spp") is None and row.get("list_price") is not None:
                today_item["price_before_spp"] = round(_number(row.get("list_price")), 2)
            if today_item.get("price_after_spp") is None and current_price is not None:
                today_item["price_after_spp"] = round(_number(current_price), 2)
            if today_item.get("spp_percent") is None and row.get("spp_percent") is not None:
                today_item["spp_percent"] = round(_number(row.get("spp_percent")), 2)
            _derive_metrics(today_item)
        fact, forecast = _summary(daily, days_in_month, elapsed_days, current_stock)
        price_source = "Цена покупателя" if buyer_price is not None else "Цена продавца"
        if current_price is None:
            current_price = fact.get("price_after_spp")
            price_source = "Последняя цена из снимка РНП"
        if current_price is None:
            current_price = fact.get("average_order")
            price_source = "Средняя цена заказа за месяц"

        products.append(
            {
                "article": article,
                "barcode": row.get("barcode") or "",
                "name": row.get("name") or article,
                "mp_sku": row.get("mp_sku") or row.get("mp_product_id") or "",
                "image_url": row.get("image_url") or "",
                "current_stock": current_stock,
                "stock_updated_at": row.get("stock_updated_at"),
                "current_price": round(_number(current_price), 2) if current_price is not None else None,
                "price_source": price_source,
                "list_price": round(_number(row.get("list_price")), 2)
                if row.get("list_price") is not None
                else None,
                "spp_percent": round(_number(row.get("spp_percent")), 1)
                if row.get("spp_percent") is not None
                else None,
                "cost_configured": row.get("purchase_price") is not None,
                "strategy": strategies.get(article),
                "actions": logs_by_article.get(article, {}),
                "daily": daily,
                "fact": fact,
                "forecast": forecast,
            }
        )

    store_daily = {
        day["date"]: _merge_day(
            store_daily.get(day["date"], _empty_day()),
            store_api_daily.get(day["date"]),
        )
        for day in day_items
    }
    store_stock = db.get_rnp_stock_total(store_slug, marketplace)
    if start <= today < end:
        store_today = store_daily[today.isoformat()]
        if store_today.get("stock_units") is None:
            store_today["stock_units"] = store_stock
        if store_today.get("stock_total") is None:
            store_today["stock_total"] = store_stock
    store_fact, store_forecast = _summary(store_daily, days_in_month, elapsed_days, store_stock)

    return {
        "marketplace": marketplace,
        "marketplace_label": MARKETPLACE_LABELS[marketplace],
        "store": {"slug": store_slug, "name": STORES[store_slug]["name"]},
        "month": month,
        "period": {
            "from": start.isoformat(),
            "to": (end - timedelta(days=1)).isoformat(),
            "days": day_items,
            "elapsed_days": elapsed_days,
            "is_current": start <= today < end,
        },
        "metrics": list(METRICS),
        "totals": {
            "daily": {day["date"]: store_daily.get(day["date"], _empty_day()) for day in day_items},
            "fact": store_fact,
            "forecast": store_forecast,
            "current_stock": store_stock,
        },
        "products": products,
        "pagination": {
            "total": catalog["total"],
            "offset": offset,
            "limit": limit,
            "shown": len(products),
            "has_more": offset + len(products) < catalog["total"],
        },
        "sync": _sync_state(store_slug, marketplace),
        "metric_sync": _metric_sync_state(store_slug, marketplace),
        "today": today.isoformat(),
    }


def sync_metrics(
    month: str, marketplace: str, store_slug: str, force: bool = True, articles: list[str] | None = None
) -> dict:
    marketplace = _normalize_marketplace(marketplace)
    if store_slug not in STORES:
        raise ValueError("Неизвестный магазин")
    start, end = _parse_month(month)
    today = datetime.now(MOSCOW).date()
    date_to = min(end - timedelta(days=1), today)
    clean_articles = [str(value).strip() for value in (articles or []) if str(value).strip()]
    return rnp_analytics.sync_store(
        store_slug,
        marketplace,
        start,
        date_to,
        force,
        clean_articles if clean_articles else None,
    )
