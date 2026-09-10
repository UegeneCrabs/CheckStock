"""Explicit, scoped read models for employee analytics. No arbitrary database access."""

import math
from datetime import UTC, datetime, timedelta

from app import db
from app.dto.identity import Role
from app.repositories.core import get_connection

TAG_LABELS = {"goal_week": "Цель неделя", "goal_day": "Цель день", "status": "Статус стока",
              "ends": "Сток закончится", "code": "Код товара", "fact": "Факт прошл. недели", "plan": "План прошл. недели"}


def filter_tags(rows, query):
    if query.tag_column is None and query.tag_value is None and query.tag_operator == "eq":
        return rows
    if query.tag_column is None or query.tag_value is None:
        raise ValueError("Укажите tag_column и tag_value вместе")
    numeric = query.tag_column in {"goal_week", "goal_day", "fact", "plan"}
    value = float(query.tag_value) if numeric else query.tag_value.casefold()
    if numeric and not math.isfinite(value):
        raise ValueError("Числовой фильтр должен быть конечным числом")
    if not numeric and query.tag_operator != "eq":
        raise ValueError("Для текстовых колонок поддерживается только eq")
    result = []
    for row in rows:
        actual = row.get(query.tag_column)
        if actual is None:
            continue
        actual = actual if numeric else str(actual).casefold()
        if ((query.tag_operator == "eq" and actual == value)
                or (query.tag_operator == "gte" and actual >= value)
                or (query.tag_operator == "lte" and actual <= value)):
            result.append(row)
    return result


def read(sql, params=()):
    connection = get_connection()
    try:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]
    finally:
        connection.close()


def select(rows, fields):
    return [{key: row.get(key) for key in fields.split()} for row in rows]


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return value


def catalog(store, marketplace):
    return read(
        "SELECT article, barcode, name, mp_sku, mp_product_id, image_url, mp_updated_at "
        "FROM stock_items WHERE store_slug=? AND marketplace=? AND is_service=0 ORDER BY article",
        (store, marketplace),
    )


def references(user, store, manager=None):
    from app.web.routers.unit_economics import _manager_matches_user

    rows = db.get_unit_economics_1c_product_reference_rows((store,))
    if user.role == Role.USER:
        rows = [r for r in rows if _manager_matches_user(str(r.get("manager") or ""), user)]
    if manager:
        rows = [r for r in rows if str(r.get("manager") or "").casefold() == manager.casefold()]
    return rows


def economic_filter(rows, user, store, manager=None, article_key="article"):
    if user.role != Role.USER and not manager:
        return rows
    allowed = {str(r["article"]) for r in references(user, store, manager)}
    all_articles = {str(r["article"]) for r in db.get_unit_economics_1c_product_reference_rows((store,))}
    forbidden_bases = {a.partition(" / ")[0] for a in all_articles - allowed}
    allowed_bases = {a.partition(" / ")[0] for a in allowed} - forbidden_bases
    return [r for r in rows if (str(r.get(article_key) or "") in allowed
            if " / " in str(r.get(article_key) or "")
            else str(r.get(article_key) or "") in allowed_bases)]


def date_clause(query, column="day"):
    return f"{column}>=? AND {column}<?", (
        query.date_from.isoformat(), (query.date_to + timedelta(days=1)).isoformat()
    )


def sales(query):
    clause, dates = date_clause(query, "ordered_at")
    grouping = "SUBSTR(ordered_at,1,10)" if query.group_by == "day" else "article"
    params = [query.store, query.marketplace, *dates]
    filters = ""
    if query.article:
        filters += " AND article=?"
        params.append(query.article)
    if query.scheme:
        filters += " AND scheme=?"
        params.append(query.scheme)
    rows = read(
        f"SELECT {grouping} AS {query.group_by}, SUM(quantity) AS orders_count, "
        "SUM(order_amount) AS orders_amount, SUM(cancelled_quantity) AS cancelled_count, "
        "SUM(cancelled_amount) AS cancelled_amount, SUM(sold_quantity) AS sold_count, "
        "SUM(sale_amount) AS sales_amount, SUM(return_quantity) AS returned_count, "
        "SUM(return_amount) AS returned_amount, MAX(synced_at) AS updated_at "
        f"FROM sales_order_lines WHERE store_slug=? AND marketplace=? AND {clause} {filters} "
        f"GROUP BY {grouping}", params,
    )
    state = read("SELECT ok FROM sales_sync_state WHERE store_slug=? AND marketplace=?",
                 (query.store, query.marketplace))
    if not state or not state[0]["ok"]:
        for row in rows:
            for key in ("sold_count", "sales_amount", "returned_count", "returned_amount"):
                row[key] = None
    return rows


