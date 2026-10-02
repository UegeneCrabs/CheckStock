from app.infrastructure.database import DatabaseConnection, repository_connection
from app.repositories.core import get_connection

OPERATION_LABELS = {
    "delivery": "Поставка на ФФ",
    "manual_add": "Ручная докладка",
    "transfer": "Перемещение",
    "transfer_dispatch": "Перемещение отправлено",
    "transfer_receive": "Перемещение принято",
    "transfer_receive_revert": "Приёмка возвращена в путь",
    "transfer_cancel": "Перемещение отменено",
    "shipment": "Отгрузка со стока",
    "fbs_transfer": "Перемещение на FBS",
    "trash": "Списание в мусорку",
}


SOURCE_LABELS = {
    "file": "файл",
    "sheet": "Google Таблица",
    "manual": "ручной ввод",
}


def record_operation(
    store_slug: str,
    kind: str,
    source_type: str,
    items: list[dict],
    user_id: int | None,
    user_name: str,
    created_at: str,
    source_name: str | None = None,
    sheet_url: str | None = None,
    from_fulfillment: str | None = None,
    from_marketplace: str | None = None,
    to_fulfillment: str | None = None,
    to_marketplace: str | None = None,
    note: str | None = None,
    transit_batch_id: int | None = None,
    *,
    connection: DatabaseConnection | None = None,
) -> int:

    with repository_connection(connection) as conn:
        price_rows = conn.execute(
            """
            SELECT items.article, items.barcode, source.purchase_price
              FROM stock_items items
              JOIN unit_economics_1c_source_values source
                ON source.stock_item_id=items.id
             WHERE items.store_slug=? AND items.marketplace='WB'
               AND items.is_service=0
            """,
            (store_slug,),
        ).fetchall()
        price_by_article = {
            str(row["article"]): float(row["purchase_price"])
            for row in price_rows
            if row["article"] and row["purchase_price"] is not None
        }
        price_by_barcode = {
            str(row["barcode"]): float(row["purchase_price"])
            for row in price_rows
            if row["barcode"] and row["purchase_price"] is not None
        }
        cur = conn.execute(
            """
            INSERT INTO stock_operations
                (store_slug, kind, source_type, source_name, sheet_url,
                 from_fulfillment, from_marketplace, to_fulfillment, to_marketplace,
                 note, user_id, user_name, created_at, transit_batch_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                store_slug,
                kind,
                source_type,
                source_name,
                sheet_url,
                from_fulfillment,
                from_marketplace,
                to_fulfillment,
                to_marketplace,
                note,
                user_id,
                user_name,
                created_at,
                transit_batch_id,
            ),
        )
        operation_id = cur.lastrowid
        conn.executemany(
            """
            INSERT INTO stock_operation_items
                (operation_id, article, barcode, name, quantity,
                 purchase_price, purchase_price_recorded)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            [
                (
                    operation_id,
                    i.get("article", ""),
                    i.get("barcode"),
                    i.get("name"),
                    int(i.get("quantity") or 0),
                    (
                        None
                        if transit_batch_id is not None and i.get("purchase_price") is None
                        else (
                            float(i["purchase_price"])
                            if i.get("purchase_price") is not None
                            else price_by_article.get(
                                str(i.get("article") or ""),
                                price_by_barcode.get(str(i.get("barcode") or "")),
                            )
                        )
                    ),
                )
                for i in items
            ],
        )
        return operation_id


def get_operation(operation_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM stock_operations WHERE id = ?", (operation_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _operation_where(
    store_slug: str, kinds: tuple[str, ...] | None, marketplaces: tuple[str, ...] | None
) -> tuple[str, list]:
    clauses = ["o.store_slug = ?"]
    params: list = [store_slug]
    if kinds:
        clauses.append(f"o.kind IN ({','.join('?' for _ in kinds)})")
        params.extend(kinds)
    if marketplaces is not None:
        if not marketplaces:
            clauses.append("1 = 0")
        else:
            placeholders = ",".join("?" for _ in marketplaces)
            clauses.append(
                f"(o.from_marketplace IN ({placeholders}) OR o.to_marketplace IN ({placeholders}) "
                "OR (COALESCE(o.from_marketplace, '') = '' AND COALESCE(o.to_marketplace, '') = ''))"
            )
            params.extend(marketplaces)
            params.extend(marketplaces)
    return " AND ".join(clauses), params


