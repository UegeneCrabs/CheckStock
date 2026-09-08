from app.repositories.core import get_connection


def source_states(store_slugs: tuple[str, ...]) -> dict[str, dict[str, dict]]:
    if not store_slugs:
        return {}
    placeholders = ", ".join("?" for _ in store_slugs)
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT store_slug, scope, ok FROM sync_health "
            f"WHERE marketplace='WB' AND store_slug IN ({placeholders})",
            store_slugs,
        ).fetchall()
        advertising = connection.execute(
            f"SELECT store_slug, status FROM unit_economics_1c_wb_advertising_sync_state "
            f"WHERE marketplace='WB' AND store_slug IN ({placeholders})",
            store_slugs,
        ).fetchall()
        funnel = connection.execute(
            f"SELECT store_slug, status FROM wb_funnel_orders_sync_state "
            f"WHERE store_slug IN ({placeholders})", store_slugs,
        ).fetchall()
        fulfillment = connection.execute(
            f"SELECT DISTINCT store_slug FROM ff_stock_deliveries "
            f"WHERE marketplace='WB' AND store_slug IN ({placeholders})", store_slugs,
        ).fetchall()
    result = {store: {} for store in store_slugs}
    for row in rows:
        result[row["store_slug"]][row["scope"]] = dict(row)
    for row in fulfillment:
        result[row["store_slug"]].setdefault("ff", {"ok": True})
    for scope, states, success in (
        ("unit_economics_1c_advertising", advertising, "ok"),
        ("unit_economics_1c_funnel", funnel, "success"),
    ):
        for row in states:
            result[row["store_slug"]].setdefault(scope, {"ok": row["status"] == success})
    return result
