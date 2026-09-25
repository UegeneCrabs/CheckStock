"""Shared report aggregation for marketplace-specific source rows."""

from app.economics.completeness import status
from app.economics.wb import calculations as unit_economics_1c


def _aggregate_report_daily_calculations(rows: list[dict]) -> list[dict]:
    dates = sorted(
        {
            str(item.get("date"))
            for row in rows
            for item in row.get("daily_calculations") or []
            if item.get("date")
        }
    )
    unit_fields = (
        "vat_percent",
        "usn_percent",
        "customer_price",
        "retail_price",
        "acquiring_percent",
        "logistics",
        "storage",
        "commission_percent",
        "team_commission_percent",
        "fulfillment_cost",
        "purchase_price",
        "net_profit",
        "net_revenue",
        "vat_value",
        "usn_value",
    )
    result: list[dict] = []
    for day_key in dates:
        items = [
            item
            for row in rows
            for item in row.get("daily_calculations") or []
            if str(item.get("date")) == day_key
        ]
        orders_count = sum(int(item.get("orders_count") or 0) for item in items)
        expected_buyouts = round(sum(float(item.get("expected_buyouts") or 0) for item in items), 2)
        available_items = [item for item in items if item.get("available")]
        aggregate = {
            "date": day_key,
            "available": bool(available_items),
            "complete": all(item.get("complete", item.get("available", False)) for item in items),
            "messages": list(dict.fromkeys(message for item in items for message in item.get("messages", []))),
            "missing": list(dict.fromkeys(key for item in items for key in item.get("missing", []))),
            "covered_products": len(available_items),
            "product_count": len(items),
            "snapshot_available": all(bool(item.get("snapshot_available")) for item in items),
            "advertising_spend": round(
                sum(float(item.get("advertising_spend") or 0) for item in items),
                2,
            ),
            "orders_count": orders_count,
            "net_orders_count": sum(int(item.get("net_orders_count") or 0) for item in items),
            "expected_buyouts": expected_buyouts,
            "buyout_percent": (
                round(
                    sum(
                        float(item.get("buyout_percent") or 0) * int(item.get("orders_count") or 0)
                        for item in items
                    )
                    / orders_count,
                    2,
                )
                if orders_count
                else None
            ),
            "advertising_per_unit": (
                round(
                    sum(float(item.get("advertising_spend") or 0) for item in items) / expected_buyouts,
                    2,
                )
                if expected_buyouts
                else 0.0
            ),
        }
        for field in unit_fields:
            weighted = [
                (
                    float(item[field]),
                    float(item.get("expected_buyouts") or 0) or 1.0,
                )
                for item in items
                if item.get(field) is not None
                and (field not in {"net_profit", "net_revenue"} or (item.get("expected_buyouts") or 0) > 0)
            ]
            aggregate[field] = (
                round(
                    sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted),
                    2,
                )
                if weighted
                else None
            )
        for field in ("orders_count", "advertising_spend", "expected_buyouts", "net_orders_count", "day_profit", "day_purchase_value"):
            values = [item[field] for item in items if item.get(field) is not None]
            aggregate[field] = round(sum(values), 2) if values else None
        weights = [(item["buyout_percent"], item["orders_count"]) for item in items
                   if item.get("buyout_percent") is not None and item.get("orders_count") is not None]
        weight = sum(count for _, count in weights)
        aggregate["buyout_percent"] = round(sum(percent * count for percent, count in weights) / weight, 2) if weight else None
        spend, bought = aggregate["advertising_spend"], aggregate["expected_buyouts"]
        aggregate["advertising_per_unit"] = round(spend / bought, 2) if spend is not None and bought else 0.0 if spend == 0 else None
        aggregate["status"] = status(aggregate.get("day_profit"), not aggregate["complete"])
        if len(available_items) != len(items):
            aggregate["messages"].append(f"В сумму дня вошло товаров: {len(available_items)} из {len(items)}")
        result.append(aggregate)
    return result


