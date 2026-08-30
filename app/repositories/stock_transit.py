from app.repositories.core import get_connection

ACTIVE_TRANSIT_STATUSES = ("in_transit", "partial")


def _attach_items(batches: list[dict]) -> list[dict]:
    if not batches:
        return []
    batch_ids = tuple(int(batch["id"]) for batch in batches)
    placeholders = ", ".join("?" for _ in batch_ids)
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT id, batch_id, from_article, to_article, barcode, name,
                   sent_quantity, received_quantity, cancelled_quantity, purchase_price
              FROM ff_transit_items
             WHERE batch_id IN ({placeholders})
             ORDER BY id
            """,
            batch_ids,
        ).fetchall()
        receipt_rows = connection.execute(
            f"""
            SELECT id, batch_id, user_id, user_name, note, received_at
              FROM ff_transit_receipts
             WHERE batch_id IN ({placeholders})
             ORDER BY received_at, id
            """,
            batch_ids,
        ).fetchall()
        receipt_ids = tuple(int(row["id"]) for row in receipt_rows)
        receipt_item_rows = []
        if receipt_ids:
            receipt_placeholders = ", ".join("?" for _ in receipt_ids)
            receipt_item_rows = connection.execute(
                f"""
                SELECT receipt_item.receipt_id,
                       receipt_item.transit_item_id,
                       receipt_item.quantity,
                       transit_item.to_article AS article,
                       transit_item.barcode,
                       transit_item.name
                  FROM ff_transit_receipt_items receipt_item
                  JOIN ff_transit_items transit_item
                    ON transit_item.id = receipt_item.transit_item_id
                 WHERE receipt_item.receipt_id IN ({receipt_placeholders})
                 ORDER BY receipt_item.id
                """,
                receipt_ids,
            ).fetchall()
    finally:
        connection.close()

    by_batch: dict[int, list[dict]] = {}
    for row in rows:
        item = dict(row)
        item["remaining_quantity"] = max(
            int(item["sent_quantity"] or 0)
            - int(item["received_quantity"] or 0)
            - int(item["cancelled_quantity"] or 0),
            0,
        )
        by_batch.setdefault(int(item["batch_id"]), []).append(item)

    receipt_items: dict[int, list[dict]] = {}
    for row in receipt_item_rows:
        item = dict(row)
        receipt_items.setdefault(int(item["receipt_id"]), []).append(item)

    receipts_by_batch: dict[int, list[dict]] = {}
    for row in receipt_rows:
        receipt = dict(row)
        receipt["items"] = receipt_items.get(int(receipt["id"]), [])
        receipt["received_units"] = sum(
            int(item["quantity"] or 0) for item in receipt["items"]
        )
        receipts_by_batch.setdefault(int(receipt["batch_id"]), []).append(receipt)

    for batch in batches:
        items = by_batch.get(int(batch["id"]), [])
        batch["items"] = items
        batch["sent_units"] = sum(int(item["sent_quantity"] or 0) for item in items)
        batch["received_units"] = sum(int(item["received_quantity"] or 0) for item in items)
        batch["cancelled_units"] = sum(int(item["cancelled_quantity"] or 0) for item in items)
        batch["remaining_units"] = sum(int(item["remaining_quantity"] or 0) for item in items)
        batch["positions"] = len(items)
        batch["receipts"] = receipts_by_batch.get(int(batch["id"]), [])
    return batches


def get_ff_transit_batches(
    store_slug: str,
    marketplace: str | None = None,
    *,
    active_only: bool = True,
    closed_only: bool = False,
    limit: int = 200,
) -> list[dict]:
    where = ["store_slug = ?"]
    params: list[str | int] = [store_slug]
    if marketplace:
        where.append("(from_marketplace = ? OR to_marketplace = ?)")
        params.extend((marketplace, marketplace))
    if active_only:
        where.append("status IN ('in_transit', 'partial')")
    elif closed_only:
        where.append("status NOT IN ('in_transit', 'partial')")
    params.append(limit)

    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT *
              FROM ff_transit_batches
             WHERE {" AND ".join(where)}
             ORDER BY id DESC
             LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        connection.close()
    return _attach_items([dict(row) for row in rows])


def get_ff_transit_batch(transfer_id: int) -> dict | None:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM ff_transit_batches WHERE id = ?",
            (transfer_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return _attach_items([dict(row)])[0]


def get_ff_transit_totals(
    store_slug: str,
    marketplace: str,
    fulfillment: str | None = None,
) -> dict[str, int]:
    where = [
        "batch.store_slug = ?",
        "batch.to_marketplace = ?",
        "batch.status IN ('in_transit', 'partial')",
    ]
    params: list[str] = [store_slug, marketplace]
    if fulfillment:
        where.append("batch.to_fulfillment = ?")
        params.append(fulfillment)

    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT item.to_article AS article,
                   SUM(item.sent_quantity - item.received_quantity - item.cancelled_quantity) AS total
              FROM ff_transit_items item
              JOIN ff_transit_batches batch ON batch.id = item.batch_id
             WHERE {" AND ".join(where)}
             GROUP BY item.to_article
            HAVING SUM(item.sent_quantity - item.received_quantity - item.cancelled_quantity) > 0
            """,
            params,
        ).fetchall()
    finally:
        connection.close()
    return {str(row["article"]): int(row["total"] or 0) for row in rows}
