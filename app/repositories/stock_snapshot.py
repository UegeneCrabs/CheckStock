"""Publish complete stock schemes atomically after all network reads finish."""

from collections.abc import Iterable, Mapping

from app.dto.yandex_stock import YandexWarehouseQuantities
from app.repositories.core import WRITE_LOCK, get_connection


def replace_snapshot(
    store_slug: str,
    marketplace: str,
    totals: Mapping[str, Mapping[str, int]],
    warehouses: Mapping[str, Iterable[tuple[str, str, str | None, int]]],
    updated_at: str,
    *,
    remove_variants: Iterable[str] = (),
    yandex_quantities: Mapping[str, Mapping[tuple[str, str], YandexWarehouseQuantities]] | None = None,
) -> None:
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
            details = (yandex_quantities or {}).get(scheme, {})
            warehouse_rows = []
            for article, warehouse, cluster, quantity in warehouses.get(scheme, ()):
                stock = details.get((article, warehouse))
                if quantity or stock is not None:
                    warehouse_rows.append(
                        (
                            store_slug,
                            marketplace,
                            scheme,
                            article,
                            warehouse,
                            cluster,
                            quantity,
                            updated_at,
                            stock.available_for_order if stock is not None else None,
                            stock.reserved if stock is not None else None,
                        )
                    )
            conn.executemany(
                "INSERT INTO mp_warehouse_stock "
                "(store_slug,marketplace,scheme,article,warehouse,cluster,quantity,updated_at,"
                "available_for_order,reserved) VALUES (?,?,?,?,?,?,?,?,?,?)",
                warehouse_rows,
            )
        for scheme in remove_variants:
            for table in ("mp_stock", "mp_warehouse_stock"):
                conn.execute(
                    f"DELETE FROM {table} WHERE store_slug=? AND marketplace=? "
                    "AND scheme LIKE ? AND scheme<>?",
                    (store_slug, marketplace, scheme + "_%", scheme),
                )
        conn.commit()
