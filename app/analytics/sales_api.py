"""The 'Sales by API' screen intentionally reports WB funnel ORDERS, not sales.

One row per cabinet and WB nmID, even when the catalog contains several sizes.
No source request or synchronization is started by this read-only report.
"""

from datetime import UTC, date, datetime, timedelta

from app import db
from app.analytics.analyzer import permitted, turnover_sort_key
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.repositories.economics_coverage import wb_days
from app.repositories.sales_api import coverage_bounds, funnel_bounds

MAX_DAYS = 366


def article_key(article):
    return str(article or "").partition(" / ")[0].strip()


def known_total(values):
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def load(stores, user, date_from=None, date_to=None, *, today=None):
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    references = {
        (row["store_slug"], row["article"]): row
        for row in db.get_unit_economics_1c_product_reference_rows(stores)
    }
    products = {}
    catalog_keys = set()
    for store in stores:
        for product in db.get_catalog_items(store, "WB"):
            key = (store, article_key(product["article"]))
            catalog_keys.add(key)
            reference = references.get((store, product["article"]), {})
            if not permitted(reference.get("manager"), user):
                continue
            row = products.setdefault(
                key,
                {
                    "id": f"{store}:{key[1]}",
                    "store_slug": store,
                    "project": STORES[store]["name"],
                    "article": key[1],
                    "name": product.get("name") or key[1],
                    "image": product.get("image_url") or "",
                    "category": reference.get("category") or "",
                    "barcodes": [],
                    "article_aliases": [],
                },
            )
            row["barcodes"] = sorted(
                set(row["barcodes"])
                | set(product.get("barcodes") or [])
                | ({str(product["barcode"])} if product.get("barcode") else set())
            )
            row["article_aliases"] = sorted(
                set(row["article_aliases"])
                | set(product.get("article_aliases") or [])
                | {str(product["article"])}
            )
            if not row["category"]:
                row["category"] = reference.get("category") or ""
            if not row["image"]:
                row["image"] = product.get("image_url") or ""

    bounds = funnel_bounds(stores, today.isoformat())
    # Historical orders remain visible after a product leaves the catalog.
    # Manager-scoped employees only see products with a matching assignment.
    for bound in bounds:
        key = (bound["store_slug"], str(bound["article"]))
        if key not in products and key not in catalog_keys and permitted(None, user):
            products[key] = {
                "id": f"{key[0]}:{key[1]}",
                "store_slug": key[0],
                "project": STORES[key[0]]["name"],
                "article": key[1],
                "name": key[1],
                "image": "",
                "category": "",
                "barcodes": [],
                "article_aliases": [],
            }
    allowed_bounds = [bound for bound in bounds if (bound["store_slug"], str(bound["article"])) in products]
    allowed_bounds.extend(coverage_bounds(tuple({key[0] for key in products}), today.isoformat()))
    first = min((row["first_day"] for row in allowed_bounds), default=None)
    last = max((row["last_day"] for row in allowed_bounds), default=None)
    if date_from is None:
        date_to = min(today, date.fromisoformat(last)) if last else today
        date_from = date_to - timedelta(days=6)
    dates = [(date_from + timedelta(days=i)).isoformat() for i in range((date_to - date_from).days + 1)]
    saved = {}
    latest_update = None
    for record in db.get_unit_economics_1c_funnel_daily_order_rows(stores, dates[0], dates[-1]):
        key = (record["store_slug"], str(record["article"]))
        if key not in products:
            continue
        saved.setdefault(key, {})[record["day"]] = record
        if products[key]["name"] == key[1] and record.get("product_name"):
            products[key]["name"] = record["product_name"]
        if record.get("updated_at"):
            latest_update = max(latest_update or "", str(record["updated_at"]))
    coverage = wb_days(stores, dates[0], dates[-1])
    turnover_day = today.isoformat()
    if turnover_day in dates:
        today_orders = {key: days[turnover_day] for key, days in saved.items() if turnover_day in days}
        today_coverage = coverage
    else:
        today_orders = {
            (record["store_slug"], str(record["article"])): record
            for record in db.get_unit_economics_1c_funnel_daily_order_rows(stores, turnover_day, turnover_day)
        }
        today_coverage = wb_days(stores, turnover_day, turnover_day)
    rows = []
    for key, product in products.items():
        source = saved.get(key, {})
        loaded = coverage[key[0]]["orders"]
        orders, cancels = [], []
        for day in dates:
            record = source.get(day)
            known = record is not None or day in loaded
            orders.append(int((record or {}).get("orders_count") or 0) if known else None)
            cancels.append(int((record or {}).get("cancel_count") or 0) if known else None)
        known_days = sum(value is not None for value in orders)
        rows.append(
            {
                **product,
                "barcode": ", ".join(product["barcodes"]),
                "days": orders,
                "cancellations": cancels,
                "orders": known_total(orders),
                "cancels": known_total(cancels),
                "known_days": known_days,
                "complete": known_days == len(dates),
                "today_turnover": today_orders[key].get("orders_amount")
                if key in today_orders
                else 0
                if turnover_day in today_coverage[key[0]]["orders"]
                else None,
            }
        )
    rows.sort(key=lambda row: turnover_sort_key(row, row["today_turnover"]))
    return {
        "ok": True,
        "rows": rows,
        "dates": dates,
        "date_from": dates[0],
        "date_to": dates[-1],
        "available_from": first,
        "available_to": last,
        "today": today.isoformat(),
        "turnover_day": turnover_day,
        "updated_at": latest_update,
        "loaded_at": datetime.now(UTC).isoformat(),
        "max_days": MAX_DAYS,
    }
