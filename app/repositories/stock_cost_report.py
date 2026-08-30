from app.repositories.core import WRITE_LOCK, get_connection


def get_operations_with_items_for_period(
    store_slugs: tuple[str, ...],
    date_from: str,
    date_to_exclusive: str,
) -> list[dict]:
    if not store_slugs:
        return []

    placeholders = ", ".join("?" for _ in store_slugs)
    connection = get_connection()
    try:
        operation_rows = connection.execute(
            f"""
            SELECT operation.*,
                   COALESCE(flags.is_fbs_transfer, 0) AS is_fbs_transfer
              FROM stock_operations operation
              LEFT JOIN stock_operation_report_flags flags
                ON flags.operation_id=operation.id
             WHERE operation.store_slug IN ({placeholders})
               AND operation.kind IN
                   ('delivery', 'manual_add', 'transfer', 'transfer_dispatch',
                    'transfer_receive', 'transfer_cancel', 'shipment', 'fbs_transfer')
               AND operation.created_at>=? AND operation.created_at<?
             ORDER BY operation.created_at DESC, operation.id DESC
            """,
            (*store_slugs, date_from, date_to_exclusive),
        ).fetchall()
        if not operation_rows:
            return []

        operation_ids = tuple(int(row["id"]) for row in operation_rows)
        item_placeholders = ", ".join("?" for _ in operation_ids)
        item_rows = connection.execute(
            f"""
            SELECT operation_id, article, barcode, name, quantity,
                   purchase_price, purchase_price_recorded
              FROM stock_operation_items
             WHERE operation_id IN ({item_placeholders})
             ORDER BY id
            """,
            operation_ids,
        ).fetchall()
    finally:
        connection.close()

    items_by_operation: dict[int, list[dict]] = {}
    for row in item_rows:
        items_by_operation.setdefault(int(row["operation_id"]), []).append(dict(row))

    operations = []
    for row in operation_rows:
        operation = dict(row)
        operation["items"] = items_by_operation.get(int(operation["id"]), [])
        operations.append(operation)
    return operations


def get_purchase_price_rows(store_slugs: tuple[str, ...]) -> list[dict]:
    if not store_slugs:
        return []

    placeholders = ", ".join("?" for _ in store_slugs)
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT items.store_slug, items.article, items.barcode, items.name,
                   source.purchase_price
              FROM stock_items items
              JOIN unit_economics_1c_source_values source
                ON source.stock_item_id=items.id
             WHERE items.store_slug IN ({placeholders})
               AND items.marketplace='WB'
               AND items.is_service=0
               AND source.purchase_price IS NOT NULL
            """,
            store_slugs,
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def get_fbs_sales_for_period(
    store_slugs: tuple[str, ...],
    date_from: str,
    date_to_exclusive: str,
) -> list[dict]:
    if not store_slugs:
        return []

    placeholders = ", ".join("?" for _ in store_slugs)
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT store_slug, marketplace, article,
                   MAX(COALESCE(barcode, '')) AS barcode,
                   MAX(COALESCE(name, '')) AS name,
                   SUM(sold_quantity) AS quantity
              FROM sales_order_lines
             WHERE store_slug IN ({placeholders})
               AND LOWER(scheme) IN ('fbs', 'rfbs')
               AND sold_at IS NOT NULL
               AND sold_at>=? AND sold_at<?
             GROUP BY store_slug, marketplace, article
            HAVING SUM(sold_quantity)<>0
             ORDER BY store_slug, marketplace, article
            """,
            (*store_slugs, date_from, date_to_exclusive),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def get_fbs_stock_snapshots(
    store_slugs: tuple[str, ...],
    days: tuple[str, ...],
) -> tuple[list[dict], set[tuple[str, str, str]]]:
    if not store_slugs or not days:
        return [], set()

    store_placeholders = ", ".join("?" for _ in store_slugs)
    day_placeholders = ", ".join("?" for _ in days)
    params = (*store_slugs, *days)
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT history.store_slug, history.marketplace, history.article, history.day,
                   MAX(COALESCE(items.barcode, '')) AS barcode,
                   MAX(COALESCE(items.name, '')) AS name,
                   SUM(history.quantity) AS quantity
              FROM marketplace_stock_daily_history history
              LEFT JOIN stock_items items
                ON items.store_slug=history.store_slug
               AND items.marketplace=history.marketplace
               AND items.article=history.article
             WHERE history.store_slug IN ({store_placeholders})
               AND history.day IN ({day_placeholders})
               AND history.scheme='fbs'
             GROUP BY history.store_slug, history.marketplace, history.article, history.day
            """,
            params,
        ).fetchall()
        coverage_rows = connection.execute(
            f"""
            SELECT DISTINCT store_slug, marketplace, day
              FROM marketplace_stock_daily_history
             WHERE store_slug IN ({store_placeholders})
               AND day IN ({day_placeholders})
               AND scheme='fbs'
            """,
            params,
        ).fetchall()
    finally:
        connection.close()

    coverage = {(str(row["store_slug"]), str(row["marketplace"]), str(row["day"])) for row in coverage_rows}
    return [dict(row) for row in rows], coverage


def set_operation_fbs_transfer(
    operation_id: int,
    is_fbs_transfer: bool,
    updated_by: int | None,
    updated_at: str,
) -> bool:
    with WRITE_LOCK:
        connection = get_connection()
        try:
            operation = connection.execute(
                "SELECT id FROM stock_operations WHERE id=? AND kind='shipment'",
                (operation_id,),
            ).fetchone()
            if operation is None:
                return False
            connection.execute(
                """
                INSERT INTO stock_operation_report_flags
                    (operation_id, is_fbs_transfer, updated_by, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    is_fbs_transfer=excluded.is_fbs_transfer,
                    updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
                """,
                (operation_id, 1 if is_fbs_transfer else 0, updated_by, updated_at),
            )
            connection.commit()
            return True
        finally:
            connection.close()
