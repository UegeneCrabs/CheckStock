"""Exact dated WB FBO snapshots; absent rows are unknown, not zero."""

from app.repositories.core import get_connection


def snapshots(stores: tuple[str, ...], days: tuple[str, ...]) -> dict:
    if not stores or not days:
        return {}
    store_marks, day_marks = ",".join("?" for _ in stores), ",".join("?" for _ in days)
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT store_slug, article, day, quantity, captured_at
                FROM marketplace_stock_daily_history
                WHERE marketplace='WB' AND scheme='fbo'
                  AND store_slug IN ({store_marks}) AND day IN ({day_marks})""",
            (*stores, *days),
        ).fetchall()
    # Each row already contains the total across WB warehouses for the store/product/day.
    return {
        (row["store_slug"], row["article"], row["day"]): {
            "quantity": int(row["quantity"]),
            "captured_at": row["captured_at"],
        }
        for row in rows
    }