def funnel(query):
    clause, dates = date_clause(query)
    return read(
        "SELECT article, day, vendor_code, product_name, orders_count, orders_amount, "
        "cancel_count, cancel_amount, buyout_count, buyout_amount, buyout_percent, updated_at "
        f"FROM wb_funnel_daily_orders WHERE store_slug=? AND {clause}", (query.store, *dates)
    )


def advertising(query):
    clause, dates = date_clause(query)
    return read(
        "SELECT nm_id AS article, day, spend, impressions, clicks, synced_at AS updated_at "
        f"FROM unit_economics_1c_wb_daily_advertising WHERE store_slug=? AND {clause}",
        (query.store, *dates),
    )


def prices(query):
    if query.date_from:
        clause, dates = date_clause(query)
        rows = read(
            "SELECT article, day, seller_base_price, retail_price, club_discounted_price, "
            "customer_price_with_spp, customer_price_with_wallet, updated_at "
            f"FROM unit_economics_1c_wb_daily_prices WHERE store_slug=? AND {clause}",
            (query.store, *dates),
        )
    else:
        rows = select(db.get_unit_economics_1c_latest_daily_prices((query.store,)),
                      "article day seller_base_price retail_price club_discounted_price "
                      "customer_price_with_spp customer_price_with_wallet updated_at")
    available = {r["article"] for r in catalog(query.store, "WB")}
    return [r for r in rows if r["article"] in available]


def stocks(query):
    rows = read(
        "SELECT article, scheme, NULL AS warehouse, quantity, updated_at FROM mp_stock "
        "WHERE store_slug=? AND marketplace=?", (query.store, query.marketplace)
    )
    for row in rows:
        row["source"] = "marketplace"
    detailed = read("SELECT article, scheme, warehouse, quantity, updated_at FROM mp_warehouse_stock "
                    "WHERE store_slug=? AND marketplace=?", (query.store, query.marketplace))
    if query.warehouse:
        rows = [{**r, "source": "marketplace"} for r in detailed]
    else:
        by_product = {}
        for detail in detailed:
            by_product.setdefault((detail["article"], detail["scheme"]), []).append(
                {key: detail[key] for key in ("warehouse", "quantity", "updated_at")}
            )
        for row in rows:
            # A separate snapshot is context, never extra stock to sum into totals.
            row["warehouse_breakdown"] = by_product.get((row["article"], row["scheme"]), [])
    fulfillment = read(
        "SELECT article, fulfillment AS warehouse, quantity, updated_at FROM ff_stock "
        "WHERE store_slug=? AND marketplace=?", (query.store, query.marketplace)
    )
    for row in fulfillment:
        row.update(source="fulfillment", scheme="ff")
    transit = read(
        "SELECT i.to_article AS article, b.to_fulfillment AS warehouse, "
        "SUM(i.sent_quantity-i.received_quantity-i.cancelled_quantity) AS quantity "
        "FROM ff_transit_items i JOIN ff_transit_batches b ON b.id=i.batch_id "
        "WHERE b.store_slug=? AND b.to_marketplace=? AND b.status IN ('in_transit','partial') "
        "GROUP BY i.to_article,b.to_fulfillment",
        (query.store, query.marketplace),
    )
    for row in transit:
        row.update(source="transit", scheme="transit", updated_at=None)
    return rows + fulfillment + [r for r in transit if r["quantity"] > 0]


def stock_summary(query):
    from app.web.stock_rendering import schemes_for

    schemes = schemes_for(query.marketplace, query.store)
    labels = {"total": "Тотал", "ff_available": "Доступно ФФ для распределения", "transit": "В пути между ФФ",
              **{scheme + "_stock": label for scheme, label in schemes}}
    rows = db.get_stock_items(query.store, query.marketplace, tuple(s for s, _ in schemes))
    ff = db.get_ff_available_totals(query.store, query.fulfillment, query.marketplace)
    transit = db.get_ff_transit_totals(query.store, query.marketplace, query.fulfillment)
    selected = {}
    if query.fulfillment:
        selected = {s: db.get_mp_stock_by_warehouse(query.store, query.marketplace, s, query.fulfillment)
                    for s, _ in schemes if s == "fbs" or s.startswith("fbs_")}
    for row in rows:
        row["ff_available"] = ff.get(row["article"])
        row["transit"] = transit.get(row["article"], 0)
        for scheme, quantities in selected.items():
            row[scheme + "_stock"] = quantities.get(row["article"])
        row["total"] = sum(row.get(key) or 0 for key in labels if key != "total")
        row["missing_components"] = [key for key in labels if key != "total" and row.get(key) is None]
    return rows, labels


