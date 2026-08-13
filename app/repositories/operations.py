from app.repositories.core import get_connection

OPERATION_LABELS = {
    "delivery": "Поставка на ФФ",
    "manual_add": "Ручная докладка",
    "transfer": "Перемещение",
    "shipment": "Отгрузка со стока",
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
) -> int:

    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO stock_operations
                (store_slug, kind, source_type, source_name, sheet_url,
                 from_fulfillment, from_marketplace, to_fulfillment, to_marketplace,
                 note, user_id, user_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        operation_id = cur.lastrowid
        conn.executemany(
            """
            INSERT INTO stock_operation_items (operation_id, article, barcode, name, quantity)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    operation_id,
                    i.get("article", ""),
                    i.get("barcode"),
                    i.get("name"),
                    int(i.get("quantity") or 0),
                )
                for i in items
            ],
        )
        conn.commit()
        return operation_id
    finally:
        conn.close()


def get_operation(operation_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM stock_operations WHERE id = ?", (operation_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_store_operations(
    store_slug: str, kinds: tuple[str, ...] | None = None, limit: int = 500
) -> list[dict]:

    conn = get_connection()

    sql = """
        SELECT o.*,
               (SELECT COUNT(*) FROM stock_operation_items i
                 WHERE i.operation_id = o.id) AS positions,
               (SELECT COALESCE(SUM(i.quantity), 0) FROM stock_operation_items i
                 WHERE i.operation_id = o.id) AS units
        FROM stock_operations o
        WHERE o.store_slug = ?
    """
    params: list = [store_slug]

    if kinds:
        sql += f" AND o.kind IN ({','.join('?' for _ in kinds)})"
        params.extend(kinds)

    sql += " ORDER BY o.id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_operations_with_items(
    store_slug: str, kinds: tuple[str, ...] | None = None, limit: int = 500
) -> list[dict]:

    operations = get_store_operations(store_slug, kinds, limit)
    if not operations:
        return []

    ids = [op["id"] for op in operations]
    conn = get_connection()
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM stock_operation_items WHERE operation_id IN ({placeholders}) ORDER BY id",
        ids,
    ).fetchall()
    conn.close()

    by_operation: dict[int, list[dict]] = {}
    for row in rows:
        by_operation.setdefault(row["operation_id"], []).append(dict(row))

    for op in operations:
        op["items"] = by_operation.get(op["id"], [])
    return operations


def get_operation_items(operation_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT article, barcode, name, quantity FROM stock_operation_items "
        "WHERE operation_id = ? ORDER BY id",
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
) -> None:

    conn = get_connection()
    conn.execute(
        "INSERT INTO activity_log (user_id, user_name, action, details, created_at, operation_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, user_name, action, details, created_at, operation_id),
    )
    conn.commit()
    conn.close()
