"""Read WB product identities and daily funnel facts without changing source data."""

from app.repositories.core import get_connection


def products() -> list[dict]:
    with get_connection() as conn:
        items = [
            dict(row)
            for row in conn.execute(
                "SELECT store_slug, article FROM stock_items WHERE marketplace='WB' AND is_service=0"
            )
        ]
        by_key = {(item["store_slug"], item["article"]): item for item in items}
        for alias in conn.execute(
            "SELECT store_slug, article, target_article FROM catalog_article_aliases WHERE marketplace='WB'"
        ):
            item = by_key.get((alias["store_slug"], alias["target_article"]))
            if item is not None:
                item.setdefault("article_aliases", []).append(alias["article"])
    return items


def daily_facts(stores: tuple[str, ...], start: str, end: str) -> tuple[dict, dict]:
    """A missing product row means zero only on a proven complete store/day load."""
    facts = {}
    coverage = {store: set() for store in stores}
    if not stores:
        return facts, coverage
    marks = ",".join("?" for _ in stores)
    params = (*stores, start, end)
    with get_connection() as conn:
        for row in conn.execute(
            f"""SELECT store_slug, article, day, orders_count, cancel_count, source_version
                FROM wb_funnel_daily_orders WHERE store_slug IN ({marks}) AND day>=? AND day<=?
                UNION ALL
                SELECT store_slug, NULL AS article, day, 0 AS orders_count, 0 AS cancel_count,
                       4 AS source_version FROM economics_source_days WHERE marketplace='WB'
                AND source='orders' AND store_slug IN ({marks}) AND day>=? AND day<=?""",
            (*params, *params),
        ):
            # One statement sees matching facts and coverage even during a concurrent funnel load.
            if row["article"] is None:
                coverage[row["store_slug"]].add(row["day"])
                continue
            key = (row["store_slug"], row["article"], row["day"])
            # v1/v2 rows do not prove that cancellation data is available.
            facts[key] = (
                int(row["orders_count"]) - int(row["cancel_count"]) if row["source_version"] >= 3 else None
            )
            if row["source_version"] >= 4:
                coverage[row["store_slug"]].add(row["day"])
    return facts, coverage