def operations(query, allowed_pairs):
    rows = db.get_operations_with_items_for_period(
        (query.store,), query.date_from.isoformat(),
        (query.date_to + timedelta(days=1)).isoformat(),
    )
    result = []
    for row in rows:
        platforms = {r for r in (row.get("from_marketplace"), row.get("to_marketplace")) if r}
        if not platforms or query.marketplace not in platforms:
            continue
        if any((query.store, p) not in allowed_pairs for p in platforms):
            continue
        if query.kind and row.get("kind") != query.kind:
            continue
        safe = select([row], "id kind created_at from_fulfillment to_fulfillment "
                      "from_marketplace to_marketplace")[0]
        for item in row.get("items", []):
            result.append({**safe, **select([item], "article barcode name quantity")[0]})
    return result


def grouped(rows, group_by, metrics):
    buckets = {}
    for row in rows:
        key = row.get("day" if group_by == "day" else "article")
        bucket = buckets.setdefault(key, {group_by: key, "updated_at": None})
        if row.get("updated_at"):
            bucket["updated_at"] = max(bucket["updated_at"] or "", row["updated_at"])
        for field in metrics:
            value = row.get(field)
            if field not in bucket:
                bucket[field] = value
            elif value is None or bucket[field] is None:
                bucket[field] = None
            else:
                bucket[field] += value
    return list(buckets.values())


def filtered(rows, query):
    result = rows
    if query.article:
        result = [r for r in result if str(r.get("article") or "") == query.article]
    if query.search:
        term = query.search.casefold()
        result = [r for r in result if any(term in str(r.get(k) or "").casefold()
                  for k in ("article", "barcode", "name", "vendor_code", "product_name"))]
    if query.warehouse:
        result = [r for r in result if r.get("warehouse") == query.warehouse]
    if query.scheme:
        result = [r for r in result if r.get("scheme") == query.scheme]
    return result


def page(rows, query, metrics=(), *, sort_fields=()):
    rows = clean(rows)
    key = query.sort_by or (metrics[0] if metrics else next(iter(rows[0]), "article") if rows else "article")
    if query.sort_by and key not in set(metrics) | set(sort_fields) | {"article", "day", "name", "updated_at", "created_at"}:
        raise ValueError("Unsupported sort_by")
    known = [r for r in rows if r.get(key) is not None]
    unknown = [r for r in rows if r.get(key) is None]
    known.sort(key=lambda r: (r[key], str(r.get("article") or r.get("day") or "")),
               reverse=query.order == "desc")
    rows = known + unknown
    totals = {key: sum(r[key] for r in rows) if rows and all(r.get(key) is not None for r in rows)
              else None for key in metrics}
    return {"rows": rows[query.offset:query.offset + query.limit], "total_rows": len(rows),
            "next_offset": query.offset + query.limit if query.offset + query.limit < len(rows) else None,
            "totals": totals}


def status(store, marketplace, section):
    sources = {
        "sales": [("sales_order_lines", "synced_at", "ordered_at", True)],
        "stock": [("mp_stock", "updated_at", None, True), ("ff_stock", "updated_at", None, True)],
        "unit_economics_1c": [("unit_economics_1c_wb_daily_prices", "updated_at", "day", True),
                              ("unit_economics_1c_wb_daily_advertising", "synced_at", "day", True),
                              ("unit_economics_1c_daily_margin_snapshots", "captured_at", "day", False)],
        "rnp": [("rnp_daily_metrics", "funnel_synced_at", "day", True)],
        "decision_center": [("wb_decision_metrics", "funnel_synced_at", None, False)],
    }
    result = []
    for table, stamp, day, has_marketplace in sources.get(section, []):
        period = f", MIN({day}) AS first_observed, MAX({day}) AS last_observed" if day else ""
        clause = " AND marketplace=?" if has_marketplace else ""
        values = (store, marketplace) if has_marketplace else (store,)
        row = read(f"SELECT COUNT(*) AS records, MAX({stamp}) AS updated_at {period} "
                   f"FROM {table} WHERE store_slug=? {clause}", values)[0]
        result.append({"source": table, **row})
    return result


def envelope(query, payload, warnings=(), sources=()):
    return clean({"store": query.store, "marketplace": query.marketplace, "currency": "RUB",
                  "timezone": "Europe/Moscow", "generated_at": datetime.now(UTC).isoformat(),
                  "date_from": query.date_from, "date_to": query.date_to,
                  "sources": sources, "warnings": list(warnings), **payload})
