from datetime import UTC, date, datetime, time, timedelta

from app import db
from app.domain import MARKETPLACES, MOSCOW_TIMEZONE
from app.stores import STORES

VIEW_KINDS = {
    "summary": None,
    "deliveries": {"delivery", "manual_add"},
    "transfers": {"transfer"},
    "shipments": {"shipment"},
    "fbs_sales": set(),
}


def _empty_metric() -> dict:
    return {
        "operations": 0,
        "positions": 0,
        "units": 0,
        "cost": 0.0,
        "missing_units": 0,
    }


def _add_items(metric: dict, items: list[dict], *, count_operation: bool = True) -> None:
    if count_operation:
        metric["operations"] += 1
    metric["positions"] += len(items)
    for item in items:
        quantity = int(item.get("quantity") or 0)
        metric["units"] += quantity
        item_cost = item.get("purchase_cost")
        if item_cost is None:
            metric["missing_units"] += abs(quantity)
        else:
            metric["cost"] += float(item_cost)
    metric["cost"] = round(float(metric["cost"]), 2)


def _price_indexes(rows: list[dict]) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float]]:
    by_article: dict[tuple[str, str], float] = {}
    by_barcode: dict[tuple[str, str], float] = {}
    for row in rows:
        price = row.get("purchase_price")
        if price is None:
            continue
        store_slug = str(row.get("store_slug") or "")
        article = str(row.get("article") or "")
        barcode = str(row.get("barcode") or "")
        if article:
            by_article[(store_slug, article)] = float(price)
        if barcode:
            by_barcode.setdefault((store_slug, barcode), float(price))
    return by_article, by_barcode


def _enrich_item(
    raw: dict,
    store_slug: str,
    by_article: dict[tuple[str, str], float],
    by_barcode: dict[tuple[str, str], float],
) -> dict:
    item = dict(raw)
    article = str(item.get("article") or "")
    barcode = str(item.get("barcode") or "")
    quantity = int(item.get("quantity") or 0)
    purchase_price = by_article.get((store_slug, article))
    if purchase_price is None and barcode:
        purchase_price = by_barcode.get((store_slug, barcode))
    item["article"] = article
    item["barcode"] = barcode
    item["quantity"] = quantity
    item["purchase_price"] = purchase_price
    item["purchase_cost"] = round(float(purchase_price) * quantity, 2) if purchase_price is not None else None
    return item


def _operation_label(operation: dict) -> str:
    if operation.get("kind") == "shipment" and operation.get("is_fbs_transfer"):
        return "Перемещение на FBS"
    return db.OPERATION_LABELS.get(str(operation.get("kind") or ""), str(operation.get("kind") or ""))


def _route_label(fulfillment: str | None, marketplace: str | None, fallback: str) -> str:
    parts = [str(value) for value in (fulfillment, marketplace) if value]
    return " / ".join(parts) or fallback


def _enrich_operation(
    raw: dict,
    by_article: dict[tuple[str, str], float],
    by_barcode: dict[tuple[str, str], float],
) -> dict:
    operation = dict(raw)
    store_slug = str(operation.get("store_slug") or "")
    operation["is_fbs_transfer"] = bool(int(operation.get("is_fbs_transfer") or 0))
    operation["items"] = [
        _enrich_item(item, store_slug, by_article, by_barcode) for item in operation.get("items", [])
    ]
    operation["positions"] = len(operation["items"])
    operation["units"] = sum(int(item["quantity"]) for item in operation["items"])
    operation["purchase_cost"] = round(
        sum(float(item["purchase_cost"]) for item in operation["items"] if item["purchase_cost"] is not None),
        2,
    )
    operation["missing_units"] = sum(
        abs(int(item["quantity"])) for item in operation["items"] if item["purchase_cost"] is None
    )
    operation["label"] = _operation_label(operation)
    operation["from_label"] = _route_label(
        operation.get("from_fulfillment"),
        operation.get("from_marketplace"),
        "Поставка",
    )
    operation["to_label"] = _route_label(
        operation.get("to_fulfillment"),
        operation.get("to_marketplace"),
        "Наружу" if operation.get("kind") == "shipment" else "—",
    )
    return operation


