"""Read-only adapters for existing WB data; no external sync is triggered."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.access.access_control import scope_pairs
from app.agents import reports
from app.config import settings
from app.dto.identity import SectionName as S

EXTRA_SPECS = {
    "stock-history": (
        S.STOCK_BALANCES,
        True,
        "date_from date_to article scheme warehouse",
        "Daily observed WB stock snapshots. Required dates; missing snapshots are unknown, not zero. FF warehouse filter applies only to ff rows.",
    ),
    "orders": (
        S.UNIT_ECONOMICS_WB,
        True,
        "date_from date_to article search scheme order_id status manager",
        "WB order lines by order creation date (Moscow). Required dates. Current observed status, not status history. No buyer data or raw payloads.",
    ),
    "inbound-supplies": (
        S.STOCK_INBOUND,
        True,
        "article supply_id status warehouse",
        "Cached WB FBO supply items, accepted/ready quantities and discrepancies. status is the normalized supply stage. No live sync.",
    ),
    "supply-arrivals": (
        S.STOCK_ARRIVALS,
        True,
        "date_from date_to status warehouse",
        "Shared store arrivals register, not WB-specific. Requires access to every marketplace in the store. Optional dates filter actual arrival; future dates allowed.",
    ),
    "stock-cost-report": (
        S.STOCK_COST_REPORT,
        True,
        "date_from date_to cost_view",
        "Website stock movement and purchase-cost report. Required dates. Views: summary, deliveries, transfers, shipments, fbs_transfers, fbs_sales, fbs_actual_sales. Formula FBS sales differ from actual sales.",
    ),
    "economics-history": (
        S.UNIT_ECONOMICS_WB,
        True,
        "article",
        "Last 21 days of the website product chart: orders, margin, advertising and stock. Exact article required. Current day incomplete; unknown values stay null.",
    ),
}


def stock_history(query):
    params = (query.store, query.marketplace, str(query.date_from), str(query.date_to))
    rows = reports.read(
        "SELECT article, day, scheme, quantity, captured_at AS updated_at, NULL AS warehouse "
        "FROM marketplace_stock_daily_history WHERE store_slug=? AND marketplace=? AND day>=? AND day<=? "
        "ORDER BY day, article, scheme",
        params,
    )
    rows += reports.read(
        "SELECT article, day, 'ff' AS scheme, quantity, captured_at AS updated_at, fulfillment AS warehouse "
        "FROM fulfillment_stock_daily_history WHERE store_slug=? AND marketplace=? AND day>=? AND day<=? "
        "ORDER BY day, article, fulfillment",
        params,
    )
    return reports.filtered(rows, query)


def orders(query, user):
    clause, dates = reports.date_clause(query, "ordered_at")
    # The registry normalizes timestamps to Moscow; ISO date boundaries are inclusive/exclusive.
    rows = reports.read(
        "SELECT external_order_id, line_key, article, barcode, name, scheme, status, substatus, "
        "ordered_at, source_updated_at, cancelled_at, sold_at, returned_at, quantity, "
        "cancelled_quantity, sold_quantity, return_quantity, order_amount, cancelled_amount, "
        "sale_amount, return_amount, currency, synced_at AS updated_at "
        f"FROM sales_order_lines WHERE store_slug=? AND marketplace=? AND {clause} "
        "ORDER BY ordered_at, external_order_id, line_key",
        (query.store, query.marketplace, *dates),
    )
    rows = reports.filtered(rows, query)
    if query.marketplace == "YANDEX MARKET":
        from app.agents.yandex_reports import economic_filter

        rows = economic_filter(rows, user, query.store, query.manager)
    else:
        rows = reports.economic_filter(rows, user, query.store, query.manager)
    if query.order_id:
        rows = [row for row in rows if str(row["external_order_id"]) == query.order_id]
    if query.status:
        rows = [row for row in rows if row["status"] == query.status]
    return rows


def inbound(request, query):
    snapshots = request.app.state.container.inbound_supplies.report(((query.store, query.marketplace),))
    rows, sources = [], []
    cutoff = datetime.now(UTC) - timedelta(seconds=max(settings.inbound_sync_interval_seconds * 2, 3600))
    for snapshot in snapshots:
        if (snapshot.store_slug, snapshot.marketplace) != (query.store, query.marketplace):
            continue
        sources.append(
            {
                "source": "inbound_supplies",
                "status": snapshot.status,
                "updated_at": snapshot.last_success,
                "stale": not snapshot.last_success or datetime.fromisoformat(snapshot.last_success) < cutoff,
                "has_error": bool(snapshot.error),
            }
        )
        for supply in snapshot.supplies:
            if query.supply_id and supply.supply_id != query.supply_id:
                continue
            if query.status and supply.stage != query.status:
                continue
            common = reports.select(
                [supply.model_dump(mode="json")],
                "supply_id order_id number status status_label stage warehouse transit_warehouse "
                "planned_at created_at updated_at checked_at unavailable warning",
            )[0]
            for item in supply.items:
                rows.append({**common, **item.model_dump(mode="json")})
            if not supply.items:
                rows.append({**common, "article": None, "quantity": None, "items_missing": True})
    return reports.filtered(rows, query), sources


def arrivals(query, user):
    from app.stock import supply_arrivals

    data = supply_arrivals.report(user)
    rows = [row for row in data["rows"] if row.get("store_slug") == query.store]
    if query.date_from:
        rows = [
            row
            for row in rows
            if row.get("arrival") and str(query.date_from) <= str(row["arrival"])[:10] <= str(query.date_to)
        ]
    if query.status:
        rows = [row for row in rows if row.get("status") == query.status]
    if query.warehouse:
        rows = [row for row in rows if row.get("warehouse") == query.warehouse]
    rows = reports.select(
        rows,
        "row store_slug project order group category warehouse status arrival volume weight boxes shipping warnings",
    )
    return rows, [
        {
            "source": "supply_arrivals",
            "updated_at": data.get("last_success"),
            "stale": data.get("stale"),
            "has_error": bool(data.get("error")),
        }
    ]


def cost_report(query, user):
    from app.stock import cost_report as source

    data = source.build_report(
        (query.store,), query.date_from, query.date_to, (query.marketplace,), scope_pairs(user)
    )
    context = {
        "reconciliation": [
            {key: value for key, value in row.items() if key != "items"}
            for row in data.get("reconciliation", [])
        ]
    }
    if query.cost_view in {"summary", "fbs_sales", "fbs_actual_sales"}:
        rows = data[query.cost_view]
        if query.cost_view == "summary":
            rows = [
                {
                    **row,
                    "fbs_formula": {
                        key: value for key, value in row.get("fbs_formula", {}).items() if key != "items"
                    },
                }
                for row in rows
            ]
        return rows, context
    rows = []
    allowed = set(scope_pairs(user))
    for operation in source.operations_for_view(data, query.cost_view):
        platforms = {operation.get(k) for k in ("from_marketplace", "to_marketplace")} - {None, ""}
        if any((query.store, mp) not in allowed for mp in platforms):
            continue
        common = reports.select(
            [operation],
            "id kind created_at from_marketplace to_marketplace from_fulfillment to_fulfillment is_fbs_transfer",
        )[0]
        for item in operation.get("items", []):
            rows.append(
                {
                    **common,
                    **reports.select([item], "article barcode name quantity purchase_price purchase_cost")[0],
                }
            )
    return rows, context


async def execute_extra(name, request, user, query, *, paginate=True):
    warnings = [
        "Отсутствие записей не подтверждает нулевые значения. API читает сохранённые данные без запуска синхронизации."
    ]
    context, sources = {}, []
    sort_fields = ("ordered_at", "external_order_id", "supply_id", "arrival", "row", "store_slug")
    if name == "stock-history":
        rows = await run_in_threadpool(stock_history, query)
        context = {"basis": "daily_stock_snapshots", "transit_included": False}
        warnings.append(
            "Снимки разных схем могут иметь разное время обновления. Остатки разных дней не суммируются."
        )
    elif name == "orders":
        rows = await run_in_threadpool(orders, query, user)
        context = {"basis": "order_lines", "date_filter": "ordered_at", "status_basis": "latest_observed"}
    elif name == "inbound-supplies":
        rows, sources = await run_in_threadpool(inbound, request, query)
        context = {"basis": "supply_items"}
    elif name == "supply-arrivals":
        rows, sources = await run_in_threadpool(arrivals, query, user)
        context = {"marketplace_scope": "shared_store_register", "date_filter": "actual_arrival"}
        warnings.append(
            "Реестр общий для магазина: строки не привязаны к WB. При фильтре дат строки без фактической даты исключаются."
        )
    elif name == "stock-cost-report":
        rows, context = await run_in_threadpool(cost_report, query, user)
        context.update(view=query.cost_view, basis="website_stock_cost_report")
        warnings.append(
            "Оценка по закупочным ценам сайта; missing_units отражает отсутствие цены. Продажи FBS по формуле не равны фактическим продажам. Детали межплощадочных операций скрыты без доступа ко всем их площадкам."
        )
    else:
        from app.web.routers.unit_economics import sales_unit_economics_1c

        allowed = await run_in_threadpool(
            reports.economic_filter, [{"article": query.article}], user, query.store
        )
        if not allowed:
            raise HTTPException(404, "Товар не найден или недоступен")
        scoped = Request(
            {
                **request.scope,
                "query_string": urlencode(
                    {"data": "1", "store": query.store, "article": query.article}
                ).encode(),
            }
        )
        response = await sales_unit_economics_1c(scoped)
        if response.status_code != 200:
            raise HTTPException(response.status_code, "История экономики недоступна")
        product = json.loads(response.body).get("product")
        if (
            not product
            or product.get("store_slug") != query.store
            or str(product.get("article")) != query.article
        ):
            raise HTTPException(404, "Товар не найден или недоступен")
        rows = [{"article": query.article, **row, "day": row["date"]} for row in product.get("history", [])]
        context = {"basis": "website_product_chart", "max_days": 21}
        warnings.append(
            "Последние 21 день графика сайта. Текущий день неполный; margin_complete=false означает неполную маржу."
        )
    page_query = query if paginate else query.model_copy(update={"offset": 0, "limit": max(1, len(rows))})
    # Explicit scalar default: some source reports start with a nested metric object.
    if page_query.sort_by is None:
        default = {
            "orders": "ordered_at",
            "stock-history": "day",
            "economics-history": "day",
            "inbound-supplies": "supply_id",
            "supply-arrivals": "row",
            "stock-cost-report": "store_slug",
        }[name]
        page_query = page_query.model_copy(update={"sort_by": default})
    payload = reports.page(rows, page_query, sort_fields=sort_fields)
    return reports.envelope(query, {**payload, "context": context}, warnings, sources)
