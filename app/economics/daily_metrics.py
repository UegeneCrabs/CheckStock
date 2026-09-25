"""Refresh only dated metrics after normal source syncs, without network or current costs."""

from collections import defaultdict

from app import db
from app.repositories import daily_economics as repository
from app.repositories.economics_coverage import wb_days


def refresh_wb(stores, start, end):
    records = repository.records("WB", stores, start, end)
    if not records:
        return 0
    coverage = wb_days(stores, start, end)
    orders = defaultdict(list)
    ads = defaultdict(list)
    for row in db.get_unit_economics_1c_funnel_daily_order_rows(stores, start, end):
        orders[row["store_slug"], str(row["article"]).partition(" / ")[0], row["day"]].append(row)
    for row in db.get_unit_economics_1c_daily_advertising(stores, start, end):
        ads[row["store_slug"], str(row["nm_id"]), row["day"]].append(row)
    updated = 0
    for record in records:
        store, day, article = record["store_slug"], record["day"], record["article"]
        identity = (store, article.partition(" / ")[0], day)
        source = record["source"]
        values = dict(source["values"])
        changes = {}
        if day in coverage[store]["orders"]:
            changes["orders_count"] = sum(int(r.get("orders_count") or 0) for r in orders[identity])
        if day in coverage[store]["advertising"]:
            changes["advertising_spend"] = round(sum(float(r.get("spend") or 0) for r in ads[identity]), 2)
        if not any(values.get(k) != v for k, v in changes.items()):
            continue
        raw = {"received_at": repository.now(), "orders": orders[identity], "advertising": ads[identity]}
        # refresh_metrics preserves the original source and all manual overlays. The
        # recalculation uses only these historical inputs, in the same transaction.
        updated += int(repository.refresh_metrics(("WB", store, article, day), changes, raw))
    return updated
