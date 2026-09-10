"""Read-only employee API for the site's implemented business sections."""

import json
import logging
import threading
import time
from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import agent_reports as reports
from app import db, decision_center, rnp, supply_planning
from app.access_control import ActionPermission, has_action_permission, scope_pairs
from app.domain import MARKETPLACES, MOSCOW_TIMEZONE
from app.dto.identity import SectionName
from app.section_access import has_access
from app.web.routers.agent_analytics import Employee
from app.web.routers.target_prices import target_price_data
from app.web.routers.unit_economics import _unit_economics_1c_unit_profit_report_data

router = APIRouter()
logger = logging.getLogger(__name__)
SUPPLY_CACHE = {}
SUPPLY_LOCK = threading.Lock()


class ReportQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    store: str | None = Field(default=None, min_length=1, max_length=100)
    marketplace: Literal["WB", "OZON", "YANDEX MARKET"] = "WB"
    date_from: date | None = None
    date_to: date | None = None
    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    article: str | None = Field(default=None, min_length=1, max_length=100)
    search: str | None = Field(default=None, min_length=1, max_length=100)
    warehouse: str | None = Field(default=None, min_length=1, max_length=150)
    scheme: str | None = Field(default=None, pattern=r"^(fbo|fbs|rfbs|ff|transit|fbs_[a-zA-Z0-9_-]+)$", max_length=100)
    view: Literal["details", "summary"] = "details"
    fulfillment: str | None = Field(default=None, min_length=1, max_length=150)
    manager: str | None = Field(default=None, min_length=1, max_length=150)
    kind: str | None = Field(default=None, min_length=1, max_length=50)
    group_by: Literal["article", "day"] = "article"
    sort_by: str | None = Field(default=None, max_length=60)
    order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)
    is_new: bool | None = None
    tag_column: Literal["goal_week", "goal_day", "status", "ends", "code", "fact", "plan"] | None = None
    tag_value: str | None = Field(default=None, min_length=1, max_length=100)
    tag_operator: Literal["eq", "gte", "lte"] = "eq"

    @field_validator("store")
    @classmethod
    def normalize_store(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else None


class ReportResponse(BaseModel):
    store: str | None
    marketplace: str
    currency: str = "RUB"
    timezone: str
    generated_at: str
    date_from: date | None = None
    date_to: date | None = None
    rows: list[dict]
    total_rows: int
    next_offset: int | None
    totals: dict
    sources: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)


class ArticleLookupQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    article: str = Field(min_length=1, max_length=100)
    marketplace: Literal["WB", "OZON", "YANDEX MARKET"] | None = None


class ArticleStoreMatch(BaseModel):
    store: str
    marketplace: str
    article: str


class ArticleLookupResponse(BaseModel):
    article: str
    status: Literal["found", "ambiguous", "not_found"]
    matches: list[ArticleStoreMatch]
    warnings: list[str]


