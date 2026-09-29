"""Employee read models backed exclusively by the site's Yandex sources."""

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from app.agents import reports
from app.dto.identity import SectionName as S
from app.economics.yandex import calculations, target_prices
from app.economics.yandex import reports as ym
from app.repositories import unit_economics_yandex as metrics
from app.repositories import yandex_source_values, yandex_storefront
from app.yandex import economics, economics_history

MARKETPLACE = "YANDEX MARKET"
ECONOMIC = {
    "product-newness",
    "product-reputation",
    "product-tags",
    "current-economics",
    "profit-calculator",
    "costs",
    "prices",
    "stock-value",
    "profit",
    "target-prices",
    "economics-history",
    "advertising",
}
SHARED = {"stock-history", "orders", "inbound-supplies", "supply-arrivals", "stock-cost-report"}
SUPPORTED = ECONOMIC | SHARED
SECTION_MAP = {
    S.UNIT_ECONOMICS_WB: S.UNIT_ECONOMICS_YANDEX,
    S.REPORT_UNIT_PROFIT: S.REPORT_UNIT_PROFIT_YANDEX,
    S.REPORT_TARGET_PRICE: S.REPORT_TARGET_PRICE_YANDEX,
}


def section_for(section, marketplace):
    return SECTION_MAP.get(section, section) if marketplace == MARKETPLACE else section


def economic_filter(rows, user, store, manager=None):
    allowed = {
        row["article"]
        for row in ym.catalog((store,), user)
        if not manager or str(row.get("manager") or "").casefold() == manager.casefold()
    }
    return [row for row in rows if row.get("article") in allowed]


def source_status(store):
    return reports.read(
        "SELECT source, COUNT(*) AS records, MIN(day) AS first_observed, MAX(day) AS last_observed, "
        "MAX(updated_at) AS updated_at FROM unit_economics_yandex_daily_metrics "
        "WHERE store_slug=? GROUP BY source",
        (store,),
    )