def _unit_profit_report_totals(rows: list[dict]) -> dict:
    margin_complete = all(bool(row.get("margin_complete")) and row.get("margin") is not None for row in rows)
    margin_rows = [row for row in rows if row.get("margin") is not None]
    purchase_rows = [row for row in rows if row.get("purchase_value") is not None]
    margin_available = bool(margin_rows) or not rows
    purchase_available = bool(purchase_rows) or not rows
    totals = {
        "orders_count": sum(int(row.get("orders_count") or 0) for row in rows),
        "orders_amount": round(sum(float(row.get("orders_amount") or 0) for row in rows), 2),
        "cancel_count": sum(int(row.get("cancel_count") or 0) for row in rows),
        "cancel_amount": round(sum(float(row.get("cancel_amount") or 0) for row in rows), 2),
        "net_orders_count": sum(int(row.get("net_orders_count") or 0) for row in rows),
        "net_orders_amount": round(sum(float(row.get("net_orders_amount") or 0) for row in rows), 2),
        "buyout_count": (
            sum(int(row.get("buyout_count") or 0) for row in rows)
            if any(row.get("buyout_count") is not None for row in rows)
            else None
        ),
        "buyout_amount": (
            round(sum(float(row.get("buyout_amount") or 0) for row in rows), 2)
            if any(row.get("buyout_amount") is not None for row in rows)
            else None
        ),
        "buyout_orders_count": sum(int(row.get("buyout_orders_count") or 0) for row in rows),
        "expected_buyout_amount": round(
            sum(float(row.get("expected_buyout_amount") or 0) for row in rows),
            2,
        ),
        "stock": sum(int(row.get("stock") or 0) for row in rows),
        "stock_fbs": sum(int(row.get("stock_fbs") or 0) for row in rows),
        "stock_fbo": sum(int(row.get("stock_fbo") or 0) for row in rows),
        "stock_fulfillment": sum(int(row.get("stock_fulfillment") or 0) for row in rows),
        "stock_average_daily_orders": sum(float(row.get("stock_average_daily_orders") or 0) for row in rows),
        "impressions": sum(int(row.get("impressions") or 0) for row in rows),
        "clicks": sum(int(row.get("clicks") or 0) for row in rows),
        "advertising_spend": round(
            sum(float(row.get("advertising_spend") or 0) for row in rows),
            2,
        ),
        "margin": (round(sum(float(row["margin"]) for row in margin_rows), 2) if margin_available else None),
        "margin_orders_count": round(
            sum(float(row.get("margin_orders_count") or 0) for row in rows),
            2,
        ),
        "purchase_value": (
            round(sum(float(row["purchase_value"]) for row in purchase_rows), 2)
            if purchase_available
            else None
        ),
        "margin_complete": margin_complete,
        "covered_products": len(margin_rows), "product_count": len(rows),
        "messages": list(dict.fromkeys(message for row in rows for message in row.get("messages", []))),
        "unavailable_products": [str(row.get("store_name") or row.get("store_slug") or "") + " / " + str(row.get("article") or row.get("name") or "") for row in rows if row.get("margin") is None],
        "margin_missing_days": sorted({day for row in rows for day in row.get("margin_missing_days") or []}),
        "unavailable_days": sorted({day for row in rows for day in row.get("unavailable_days") or []}),
    }
    totals["stock_days"] = unit_economics_1c.calculate_stock_coverage_days(
        totals["stock"],
        totals["stock_average_daily_orders"],
        period_days=1,
    )
    totals["buyout_percent"] = (
        round(
            sum(
                float(row.get("buyout_percent") or 0) * int(row.get("buyout_orders_count") or 0)
                for row in rows
                if row.get("buyout_percent") is not None
            )
            / totals["buyout_orders_count"],
            2,
        )
        if totals["buyout_orders_count"]
        else None
    )
    totals["ctr"] = round(totals["clicks"] / totals["impressions"] * 100, 2) if totals["impressions"] else 0.0
    totals["cpc"] = round(totals["advertising_spend"] / totals["clicks"], 2) if totals["clicks"] else 0.0
    totals["drr"] = (
        round(totals["advertising_spend"] / totals["expected_buyout_amount"] * 100, 2)
        if totals["expected_buyout_amount"]
        else 100.0
        if totals["advertising_spend"]
        else 0.0
    )
    bases = [row.get("roi_purchase_value", row.get("purchase_value")) for row in margin_rows]
    basis = sum(bases) if bases and all(v is not None for v in bases) else None
    totals["roi_purchase_value"] = basis
    totals["roi"] = round(totals["margin"] / basis * 100, 2) if totals["margin"] is not None and basis else None
    totals["status"] = status(totals["margin"], not margin_complete)
    totals["purchase_complete"] = all(row.get("purchase_complete", row.get("purchase_value") is not None) for row in rows)
    if totals["unavailable_products"]:
        totals["messages"].append(f"В сумму вошло товаров: {len(margin_rows)} из {len(rows)}. Не рассчитаны: " + ", ".join(totals["unavailable_products"]))
    for field in ("orders_count", "orders_amount", "cancel_count", "cancel_amount", "net_orders_count", "net_orders_amount", "advertising_spend", "expected_buyout_amount", "clicks", "impressions"):
        if rows and all(row.get(field) is None for row in rows):
            totals[field] = None
    if totals["advertising_spend"] is None or totals["expected_buyout_amount"] is None:
        totals["drr"] = None
    if totals["advertising_spend"] is None or totals["clicks"] is None:
        totals["cpc"] = None
    if totals["clicks"] is None or totals["impressions"] is None:
        totals["ctr"] = None
    return totals


def _unit_profit_category_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        subject = str(row.get("subject") or "Без предмета").strip() or "Без предмета"
        grouped.setdefault(subject, []).append(row)
    result: list[dict] = []
    for subject, products in grouped.items():
        totals = _unit_profit_report_totals(products)
        store_names = sorted({str(row.get("store_name") or "") for row in products if row.get("store_name")})
        store_slugs = sorted({str(row.get("store_slug") or "") for row in products if row.get("store_slug")})
        managers = sorted({str(row.get("manager") or "") for row in products if row.get("manager")})
        result.append(
            {
                **totals,
                "row_kind": "category",
                "name": subject,
                "article": None,
                "image_url": "",
                "subject": subject,
                "product_count": len(products),
                "store_slug": store_slugs[0] if len(store_slugs) == 1 else "all",
                "store_name": (store_names[0] if len(store_names) == 1 else f"{len(store_names)} магазинов"),
                "manager": (
                    managers[0] if len(managers) == 1 else f"{len(managers)} менеджеров" if managers else None
                ),
                "daily_calculations": _aggregate_report_daily_calculations(products),
            }
        )
    result.sort(key=lambda row: (-float(row.get("orders_amount") or 0), str(row["name"]).casefold()))
    return result