def get_store_operation_stats(
    store_slug: str, kinds: tuple[str, ...] | None = None, *, marketplaces: tuple[str, ...] | None = None
) -> dict:
    """Count the complete accessible history, independent of the displayed page."""
    conn = get_connection()
    try:
        where, params = _operation_where(store_slug, None, marketplaces)
        counts = {
            row["kind"]: int(row["total"])
            for row in conn.execute(
                f"SELECT o.kind, COUNT(*) AS total FROM stock_operations o WHERE {where} GROUP BY o.kind",
                params,
            ).fetchall()
        }
        where, params = _operation_where(store_slug, kinds, marketplaces)
        summary = conn.execute(
            f"""
            WITH selected_operations AS (
                SELECT o.id, o.user_name FROM stock_operations o WHERE {where}
            ), item_totals AS (
                SELECT i.operation_id, COUNT(*) AS positions, SUM(i.quantity) AS units
                  FROM stock_operation_items i
                  JOIN selected_operations o ON o.id = i.operation_id
                 GROUP BY i.operation_id
            )
            SELECT COUNT(*) AS total, COALESCE(SUM(items.positions), 0) AS positions,
                   COALESCE(SUM(ABS(items.units)), 0) AS units,
                   COUNT(DISTINCT NULLIF(o.user_name, '')) AS employees
              FROM selected_operations o
              LEFT JOIN item_totals items ON items.operation_id = o.id
            """,
            params,
        ).fetchone()
        return {"counts": counts, "summary": dict(summary)}
    finally:
        conn.close()


def get_store_operations(
    store_slug: str,
    kinds: tuple[str, ...] | None = None,
    limit: int | None = 500,
    *,
    offset: int = 0,
    marketplaces: tuple[str, ...] | None = None,
) -> list[dict]:

    conn = get_connection()

    where, params = _operation_where(store_slug, kinds, marketplaces)
    selection = f"SELECT o.* FROM stock_operations o WHERE {where} ORDER BY o.id DESC"
    if limit is not None:
        selection += " LIMIT ? OFFSET ?"
        params.extend((limit, offset))
    sql = f"""
        WITH selected_operations AS ({selection}), item_totals AS (
            SELECT i.operation_id, COUNT(*) AS positions, SUM(i.quantity) AS units
              FROM stock_operation_items i
              JOIN selected_operations o ON o.id = i.operation_id
             GROUP BY i.operation_id
        )
        SELECT o.*, COALESCE(items.positions, 0) AS positions, COALESCE(items.units, 0) AS units
          FROM selected_operations o
          LEFT JOIN item_totals items ON items.operation_id = o.id
         ORDER BY o.id DESC
    """
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_operations_with_items(
    store_slug: str,
    kinds: tuple[str, ...] | None = None,
    limit: int | None = 500,
    *,
    marketplaces: tuple[str, ...] | None = None,
) -> list[dict]:

    operations = get_store_operations(store_slug, kinds, limit, marketplaces=marketplaces)
    if not operations:
        return []

    ids = [op["id"] for op in operations]
    conn = get_connection()
    by_operation: dict[int, list[dict]] = {}
    try:
        for start in range(0, len(ids), 500):
            batch = ids[start : start + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = conn.execute(
                f"SELECT * FROM stock_operation_items WHERE operation_id IN ({placeholders}) ORDER BY id",
                batch,
            ).fetchall()
            for row in rows:
                by_operation.setdefault(row["operation_id"], []).append(dict(row))
    finally:
        conn.close()

    for op in operations:
        op["items"] = by_operation.get(op["id"], [])
    return operations


def get_operation_items(operation_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT article, barcode, name, quantity, purchase_price, purchase_price_recorded "
        "FROM stock_operation_items WHERE operation_id = ? ORDER BY id",
        (operation_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def log_action_for_operation(
    user_id: int | None,
    user_name: str,
    action: str,
    details: str,
    created_at: str,
    operation_id: int | None = None,
    *,
    connection: DatabaseConnection | None = None,
) -> None:

    with repository_connection(connection) as conn:
        conn.execute(
            "INSERT INTO activity_log (user_id, user_name, action, details, created_at, operation_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, user_name, action, details, created_at, operation_id),
        )