def _period_boundaries(date_from: date, date_to: date) -> tuple[str, str, str, str]:
    local_start = datetime.combine(date_from, time.min, tzinfo=MOSCOW_TIMEZONE)
    local_end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=MOSCOW_TIMEZONE)
    return (
        local_start.astimezone(UTC).isoformat(),
        local_end.astimezone(UTC).isoformat(),
        local_start.isoformat(),
        local_end.isoformat(),
    )


def _summary_rows(store_slugs: tuple[str, ...], marketplaces: tuple[str, ...]) -> list[dict]:
    return [
        {
            "store_slug": store_slug,
            "store_name": STORES[store_slug]["name"],
            "marketplace": marketplace,
            "deliveries": _empty_metric(),
            "moved_out": _empty_metric(),
            "moved_in": _empty_metric(),
            "shipped": _empty_metric(),
            "fbs_sales": _empty_metric(),
            "fbs_formula": None,
        }
        for store_slug in store_slugs
        for marketplace in marketplaces
    ]


def _build_reconciliation(
    store_slugs: tuple[str, ...],
    marketplaces: tuple[str, ...],
    date_from: date,
    date_to: date,
    operations: list[dict],
    by_article: dict[tuple[str, str], float],
    by_barcode: dict[tuple[str, str], float],
) -> list[dict]:
    start_day = (date_from - timedelta(days=1)).isoformat()
    end_day = date_to.isoformat()
    snapshot_rows, coverage = db.get_fbs_stock_snapshots(store_slugs, (start_day, end_day))
    snapshots: dict[tuple[str, str, str, str], dict] = {}
    for row in snapshot_rows:
        snapshots[
            (
                str(row["store_slug"]),
                str(row["marketplace"]),
                str(row["article"]),
                str(row["day"]),
            )
        ] = dict(row)

    inbound: dict[tuple[str, str, str], dict] = {}
    for operation in operations:
        if operation.get("kind") != "shipment" or not operation.get("is_fbs_transfer"):
            continue
        store_slug = str(operation.get("store_slug") or "")
        marketplace = str(operation.get("from_marketplace") or "")
        for item in operation["items"]:
            key = (store_slug, marketplace, str(item["article"]))
            target = inbound.setdefault(
                key,
                {
                    "quantity": 0,
                    "barcode": item.get("barcode") or "",
                    "name": item.get("name") or "",
                },
            )
            target["quantity"] += int(item["quantity"])

    result = []
    for store_slug in store_slugs:
        for marketplace in marketplaces:
            available = (store_slug, marketplace, start_day) in coverage and (
                store_slug,
                marketplace,
                end_day,
            ) in coverage
            articles = {
                article
                for current_store, current_marketplace, article, _day in snapshots
                if current_store == store_slug and current_marketplace == marketplace
            }
            articles.update(
                article
                for current_store, current_marketplace, article in inbound
                if current_store == store_slug and current_marketplace == marketplace
            )
            items = []
            if available:
                for article in sorted(articles):
                    start = snapshots.get((store_slug, marketplace, article, start_day), {})
                    end = snapshots.get((store_slug, marketplace, article, end_day), {})
                    moved = inbound.get((store_slug, marketplace, article), {})
                    start_quantity = int(start.get("quantity") or 0)
                    moved_quantity = int(moved.get("quantity") or 0)
                    end_quantity = int(end.get("quantity") or 0)
                    quantity = start_quantity + moved_quantity - end_quantity
                    metadata = moved or start or end
                    enriched = _enrich_item(
                        {
                            "article": article,
                            "barcode": metadata.get("barcode") or "",
                            "name": metadata.get("name") or "",
                            "quantity": quantity,
                        },
                        store_slug,
                        by_article,
                        by_barcode,
                    )
                    enriched.update(
                        {
                            "start_quantity": start_quantity,
                            "moved_quantity": moved_quantity,
                            "end_quantity": end_quantity,
                        }
                    )
                    if quantity or start_quantity or moved_quantity or end_quantity:
                        items.append(enriched)
            metric = _empty_metric()
            if available:
                _add_items(metric, items, count_operation=False)
            result.append(
                {
                    "store_slug": store_slug,
                    "store_name": STORES[store_slug]["name"],
                    "marketplace": marketplace,
                    "available": available,
                    "start_day": start_day,
                    "end_day": end_day,
                    "start_units": sum(int(item["start_quantity"]) for item in items),
                    "moved_units": sum(int(item["moved_quantity"]) for item in items),
                    "end_units": sum(int(item["end_quantity"]) for item in items),
                    "metric": metric,
                    "items": items,
                }
            )
    return result