S = SectionName
SPECS = {
    "product-newness": (S.UNIT_ECONOMICS_1C, True, "article search manager is_new", "Read table newness flag and observed sales_days. Filter is_new; sort sales_days. Unknown age is not proof of an old product."),
    "product-reputation": (S.UNIT_ECONOMICS_1C, True, "article search manager", "Read table product rating and reviews_count. Sort by rating or reviews_count. Missing values remain null."),
    "product-tags": (S.UNIT_ECONOMICS_1C, True, "article search manager tag_column tag_value tag_operator", "Read TAG: goal_week, goal_day, status, ends, code, fact, plan. Filter tag_column/tag_value; eq for all, gte/lte numeric only. Sort by any column. ends is week text, not a confirmed date."),
    "current-economics": (S.UNIT_ECONOMICS_1C, True, "article search manager", "Read table Current economics: margin per unit, ROI and SPP. Not sidebar calculator or period profit. Sort by margin_per_unit_rub, roi_percent, spp_percent."),
    "profit-calculator": (S.UNIT_ECONOMICS_1C, True, "article", "Read ALL saved-state net-profit calculator inputs, current unit profit, ROI and target price. Requires exact article. Not period profit or unsaved browser edits."),
    "products": (S.STOCK, False, "search article", "Search catalog by name, barcode or article. No costs."),
    "product-details": (S.STOCK, False, "article", "Read one catalog item and its warehouse stock."),
    "sales": (S.SALES, False, "date_from date_to article scheme group_by", "Order-cohort sales, cancellations and returns. Dates select order dates, not payout dates."),
    "funnel": (S.SALES, True, "date_from date_to article group_by", "WB daily orders, cancellations and observed buyouts. Missing buyouts remain unknown."),
    "advertising": (S.UNIT_ECONOMICS_1C, True, "date_from date_to article manager group_by", "WB advertising spend, impressions, clicks, CTR and CPC. No invented attribution."),
    "stocks": (S.STOCK, False, "article search warehouse scheme view fulfillment", "Stock: view=summary for table total, FF, transit, FBS/FBO/rFBS; fulfillment optional. Default details supports warehouse and scheme, including fbs_* variants."),
    "stock-value": (S.UNIT_ECONOMICS_1C, True, "article warehouse scheme manager", "Value observed current stock using known purchase costs. Disclose missing costs."),
    "stock-operations": (S.STOCK, False, "date_from date_to article kind", "Read warehouse operation items in scope, without employee identities or costs."),
    "supplies": (S.STOCK, True, "date_from date_to", "Read WB planned supplies with a five-minute cache and scoped manual plans. No writes."),
    "prices": (S.UNIT_ECONOMICS_1C, True, "date_from date_to article manager", "Read latest prices, or recorded price snapshots for explicit dates. Never changes prices."),
    "costs": (S.UNIT_ECONOMICS_1C, True, "article manager search", "Read current purchase costs, expenses, manager and ABC data. No historical backdating."),
    "profit": (S.UNIT_ECONOMICS_1C, True, "date_from date_to article manager", "Website estimated unit-profit report, including unknown and incomplete calculations."),
    "target-prices": (S.UNIT_ECONOMICS_1C, True, "article manager", "Read website target-price recommendations for its closed week. No price updates."),
    "rnp": (S.RNP, False, "month search", "Read monthly RNP product metrics. Missing metric history is explicitly unavailable."),
    "decisions": (S.DECISION_CENTER, True, "", "Read existing decision-engine suggestions. Heuristic estimates, not proven causal effects."),
}
PERIOD_REPORTS = {"sales", "funnel", "advertising", "stock-operations", "supplies", "profit"}
ECONOMIC = {name for name, spec in SPECS.items() if spec[0] == S.UNIT_ECONOMICS_1C}
COMMON = {"store", "marketplace", "limit", "offset", "sort_by", "order"}


def permitted(user, section, store, marketplace, operation=None):
    if not has_access(user, section) or (store, marketplace) not in scope_pairs(user):
        return False
    actions = {S.SALES: ActionPermission.SALES_VIEW, S.STOCK: ActionPermission.STOCK_BALANCE_VIEW,
               S.UNIT_ECONOMICS_1C: ActionPermission.UNIT_ECONOMICS_VIEW}
    action = operation or actions.get(section)
    return action is None or has_action_permission(user, action, store_slug=store, marketplace=marketplace)


def guard(user, section, query, operation=None):
    if not permitted(user, section, query.store, query.marketplace, operation):
        raise HTTPException(403, "Нет доступа к разделу, магазину или площадке")


def allowed_fields(name):
    if name == "profit-calculator":
        return {"store", "marketplace", "article"}
    common = COMMON - {"sort_by", "order"} if name == "rnp" else COMMON
    return common | set(SPECS[name][2].split())


def report_permitted(user, name, store, mp):
    section, wb_only, _, _ = SPECS[name]
    if (wb_only and mp != "WB") or not permitted(user, section, store, mp):
        return False
    if name == "stock-value" and not permitted(user, S.STOCK, store, mp):
        return False
    if name == "stock-operations" and not permitted(user, S.STOCK, store, mp, ActionPermission.STOCK_OPERATIONS_VIEW):
        return False
    if name in {"rnp", "decisions"} and not permitted(user, S.UNIT_ECONOMICS_1C, store, mp):
        return False
    if name in {"rnp", "decisions"} and user.role.value == "user":
        return False
    return True


