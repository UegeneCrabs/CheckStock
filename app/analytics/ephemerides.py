"""Two calendar weeks of saved WB data. No collection or writes on GET."""

import json
from datetime import date, datetime, timedelta

from app import db
from app.analytics import analyzer
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.repositories import ephemerides as repository
from app.repositories.economics_coverage import wb_days


def monday(day):
    return day - timedelta(days=day.weekday())


def week_info(day):
    year, number, _ = day.isocalendar()
    return {
        "start": day.isoformat(),
        "end": (day + timedelta(days=6)).isoformat(),
        "year": year,
        "number": number,
    }


def catalog(stores, user):
    references = analyzer.index(db.get_unit_economics_1c_product_reference_rows(stores))
    result = []
    for store in stores:
        for product in db.get_stock_items(store, "WB"):
            article = str(product["article"])
            reference = references.get((store, article), {})
            if analyzer.permitted(reference.get("manager"), user):
                result.append(
                    {
                        "id": f"{store}:{article}",
                        "store_slug": store,
                        "article": article,
                        "name": product.get("name") or article,
                        "barcode": product.get("barcode") or "",
                        "image": product.get("image_url") or "",
                        "project": STORES[store]["name"],
                        "manager": reference.get("manager") or "",
                        "category": reference.get("category") or "",
                        "code": reference.get("abc_code") or "",
                        "fbs": product.get("fbs_stock"),
                        "fbo": product.get("fbo_stock"),
                        "stockUpdated": product.get("mp_updated_at"),
                    }
                )
    return sorted(result, key=lambda row: (row["project"], row["name"].casefold(), row["article"]))


def attach_comments(rows, stores, start, end):
    permitted = {(row["store_slug"], row["article"]): row for row in rows}
    for row in rows:
        row["comments"] = {}
    for comment in repository.comments(stores, start.isoformat(), end.isoformat()):
        row = permitted.get((comment["store_slug"], comment["article"]))
        if row is not None:
            row["comments"][comment["week"] + ":" + comment["kind"]] = comment


def history(stores, week, user):
    weeks = [week_info(week - timedelta(weeks=i)) for i in range(12)]
    rows = catalog(stores, user)
    attach_comments(rows, stores, date.fromisoformat(weeks[-1]["start"]), week)
    return {"ok": True, "rows": rows, "weeks": weeks}


def load(stores, week, user, *, today=None):
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    week = monday(week)
    previous = week - timedelta(days=7)
    end = min(today, week + timedelta(days=6))
    # Reuse the analyzer's established ROI/DRR/coverage rules, without its daily notes.
    comparisons = [
        analyzer.load(stores, start, user, today=today, include_notes=False) for start in (previous, week)
    ]
    source_rows = [{row["id"]: row for row in item["rows"]} for item in comparisons]
    rows = catalog(stores, user)
    prices, references = repository.dated_sources(stores, previous.isoformat(), end.isoformat())
    prices = analyzer.daily_index(prices)
    references = analyzer.daily_index(references)
    orders = analyzer.daily_index(
        db.get_unit_economics_1c_funnel_daily_order_rows(stores, previous.isoformat(), end.isoformat())
    )
    coverage = wb_days(stores, previous.isoformat(), end.isoformat())
    transit = repository.transit(stores)
    for row in rows:
        key = (row["store_slug"], row["article"])
        nm_key = (key[0], key[1].partition(" / ")[0].strip())
        row["nm_id"] = nm_key[1]
        returned = transit.get(nm_key, {})
        row["fromCustomer"] = returned.get("from_customer")
        row["transitUpdated"] = returned.get("updated_at")
        stock = [row["fbs"], row["fbo"], row["fromCustomer"]]
        row["stock"] = analyzer.total(stock)
        row["stockPartial"] = any(value is None for value in stock)
        for idx, name in enumerate(("previous", "current")):
            start = (previous, week)[idx]
            last = min(today, start + timedelta(days=6))
            days = [(start + timedelta(days=i)).isoformat() for i in range((last - start).days + 1)]
            source = source_rows[idx].get(row["id"], {})
            candidates = [price for day, price in prices[key].items() if day in days]
            price = candidates[-1] if candidates else {}
            goals = [
                json.loads(record["reference_json"])
                for day, record in references[key].items()
                if day in days and record.get("reference_json")
            ]
            # Current reference is valid only for the ongoing week.
            goal = (
                source.get("goal")
                if start == monday(today)
                else goals[-1].get("goal_week")
                if goals
                else None
            )
            amounts = [
                orders[nm_key][day].get("orders_amount")
                if day in orders[nm_key]
                else 0
                if day in coverage[key[0]]["orders"]
                else None
                for day in days
            ]
            retail, customer = price.get("retail_price"), price.get("customer_price_with_spp")
            row[name] = {
                "spp": analyzer.ratio(retail - customer, retail) if retail and customer is not None else None,
                "wallet": price.get("customer_price_with_wallet"),
                "price": retail,
                "priceDay": price.get("day"),
                "goal": goal,
                "fact": analyzer.total(source.get("week", [])),
                "turnover": analyzer.total(amounts),
                "impressions": source.get("weights", {}).get("impressions"),
                "ctr": source.get("totalCtr"),
                "roi": source.get("roi"),
                "drr": source.get("drr"),
                "weights": source.get("weights", {}),
                "coverage": {**source.get("coverage", {}), "turnover": sum(v is not None for v in amounts)},
            }
        current = source_rows[1].get(row["id"], {})
        row["rating"] = current.get("rating")
        row["dayTurnover"] = {
            key: current.get(key) for key in ("plan", "fact", "forecast", "difference", "deviation")
        }
    weeks = [week_info(week - timedelta(weeks=i)) for i in range(6)]
    attach_comments(rows, stores, week - timedelta(weeks=5), week)
    return {
        "ok": True,
        "rows": rows,
        "current": week_info(week),
        "previous": week_info(previous),
        "weeks": weeks,
        "today": today.isoformat(),
        "through": end.isoformat(),
        "loaded_at": comparisons[1]["loaded_at"],
    }
