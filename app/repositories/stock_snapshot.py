"""Publish complete stock schemes atomically after all network reads finish."""

from app.repositories.core import WRITE_LOCK, get_connection


def replace_snapshot(store_slug, marketplace, totals, warehouses, updated_at, *, remove_variants=()):
    with WRITE_LOCK, get_connection() as conn:
        for scheme, quantities in totals.items():
            for table in ("mp_stock", "mp_warehouse_stock"):
                conn.execute(
                    f"DELETE FROM {table} WHERE store_slug=? AND marketplace=? AND scheme=?",
                    (store_slug, marketplace, scheme),
                )
            conn.executemany(
                "INSERT INTO mp_stock (store_slug,marketplace,scheme,article,quantity,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (store_slug, marketplace, scheme, article, qty, updated_at)
                    for article, qty in quantities.items()
                    if qty
                ],
            )
            conn.executemany(
                "INSERT INTO mp_warehouse_stock "
                "(store_slug,marketplace,scheme,article,warehouse,cluster,quantity,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [
                    (store_slug, marketplace, scheme, article, warehouse, cluster, qty, updated_at)
                    for article, warehouse, cluster, qty in warehouses.get(scheme, [])
                    if qty
                ],
            )
        for scheme in remove_variants:
            for table in ("mp_stock", "mp_warehouse_stock"):
                conn.execute(
                    f"DELETE FROM {table} WHERE store_slug=? AND marketplace=? "
                    "AND scheme LIKE ? AND scheme<>?",
                    (store_slug, marketplace, scheme + "_%", scheme),
                )
        conn.commit()