def validate(name, query, supplied):
    section, wb_only, fields, _ = SPECS[name]
    if name == "stocks" and ((query.view == "summary" and (query.warehouse or query.scheme)) or (query.fulfillment and query.view != "summary")):
        raise HTTPException(422, "summary поддерживает fulfillment; warehouse и scheme используйте с view=details")
    if wb_only and query.marketplace != "WB":
        raise HTTPException(422, "Этот отчёт пока реализован только для WB")
    if set(supplied) - allowed_fields(name):
        raise HTTPException(422, "Фильтр не поддерживается этим отчётом; см. capabilities")
    if bool(query.date_from) != bool(query.date_to):
        raise HTTPException(422, "Укажите обе даты")
    if name in PERIOD_REPORTS and query.date_from is None:
        raise HTTPException(422, "Укажите date_from и date_to")
    if query.date_from:
        from datetime import datetime

        if query.date_to < query.date_from or (query.date_to - query.date_from).days >= 90:
            raise HTTPException(422, "Допустим период от 1 до 90 дней")
        if name != "supplies" and query.date_to > datetime.now(MOSCOW_TIMEZONE).date():
            raise HTTPException(422, "Будущие даты недоступны")
    if name in {"product-details", "profit-calculator"} and not query.article:
        raise HTTPException(422, "Укажите article")
    if name == "rnp" and not query.month:
        raise HTTPException(422, "Укажите month в формате YYYY-MM")
    if name == "rnp":
        date.fromisoformat(query.month + "-01")
        if query.limit > 20:
            raise HTTPException(422, "Для РНП limit не более 20")
    return section


@router.get("/article-stores", operation_id="findArticleStores", response_model=ArticleLookupResponse)
async def article_stores(user: Employee, query: Annotated[ArticleLookupQuery, Query()]):
    """Find accessible stores by exact article when store is omitted. One match: use its store and marketplace for the requested report. Multiple matches: ask which one. No matches: report unavailable local catalog data, never guess."""
    matches = []
    for store, mp in sorted(set(scope_pairs(user))):
        if query.marketplace is not None and query.marketplace != mp:
            continue
        catalog_access = any(permitted(user, section, store, mp) for section in (S.STOCK, S.SALES))
        economic_access = mp == "WB" and permitted(user, S.UNIT_ECONOMICS_1C, store, mp)
        if not catalog_access and not economic_access:
            continue
        if not catalog_access:
            allowed = await run_in_threadpool(reports.economic_filter, [{"article": query.article}], user, store)
            if not allowed:
                continue
        rows = await run_in_threadpool(reports.catalog, store, mp)
        if any(str(row["article"]) == query.article or
               (" / " not in query.article and str(row["article"]).partition(" / ")[0] == query.article)
               for row in rows):
            matches.append(ArticleStoreMatch(store=store, marketplace=mp, article=query.article))
    status = "found" if len(matches) == 1 else "ambiguous" if matches else "not_found"
    return ArticleLookupResponse(article=query.article, status=status, matches=matches, warnings=[
        "Поиск выполняется только по доступным локальным каталогам. Отсутствие совпадений не доказывает отсутствие товара.",
        "Доступ к показателям выбранного отчёта проверяется отдельно.",
    ])


