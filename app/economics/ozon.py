"""Ozon catalog, buyer reputation, stock, turnover and 1C source parameters."""

from datetime import date, datetime, timedelta

from app import db
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.economics import ozon_advertising, ozon_turnover
from app.economics.wb import calculations as wb_calculations
from app.exports import stock_sheet_inbound
from app.ozon import reputation as ozon_reputation
from app.repositories import unit_economics_1c as unit_economics_repository
from app.repositories import unit_economics_data_errors

MARKETPLACE = "OZON"


def load_products(
    store_slugs: tuple[str, ...], article: str = "",
    period_from: date | None = None, period_to: date | None = None,
) -> list[dict]:
    period_to = period_to or datetime.now(MOSCOW_TIMEZONE).date() - timedelta(days=1)
    period_from = period_from or period_to - timedelta(days=6)
    products: list[dict] = []
    health = unit_economics_data_errors.source_states(store_slugs, MARKETPLACE)
    source_values = unit_economics_repository.get_ozon_source_values(store_slugs)
    turnover, turnover_errors = ozon_turnover.load(store_slugs, period_from, period_to)
    advertising, advertising_errors = ozon_advertising.load(store_slugs, period_from, period_to)
    stock_period_to = datetime.now(MOSCOW_TIMEZONE).date()
    stock_period_days = 21
    stock_period_from = stock_period_to - timedelta(days=stock_period_days - 1)
    order_counts, order_count_errors = ozon_turnover.load_order_counts(
        store_slugs, stock_period_from, stock_period_to
    )
    period_days = (period_to - period_from).days + 1
    turnover_coverage = {
        "dates": [(period_from + timedelta(days=offset)).isoformat() for offset in range(period_days)],
        "days": period_days,
        "expected_days": period_days,
        "complete": True,
        "missing_dates": [],
    }
    stock_coverage = {
        "dates": [
            (stock_period_from + timedelta(days=offset)).isoformat()
            for offset in range(stock_period_days)
        ],
        "days": stock_period_days,
        "expected_days": stock_period_days,
        "complete": True,
        "missing_dates": [],
    }
    for slug in store_slugs:
        catalog = db.get_catalog_items(slug, MARKETPLACE)
        stocks = {row["article"]: row for row in db.get_stock_items(slug, MARKETPLACE)}
        reputation = ozon_reputation.get_store(slug)
        inbound = stock_sheet_inbound.load(slug, MARKETPLACE, catalog)
        reputation_error = (health.get(slug, {}).get("reputation") or {}).get("error")
        source_error = (health.get(slug, {}).get("unit_economics_1c_source") or {}).get("error")
        ad_source_error = (health.get(slug, {}).get(ozon_advertising.SCOPE) or {}).get("error")
        for item in catalog:
            sku = str(item["article"])
            if article and sku != article:
                continue
            stock = stocks.get(sku)
            fbs = (
                sum(max(int(stock.get(key) or 0), 0) for key in ("fbs_stock", "rfbs_stock"))
                if stock and any(stock.get(key) is not None for key in ("fbs_stock", "rfbs_stock"))
                else None
            )
            fbo = max(int(stock["fbo_stock"]), 0) if stock and stock.get("fbo_stock") is not None else None
            ff = max(int(stock["ff_available"]), 0) if stock and stock.get("ff_available") is not None else None
            total_stock = (
                sum(value or 0 for value in (fbs, fbo, ff))
                if any(value is not None for value in (fbs, fbo, ff)) else None
            )
            stock_orders = order_counts.get((slug, sku), 0) if slug not in order_count_errors else None
            ad_available = slug not in advertising_errors
            ad_metrics = advertising.get((slug, sku)) or {}
            ad_spend = round(float(ad_metrics.get("spend") or 0), 2) if ad_available else None
            ad_click_spend = float(ad_metrics.get("click_spend") or 0)
            ad_impressions = int(ad_metrics.get("impressions") or 0)
            ad_clicks = int(ad_metrics.get("clicks") or 0)
            product_turnover = turnover.get((slug, sku), 0.0) if slug not in turnover_errors else None
            inbound_qty = inbound.quantities.get(sku)
            inbound_partial = inbound_qty is None
            if inbound_partial:
                inbound_qty = inbound.confirmed_quantities.get(sku) or None
            review = reputation.get(str(item.get("mp_sku") or "")) or {}
            source = source_values.get((slug, sku)) or {}
            errors = []
            if fbs is None and fbo is None and ff is None:
                errors.append("Не загружены остатки товара Ozon")
            elif fbs is None or fbo is None:
                errors.append("Остатки Ozon FBS/FBO загружены не полностью")
            if reputation_error:
                errors.append("Отзывы Ozon: " + str(reputation_error))
            elif not review:
                errors.append("Оценки и отзывы Ozon ещё не загружены")
            if source_error:
                errors.append("Данные 1С Ozon: " + str(source_error))
            if not source.get("source_sheet_id"):
                errors.append("Товар не найден в листах OZON таблицы 1С")
            else:
                for key, label in (
                    ("purchase_price", "себестоимость"),
                    ("fulfillment_cost", "затраты на ФФ"),
                    ("team_commission_percent", "комиссия компании (ДРР %)"),
                    ("tag_raw", "тег"),
                ):
                    if source.get(key) is None:
                        errors.append("В данных 1С Ozon не заполнены " + label)
            if not inbound.available:
                errors.append("Нет полного свежего снимка поставок Ozon")
            if turnover_errors.get(slug):
                errors.append(turnover_errors[slug])
            if order_count_errors.get(slug) and order_count_errors[slug] not in errors:
                errors.append(order_count_errors[slug])
            if ad_source_error:
                errors.append("Реклама Ozon: " + str(ad_source_error))
            elif advertising_errors.get(slug):
                errors.append(advertising_errors[slug])
            products.append({
                "id": f"ozon:{slug}:{sku}",
                "marketplace": MARKETPLACE,
                "store_slug": slug,
                "store_name": STORES[slug]["name"],
                "article": sku,
                "barcode": item.get("barcode") or None,
                "name": item.get("name") or sku,
                "image_url": item.get("image_url") or None,
                "mp_sku": item.get("mp_sku") or None,
                "mp_product_id": item.get("mp_product_id") or None,
                "rating": review.get("rating"),
                "reviews_count": review.get("reviews_count"),
                "manager": source.get("manager"),
                "tag": source.get("tag_raw"),
                "tag_data": {
                    "goal_week": source.get("goal_week"),
                    "goal_day": source.get("goal_day"),
                    "status": source.get("stock_status"),
                    "ends": source.get("stock_end_week"),
                    "code": source.get("abc_code"),
                    "fact": source.get("fact_sales"),
                    "plan": source.get("plan_sales"),
                },
                "is_new": None,
                "sales_days": None,
                "price": {"current": None, "with_spp": None, "with_wallet": None},
                "current_economics": dict.fromkeys(("margin", "roi", "orders", "buyout_percent", "advertising_spend", "period_to")),
                "economics_7d": {
                    "turnover": product_turnover,
                    "turnover_coverage": turnover_coverage if slug not in turnover_errors else None,
                    "margin": None,
                    "roi": None,
                    "period_from": period_from.isoformat(),
                    "period_to": period_to.isoformat(),
                },
                "advertising": {
                    "drr": wb_calculations.calculate_drr_percent(ad_spend, product_turnover)
                    if ad_available and product_turnover is not None else None,
                    "spend": ad_spend,
                    "ctr": wb_calculations.money(ad_clicks / ad_impressions * 100)
                    if ad_available and ad_impressions else 0.0 if ad_available else None,
                    "cpc": wb_calculations.money(ad_click_spend / ad_clicks)
                    if ad_available and ad_clicks else 0.0 if ad_available else None,
                    "impressions": ad_impressions if ad_available else None,
                    "clicks": ad_clicks if ad_available else None,
                    "orders_amount": product_turnover,
                    "period_from": period_from.isoformat(),
                    "period_to": period_to.isoformat(),
                    "coverage": turnover_coverage if ad_available else None,
                    "drr_coverage": turnover_coverage if ad_available and product_turnover is not None else None,
                },
                "stock": {
                    "total": total_stock,
                    "fbs": fbs,
                    "fbo": fbo,
                    "fulfillment": ff,
                    "inbound": inbound_qty,
                    "inbound_partial": inbound_partial,
                    "inbound_message": "Поставка Ozon ещё не принята на склад FBO." if inbound.available else "Нет полного свежего снимка поставок Ozon.",
                    "days": wb_calculations.calculate_stock_coverage_days(
                        total_stock, stock_orders, period_days=stock_period_days
                    ) if total_stock is not None and stock_orders is not None else None,
                    "state": None,
                    "period_days": stock_period_days,
                    "orders_21d": stock_orders,
                    "average_daily_orders": round(stock_orders / stock_period_days, 2)
                    if stock_orders is not None else None,
                    "coverage": stock_coverage if stock_orders is not None else None,
                },
                "details": {
                    "purchase_cost": source.get("purchase_price"),
                    "fulfillment_cost": source.get("fulfillment_cost"),
                    "team_commission_percent": source.get("team_commission_percent"),
                    "source_sheet_title": source.get("source_sheet_title"),
                    "source_row": source.get("source_row"),
                    "source_synced_at": source.get("synced_at"),
                },
                "history": None,
                "data_errors": errors,
            })
    return products