def build_report(
    store_slugs: tuple[str, ...],
    date_from: date,
    date_to: date,
    marketplaces: tuple[str, ...] | None = None,
) -> dict:
    marketplaces = marketplaces or tuple(MARKETPLACES)
    operation_from, operation_to, sales_from, sales_to = _period_boundaries(date_from, date_to)
    price_rows = db.get_purchase_price_rows(store_slugs)
    by_article, by_barcode = _price_indexes(price_rows)
    operations = [
        _enrich_operation(operation, by_article, by_barcode)
        for operation in db.get_operations_with_items_for_period(
            store_slugs,
            operation_from,
            operation_to,
        )
    ]
    operations = [
        operation
        for operation in operations
        if operation.get("from_marketplace") in marketplaces
        or operation.get("to_marketplace") in marketplaces
    ]

    fbs_sales = [
        _enrich_item(row, str(row.get("store_slug") or ""), by_article, by_barcode)
        | {
            "store_slug": str(row.get("store_slug") or ""),
            "marketplace": str(row.get("marketplace") or ""),
        }
        for row in db.get_fbs_sales_for_period(store_slugs, sales_from, sales_to)
        if row.get("marketplace") in marketplaces
    ]

    summary = _summary_rows(store_slugs, marketplaces)
    summary_by_key = {(row["store_slug"], row["marketplace"]): row for row in summary}
    for operation in operations:
        kind = operation.get("kind")
        if kind in {"delivery", "manual_add"}:
            key = (operation["store_slug"], operation.get("to_marketplace"))
            if key in summary_by_key:
                _add_items(summary_by_key[key]["deliveries"], operation["items"])
        elif kind == "transfer":
            out_key = (operation["store_slug"], operation.get("from_marketplace"))
            in_key = (operation["store_slug"], operation.get("to_marketplace"))
            if out_key in summary_by_key:
                _add_items(summary_by_key[out_key]["moved_out"], operation["items"])
            if in_key in summary_by_key:
                _add_items(summary_by_key[in_key]["moved_in"], operation["items"])
        elif kind == "shipment":
            key = (operation["store_slug"], operation.get("from_marketplace"))
            if key not in summary_by_key:
                continue
            target = "moved_in" if operation.get("is_fbs_transfer") else "shipped"
            _add_items(summary_by_key[key][target], operation["items"])

    sales_by_key: dict[tuple[str, str], list[dict]] = {}
    for item in fbs_sales:
        sales_by_key.setdefault((item["store_slug"], item["marketplace"]), []).append(item)
    for key, items in sales_by_key.items():
        if key in summary_by_key:
            _add_items(summary_by_key[key]["fbs_sales"], items, count_operation=False)

    reconciliation = _build_reconciliation(
        store_slugs,
        marketplaces,
        date_from,
        date_to,
        operations,
        by_article,
        by_barcode,
    )
    for item in reconciliation:
        summary_by_key[(item["store_slug"], item["marketplace"])]["fbs_formula"] = item

    return {
        "date_from": date_from,
        "date_to": date_to,
        "store_slugs": store_slugs,
        "marketplaces": marketplaces,
        "operations": operations,
        "fbs_sales": fbs_sales,
        "summary": summary,
        "reconciliation": reconciliation,
    }


def operations_for_view(report: dict, view: str) -> list[dict]:
    kinds = VIEW_KINDS.get(view, VIEW_KINDS["summary"])
    if kinds is None:
        return list(report["operations"])
    return [operation for operation in report["operations"] if operation.get("kind") in kinds]


def fbs_sales_for_view(report: dict, view: str) -> list[dict]:
    return list(report["fbs_sales"]) if view in {"summary", "fbs_sales"} else []