@router.get("/capabilities", operation_id="getAnalyticsCapabilities")
async def capabilities(user: Employee):
    """Discover allowed reports, supported filters and marketplace scopes before requesting data."""
    from app.web.routers.agent_analytics import LossQuery, accessible_stores

    loss_scopes = [
        {"store": store, "marketplace": "WB"}
        for store in accessible_stores(user, "WB")
        if permitted(user, S.UNIT_ECONOMICS_1C, store, "WB")
    ]
    return {
        "read_only": True,
        "reports": [
            {"report": name, "path": "/api/agent/v1/" + name, "description": description,
             "filters": sorted(allowed_fields(name)),
             "scopes": [{"store": store, "marketplace": mp} for store, mp in scope_pairs(user)
                        if report_permitted(user, name, store, mp)]}
            for name, (section, wb_only, fields, description) in SPECS.items()
            if has_access(user, section)
        ] + ([{
            "report": "loss-products", "path": "/api/agent/v1/loss-products",
            "description": "Rank WB products by estimated period loss. Incomplete calculations are excluded; empty rows do not prove no losses.",
            "filters": sorted(LossQuery.model_fields), "scopes": loss_scopes,
        }] if loss_scopes else []),
        "unavailable": ["Снабжение как отдельный раздел", "Эфемериды",
                        "Расчёты юнит-экономики Ozon и Яндекс Маркета"],
        "notes": ["Права не означают наличие загруженных данных. Проверяйте data-status.",
                  "Объединяйте магазины только из разрешённых scopes, запросив каждый отдельно."],
    }


@router.get("/data-status", operation_id="getAnalyticsDataStatus")
async def data_status(request: Request, user: Employee, query: Annotated[ReportQuery, Query()]):
    if not query.store:
        raise HTTPException(422, "Укажите store для проверки источников")
    """Read scoped source counts and observed dates. These dates do not prove complete coverage."""
    if set(request.query_params) - {"store", "marketplace"}:
        raise HTTPException(422, "Поддерживаются только store и marketplace")
    if (query.store, query.marketplace) not in scope_pairs(user):
        raise HTTPException(403, "Нет доступа к магазину или площадке")
    sections = [s for s in (S.STOCK, S.SALES, S.UNIT_ECONOMICS_1C, S.RNP, S.DECISION_CENTER)
                if permitted(user, s, query.store, query.marketplace)]
    if not sections:
        raise HTTPException(403, "Нет доступа к данным")
    rows = []
    for section in sections:
        if section in (S.UNIT_ECONOMICS_1C, S.DECISION_CENTER) and query.marketplace != "WB":
            continue
        if section in {S.UNIT_ECONOMICS_1C, S.RNP, S.DECISION_CENTER} and user.role.value == "user":
            continue
        rows.extend(await run_in_threadpool(reports.status, query.store, query.marketplace, section.value))
    return {"sources": rows, "warnings": ["Число строк и крайние даты не гарантируют полноту периода.",
             "Для пользователя с ограничением по менеджеру общие счётчики экономики скрыты."]}


def planned(query):
    key = (query.store, query.date_from, query.date_to)
    with SUPPLY_LOCK:
        cached = SUPPLY_CACHE.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        data = supply_planning.load_wb_planned_supplies(
            (query.store,), date_from=query.date_from, date_to=query.date_to)
        if len(SUPPLY_CACHE) >= 100:
            SUPPLY_CACHE.clear()
        SUPPLY_CACHE[key] = (time.monotonic(), data)
        return data