def load(name, query, user):
    context = {"basis": "website_yandex", "marketplace": MARKETPLACE}
    allowed = [
        row
        for row in ym.catalog((query.store,), user)
        if not query.manager or str(row.get("manager") or "").casefold() == query.manager.casefold()
    ]
    allowed = reports.filtered(allowed, query)
    if query.article and not allowed:
        raise HTTPException(404, "Товар не найден или недоступен")
    articles = {p["article"] for p in allowed}
    names = {p["article"]: p.get("name") for p in allowed}
    if name == "advertising":
        raw, days = metrics.get_history(query.store, "advertising", str(query.date_from), str(query.date_to))
        missing = sorted(set(metrics.days_between(str(query.date_from), str(query.date_to))) - days)
        context.update(missing_days=missing, complete=not missing, impressions_basis="advertising_only")
        grouped = {}
        for source in raw:
            if source["article"] not in articles:
                continue
            key = source[query.group_by]
            grouped.setdefault(key, []).append(source)
        rows = []
        for key, items in grouped.items():
            row = {query.group_by: key}
            for field in ("spend", "impressions", "clicks"):
                values = [r.get(field) for r in items]
                row[field] = sum(values) if all(v is not None for v in values) else None
            row["ctr_percent"] = (
                row["clicks"] / row["impressions"] * 100
                if row["impressions"] and row["clicks"] is not None
                else None
            )
            row["cpc_rub"] = (
                row["spend"] / row["clicks"] if row["clicks"] and row["spend"] is not None else None
            )
            rows.append(row)
    elif name == "profit":
        rows = ym.load_rows((query.store,), user, query.date_from, query.date_to)
        rows = [dict(r) for r in rows if r["article"] in articles]
        # Preserve the website's partial totals under explicit names; unknown is not zero.
        for row in rows:
            row.pop("daily_calculations", None)
            row["report_margin"], row["report_roi"] = row.get("margin"), row.get("roi")
            if not row.get("margin_complete"):
                row["margin"] = row["roi"] = None
        context["basis"] = "period_orders_not_confirmed_sales"
    elif name == "target-prices":
        data = target_prices.load((query.store,), user, article=query.article or "")
        rows = [dict(r) for r in data["rows"] if r["article"] in articles]
        context.update({k: data.get(k) for k in ("period_from", "period_to")})
    elif name == "profit-calculator":
        data = economics.detail(query.store, query.article, mode="calculator", include_history=False)
        rows = [
            {
                "article": query.article,
                "name": names[query.article],
                "inputs": data["calculator_values"],
                "results": data["calculator_result"],
                "origins": data["calculator_origins"],
                "pricing": data["calculator_pricing"],
                "tariff": data["calculator_tariff"],
                "advertising": data["calculator_advertising"],
            }
        ]
        context.update(mode="saved_calculator", basis="one_unit")
    elif name == "economics-history":
        data = economics_history.product_history(query.store, query.article, "FBY")
        rows = [{**r, "article": query.article, "day": r["date"]} for r in data["chart"]]
        context.update(basis="website_product_chart", max_days=21, current_day_incomplete=True)
    elif name in {"costs", "stock-value"}:
        refs = yandex_source_values.get_values(query.store)
        if name == "costs":
            rows = [
                {
                    "article": a,
                    "name": names[a],
                    **{
                        k: refs.get(a, {}).get(k)
                        for k in (
                            "manager",
                            "purchase_price",
                            "fulfillment_cost",
                            "abc_code",
                            "goal_week",
                            "goal_day",
                            "fact_sales",
                            "plan_sales",
                            "synced_at",
                        )
                    },
                }
                for a in articles
            ]
        else:
            rows = [
                dict(r) for r in reports.filtered(reports.stocks(query), query) if r["article"] in articles
            ]
            for row in rows:
                cost = refs.get(row["article"], {}).get("purchase_price")
                row.update(
                    purchase_price=cost,
                    stock_value_rub=(
                        row["quantity"] * cost
                        if cost is not None and row.get("quantity") is not None
                        else None
                    ),
                )
    elif name == "prices":
        saved = yandex_storefront.get_prices(query.store)
        rows = [
            {
                "article": a,
                "name": names[a],
                **yandex_storefront.resolved_prices(saved.get(a, {})),
                **{k: saved.get(a, {}).get(k) for k in ("status", "checked_at", "price_checked_at")},
            }
            for a in articles
        ]
        context["basis"] = "current_storefront_prices"
    else:
        products = calculations.load_products((query.store,), article=query.article or "")
        rows = []
        for product in products:
            if product["article"] not in articles:
                continue
            row = {"article": product["article"], "name": product.get("name")}
            if name == "current-economics":
                current = product.get("current_economics") or {}
                price = product.get("price") or {}
                seller, buyer = price.get("current"), price.get("with_spp")
                # Same price pair and formula as dashboard.calculateSppPercent; Pay is separate.
                spp = (
                    (seller - buyer) / seller * 100
                    if seller is not None and seller > 0 and buyer is not None
                    else None
                )
                row.update(
                    margin_per_unit_rub=current.get("margin"),
                    roi_percent=current.get("roi"),
                    spp_percent=spp,
                    as_of_date=current.get("period_to"),
                    calculation_available=current.get("margin") is not None,
                    has_source_errors=bool(product.get("data_errors")),
                )
            elif name == "product-tags":
                row.update({k: (product.get("tag_data") or {}).get(k) for k in reports.TAG_LABELS})
            elif name == "product-reputation":
                row.update({k: product.get(k) for k in ("rating", "reviews_count")})
            else:
                row.update(is_new=product.get("is_new"), sales_days=None, age_known=False)
                if query.is_new is not None and row["is_new"] is not query.is_new:
                    continue
            rows.append(row)
        if name == "product-tags":
            rows = reports.filter_tags(rows, query)
    from app.agents.catalog import documentation

    fields = documentation(name)["marketplace_guides"][MARKETPLACE]["fields"]
    keys = {field["name"].split(".")[0] for field in fields if not field["name"].startswith("context.")}
    return [{k: value for k, value in row.items() if k in keys} for row in rows], context


async def execute(name, query, user, *, paginate=True):
    rows, context = await run_in_threadpool(load, name, query, user)
    page_query = query if paginate else query.model_copy(update={"offset": 0, "limit": max(1, len(rows))})
    if page_query.sort_by is None:
        page_query = page_query.model_copy(
            update={
                "sort_by": "day"
                if name == "economics-history" or (name == "advertising" and query.group_by == "day")
                else "article"
            }
        )
    scalar_fields = {k for row in rows for k, value in row.items() if not isinstance(value, (dict, list))}
    payload = reports.page(rows, page_query, sort_fields=scalar_fields)
    return reports.envelope(
        query,
        {**payload, "context": context},
        [
            "Источник — сохранённые данные и расчёты Яндекс Маркета на сайте. Запрос не запускает синхронизацию.",
            "null означает отсутствие данных. Проверяйте полноту расчёта и даты источников; заказы не равны выкупам.",
        ],
    )
