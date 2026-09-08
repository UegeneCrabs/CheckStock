"""Yandex Market catalog in the shared unit-economics layout, before calculations exist."""

from app.stores import STORES

MARKETPLACE = "YANDEX MARKET"


def catalog_product(store_slug: str, product: dict) -> dict:
    store = STORES[store_slug]
    article = str(product["article"])
    return {
        "id": f"yandex:{store_slug}:{article}",
        "marketplace": MARKETPLACE,
        "store_slug": store_slug,
        "store_name": store["name"],
        "article": article,
        "barcode": product.get("barcode") or None,
        "name": product.get("name") or article,
        "image_url": product.get("image_url") or None,
        "mp_sku": product.get("mp_sku") or None,
        "mp_product_id": product.get("mp_product_id") or None,
        "rating": None,
        "reviews_count": None,
        "is_new": None,
        "sales_days": None,
        "price": dict.fromkeys(("current", "with_spp", "with_wallet")),
        "current_economics": dict.fromkeys(("margin", "roi", "orders", "buyout_percent", "advertising_spend", "period_to")),
        "economics_7d": dict.fromkeys(("turnover", "margin", "roi")),
        "advertising": dict.fromkeys(("drr", "spend", "ctr", "cpc", "orders_amount", "period_from", "period_to")),
        "tag_data": dict.fromkeys(("goal_week", "goal_day", "status", "ends", "code", "fact", "plan")),
        "stock": dict.fromkeys(("total", "fbs", "fbo", "fulfillment", "days", "state", "period_days", "orders_21d", "average_daily_orders")),
        "details": dict.fromkeys((
            "retail_price", "customer_price", "commission_percent", "logistics", "drr",
            "buyout_percent", "purchase_cost", "fulfillment_cost", "acquiring", "storage",
            "tax", "volume_l", "weight_kg", "category",
        )),
        "history": None,
    }