async def execute(name, request, user, query):
    section = validate(name, query, request.query_params)
    if query.store is None:
        if not query.article:
            raise HTTPException(422, "Укажите store или точный article")
        lookup = await article_stores(user, ArticleLookupQuery(
            article=query.article,
            marketplace=query.marketplace if SPECS[name][1] or "marketplace" in request.query_params else None,
        ))
        if lookup.status != "found":
            return reports.envelope(query, {"rows": [], "total_rows": 0, "next_offset": None, "totals": {}},
                lookup.warnings + ["Уточните магазин из context.store_resolution.matches." if lookup.matches else
                                   "Артикул не найден в доступных локальных каталогах."], [],
                ) | {"context": {"store_resolution": lookup.model_dump()}}
        match = lookup.matches[0]
        query = query.model_copy(update={"store": match.store, "marketplace": match.marketplace})
    guard(user, section, query)
    if name in {"stock-value", "product-details"}:
        guard(user, S.STOCK, query)
    if name == "stock-operations":
        guard(user, S.STOCK, query, ActionPermission.STOCK_OPERATIONS_VIEW)
    if name in {"rnp", "decisions"}:
        guard(user, S.UNIT_ECONOMICS_1C, query)
        if user.role.value == "user":
            raise HTTPException(403, "Сводные экспериментальные отчёты недоступны с ограничением по менеджеру")
    warnings = ["Отсутствующие значения не являются нулями. Время ответа не является временем загрузки источников."]
    context = {}
    metrics = ()
    rows = []
    if name in {"current-economics", "product-tags", "product-newness", "product-reputation"}:
        from app.web.routers.unit_economics import sales_unit_economics_1c

        params = {"data": "1", "store": query.store}
        if query.article:
            allowed = await run_in_threadpool(reports.economic_filter, [{"article": query.article}], user, query.store)
            if not allowed:
                raise HTTPException(404, "Товар не найден или недоступен")
            params["article"] = query.article
        scoped = Request({**request.scope, "query_string": urlencode(params).encode()})
        response = await sales_unit_economics_1c(scoped)
        if response.status_code != 200:
            raise HTTPException(response.status_code, "Источник текущей экономики недоступен")
        data = json.loads(response.body)
        products = [data["product"]] if "product" in data else data.get("products", [])
        products = [p for p in products if p.get("store_slug") == query.store]
        products = reports.filtered(products, query)
        products = await run_in_threadpool(reports.economic_filter, products, user, query.store, query.manager)
        for product in products:
            if name in {"product-newness", "product-reputation"}:
                fields = ("is_new", "sales_days") if name == "product-newness" else ("rating", "reviews_count")
                row = {"article": product["article"], "name": product.get("name"),
                       **{key: product.get(key) for key in fields}}
                if name == "product-newness":
                    row["age_known"] = product.get("sales_days") is not None
                    if query.is_new is not None and row["is_new"] is not query.is_new:
                        continue
                rows.append(row)
                continue
            if name == "product-tags":
                tag = product.get("tag_data") or {}
                rows.append({"article": product["article"], "name": product.get("name"),
                             **{key: tag.get(key) for key in reports.TAG_LABELS}})
                continue
            current = product.get("current_economics") or {}
            price = product.get("price") or {}
            retail, client = price.get("current"), price.get("with_spp")
            spp = (retail - client) / retail * 100 if retail is not None and retail > 0 and client is not None else None
            rows.append({"article": product["article"], "name": product.get("name"),
                         "margin_per_unit_rub": current.get("margin"), "roi_percent": current.get("roi"),
                         "spp_percent": spp, "as_of_date": current.get("period_to"),
                         "calculation_available": current.get("margin") is not None,
                         "has_source_errors": bool(product.get("data_errors"))})
        context = {"block": "Текущая экономика", "basis": "one_unit",
                   "labels": {"margin_per_unit_rub": "Маржа на шт., ₽", "roi_percent": "ROI, %", "spp_percent": "СПП, %"}}
        warnings.append("Показатели блока «Текущая экономика» таблицы. Это не расчёт бокового калькулятора и не прибыль за исторический период. Наличие расчёта не гарантирует полноту источников; могут применяться значения по умолчанию сайта.")
        if name == "product-tags":
            rows = reports.filter_tags(rows, query)
            context = {"block": "ТЕГ", "labels": reports.TAG_LABELS, "period_verified": False}
            warnings = ["Сохранённые значения блока «ТЕГ». Даты обновления и календарная привязка прошлой недели не подтверждены. ends — исходная строка срока окончания стока, не гарантированная дата. Текстовые колонки сортируются как текст. null не является нулём; код товара не является артикулом."]
        if name == "product-newness":
            context = {"block": "Новинка", "new_sales_days_threshold": 28}
            warnings = ["Признак совпадает с сайтом. sales_days — календарные дни от первой загруженной продажи, не число дней с продажами и не подтверждённый возраст товара. При отсутствии продаж сайт использует дату создания карточки (sales_days=0). При неизвестном возрасте is_new=false не доказывает, что товар старый."]
        elif name == "product-reputation":
            context = {"block": "Товар", "labels": {"rating": "Рейтинг товара", "reviews_count": "Количество отзывов"}}
            warnings = ["Сохранённые рейтинг и количество отзывов из таблицы сайта. null означает отсутствие данных, а не ноль. Количество отдельных оценок без отзывов этим полем не подтверждается."]
    elif name == "profit-calculator":
        from app.agent_calculator import calculator
        from app.web.routers.unit_economics import sales_unit_economics_1c

        allowed = await run_in_threadpool(reports.economic_filter, [{"article": query.article}], user, query.store)
        if not allowed:
            raise HTTPException(404, "Товар не найден или недоступен")
        scoped = Request({**request.scope, "query_string": urlencode({"data": "1", "store": query.store, "article": query.article}).encode()})
        response = await sales_unit_economics_1c(scoped)
        if response.status_code != 200:
            raise HTTPException(response.status_code, "Товар не найден или недоступен")
        product = json.loads(response.body)["product"]
        row = calculator(product)
        target = await target_price_data(scoped)
        candidates = target.get("rows", []) if isinstance(target, dict) else []
        target_row = next((r for r in candidates if r.get("article") == query.article and r.get("store_slug") == query.store), {})
        row["results"]["target_price"] = target_row.get("target_price")
        row["target_warnings"] = target_row.get("target_warnings", [])
        rows = [row]
        context = {"mode": "saved_calculator", "basis": "one_unit", "input_rounding_decimals": 2,
                   "target_price_period": {k: target.get(k) for k in ("period_from", "period_to")} if isinstance(target, dict) else {},
                   "price_updated_at": (product.get("price") or {}).get("updated_at")}
        warnings += ["Расчёт блока «Расчёт чистой прибыли» по сохранённым данным, с округлением полей как на сайте. Несохранённые изменения браузера не включены.",
                     "ROI — прибыль на единицу / закупочная стоимость × 100, не ROI отчёта за период. Значения по умолчанию сайта не подтверждают полноту источников."]
    elif name in {"products", "product-details"}:
        rows = await run_in_threadpool(reports.catalog, query.store, query.marketplace)
        rows = reports.filtered(rows, query)
        if name == "product-details":
            if not rows:
                raise HTTPException(404, "Товар не найден")
            stock = await run_in_threadpool(reports.stocks, query)
            context["stock"] = reports.filtered(stock, query)
            warnings.append("warehouse_breakdown — отдельный снимок остатков по складам со своими датами обновления; его не нужно прибавлять к quantity.")
    elif name == "sales":
        rows = await run_in_threadpool(reports.sales, query)
        metrics = ("orders_count", "orders_amount", "cancelled_count", "cancelled_amount",
                   "sold_count", "sales_amount", "returned_count", "returned_amount")
        warnings.append("Даты относятся к заказам. Продажи/возвраты показаны для этих заказов, а не как выплаты за период.")
    elif name in {"funnel", "advertising"}:
        loader = reports.funnel if name == "funnel" else reports.advertising
        rows = await run_in_threadpool(loader, query)
        rows = reports.filtered(rows, query)
        if name == "advertising":
            rows = await run_in_threadpool(reports.economic_filter, rows, user, query.store, query.manager)
        metrics = (("orders_count", "orders_amount", "cancel_count", "cancel_amount", "buyout_count", "buyout_amount")
                   if name == "funnel" else ("spend", "impressions", "clicks"))
        rows = reports.grouped(rows, query.group_by, metrics)
        if name == "advertising":
            for row in rows:
                row["ctr_percent"] = row["clicks"] / row["impressions"] * 100 if row["impressions"] else None
                row["cpc_rub"] = row["spend"] / row["clicks"] if row["clicks"] else None
            warnings.append("Рекламная атрибуция заказов этим источником не предоставляется.")
    elif name in {"stocks", "stock-value"}:
        rows = reports.filtered(await run_in_threadpool(reports.stocks, query), query)
        metrics = ("quantity",)
        warnings.append("Текущие наблюдаемые остатки. Пустой источник не подтверждает нулевые остатки.")
        warnings.append("warehouse_breakdown — отдельный снимок остатков по складам со своими датами обновления; его не нужно прибавлять к quantity.")
        if name == "stocks" and query.view == "summary":
            rows, labels = await run_in_threadpool(reports.stock_summary, query)
            rows = reports.filtered(rows, query)
            metrics = tuple(labels)
            context = {"view": "summary", "labels": labels, "fulfillment": query.fulfillment,
                       "total_includes_transit": True}
            warnings = ["Сводка колонок сайта. total — сумма наблюдаемых значений, включая товар в пути; это не весь доступный к продаже остаток. null в компоненте не подтверждает ноль. При выборе fulfillment FBO/rFBS остаются общими по площадке, как на сайте. Даты снимков могут различаться."]
        if name == "stock-value":
            refs = await run_in_threadpool(reports.references, user, query.store, query.manager)
            costs = {r["article"]: r.get("purchase_price") for r in refs}
            rows = await run_in_threadpool(reports.economic_filter, rows, user, query.store, query.manager)
            for row in rows:
                row["purchase_price"] = costs.get(row["article"])
                row["stock_value_rub"] = row["quantity"] * row["purchase_price"] if row["purchase_price"] is not None else None
            metrics = ("quantity", "stock_value_rub")
    elif name == "costs":
        refs = await run_in_threadpool(reports.references, user, query.store, query.manager)
        names = {r["article"]: r["name"] for r in await run_in_threadpool(reports.catalog, query.store, "WB")}
        refs = [{**r, "name": names.get(r["article"])} for r in refs]
        rows = reports.select(refs, "article name manager purchase_price fulfillment_cost team_commission_percent abc_code goal_week goal_day fact_sales plan_sales")
        rows = reports.filtered(rows, query)
        warnings.append("Текущий импорт Google Sheets. Эти значения не подтверждают себестоимость прошлых дат.")
    elif name == "prices":
        rows = reports.filtered(await run_in_threadpool(reports.prices, query), query)
        rows = await run_in_threadpool(reports.economic_filter, rows, user, query.store, query.manager)
    elif name == "stock-operations":
        rows = reports.filtered(await run_in_threadpool(reports.operations, query, scope_pairs(user)), query)
        metrics = ("quantity",)
        warnings.append("Операции без площадки и межплощадочные операции вне полного доступа исключены.")
    elif name == "supplies":
        data = await run_in_threadpool(planned, query)
        rows = reports.select(data.get("supplies", []), "supply_id preorder_id supply_date warehouse_name status supply_type is_urgent")
        context["cache_ttl_seconds"] = 300
        context["fetched_at"] = data.get("fetched_at")
        if data.get("errors"):
            warnings.append("Не удалось получить часть данных WB о поставках. Повторите позже.")
        if all((query.store, mp) in scope_pairs(user) for mp in MARKETPLACES):
            manual = await run_in_threadpool(db.list_manual_supplies, (query.store,))
            rows += [{**r, "source": "manual"} for r in reports.select(
                [r for r in manual if query.date_from.isoformat() <= str(r.get("delivery_at") or "")[:10] <= query.date_to.isoformat()],
                "id delivery_at origin destination supply_type ready")]
        else:
            warnings.append("Ручные планы без площадки скрыты при ограничении доступа по площадкам.")
    elif name in {"profit", "target-prices"}:
        params = {"store": query.store}
        for field in ("date_from", "date_to", "article", "manager"):
            value = getattr(query, field)
            if value is not None:
                params[field] = str(value)
        scoped = Request({**request.scope, "query_string": urlencode(params).encode()})
        data = await (_unit_economics_1c_unit_profit_report_data(scoped) if name == "profit" else target_price_data(scoped))
        if isinstance(data, JSONResponse):
            return data
        rows = data["rows"]
        rows = reports.filtered(rows, query)
        rows = await run_in_threadpool(reports.economic_filter, rows, user, query.store, query.manager)
        if name == "profit":
            rows = reports.select(rows, "article name manager orders_count orders_amount advertising_spend margin margin_complete margin_missing_days roi")
            for row in rows:
                if row["margin_complete"] is not True:
                    row["margin"] = row["roi"] = None
            metrics = ("orders_count", "orders_amount", "advertising_spend", "margin")
        else:
            context = {k: data.get(k) for k in ("period_from", "period_to")}
        warnings.append("Расчётные показатели по методике сайта. Не подтверждённый финансовый результат; полнота источников обязательна.")
    elif name == "rnp":
        observed = await run_in_threadpool(reports.read,
            "SELECT COUNT(*) AS records FROM rnp_daily_metrics WHERE store_slug=? AND marketplace=? AND SUBSTR(day,1,7)=?",
            (query.store, query.marketplace, query.month))
        if not observed[0]["records"]:
            return reports.envelope(query, {"rows": [], "total_rows": 0, "next_offset": None,
                "totals": {}, "context": {"month": query.month, "available": False}},
                ["История метрик РНП за месяц не загружена. Для заказов используйте sales, для остатков stocks."])
        data = await run_in_threadpool(rnp.dashboard, query.month, query.marketplace, query.store,
                                       query.search or "", query.limit, query.offset)
        safe = reports.select(data["products"], "article barcode name current_stock stock_updated_at current_price price_source fact forecast")
        return reports.envelope(query, {"rows": safe, "total_rows": data["pagination"]["total"],
            "next_offset": query.offset + query.limit if data["pagination"]["has_more"] else None,
            "totals": {}, "context": {"month": data["month"], "metrics": data["metrics"]}},
            ["РНП использует расчётные и резервные значения сайта. При пустой истории расширенные показатели не подтверждены."])
    elif name == "decisions":
        sources = await run_in_threadpool(reports.status, query.store, query.marketplace, section.value)
        if not any(s["records"] for s in sources):
            return reports.envelope(query, {"rows": [], "total_rows": 0, "next_offset": None,
                "totals": {}, "context": {"available": False}},
                ["Метрики Центра решений не загружены. Достоверные рекомендации пока недоступны."], sources)
        data = await run_in_threadpool(decision_center.dashboard, (query.store,))
        rows = data.get("opportunities", data.get("decisions", []))
        warnings.append("Эвристические предложения сайта: содержат оценки и резервные значения. Не доказанные причины и не фактическая прибыль.")
    sort_fields = tuple(reports.TAG_LABELS) if name == "product-tags" else ("margin_per_unit_rub", "roi_percent", "spp_percent") if name == "current-economics" else ()
    if name == "product-newness":
        sort_fields = ("sales_days", "is_new")
    elif name == "product-reputation":
        sort_fields = ("rating", "reviews_count")
    payload = reports.page(rows, query, metrics, sort_fields=sort_fields)
    if name == "stocks" and query.view == "summary":
        payload["totals"] = {key: sum(row.get(key) or 0 for row in rows) for key in metrics}
        context["totals_basis"] = "observed_values_like_table"
        context["missing_rows_by_component"] = {key: sum(row.get(key) is None for row in rows) for key in metrics}
    payload["context"] = context
    if not rows:
        warnings.append("Нет строк по выбранным условиям. Это не доказывает отсутствие событий или убытков.")
    sources = []
    if name in {"sales", "stocks", "stock-operations"}:
        sources = await run_in_threadpool(reports.status, query.store, query.marketplace, section.value)
    return reports.envelope(query, payload, warnings, sources)


def endpoint(name):
    async def handle(request: Request, user: Employee, query: Annotated[ReportQuery, Query()]):
        try:
            return await execute(name, request, user, query)
        except HTTPException:
            raise
        except ValueError as error:
            raise HTTPException(422, "Некорректные параметры отчёта") from error
        except Exception as error:
            logger.error("agent_report_failed report=%s type=%s", name, type(error).__name__)
            raise HTTPException(503, "Источник отчёта временно недоступен") from error
    return handle


for report_name, spec in SPECS.items():
    router.add_api_route("/" + report_name, endpoint(report_name), methods=["GET"],
                        operation_id="getAnalytics" + "".join(part.title() for part in report_name.split("-")),
                        description=spec[3], response_model=ReportResponse)
