from app.repositories.core import get_connection


def get_source_rows(
    store_slugs: tuple[str, ...],
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Load catalog, marketplace, fulfillment and in-transit stock rows."""

    if not store_slugs:
        return [], [], [], []

    placeholders = ", ".join("?" for _ in store_slugs)

    connection = get_connection()
    try:
        catalog = connection.execute(
            f"""
            SELECT source.id, source.store_slug, source.marketplace, source.article,
                   source.barcode, source.name
              FROM stock_items source
             WHERE source.store_slug IN ({placeholders})
               AND source.is_service = 0
             ORDER BY source.store_slug,
                      CASE source.marketplace
                          WHEN 'WB' THEN 0
                          WHEN 'OZON' THEN 1
                          WHEN 'YANDEX MARKET' THEN 2
                          ELSE 3
                      END,
                      source.id
            """,
            store_slugs,
        ).fetchall()
        marketplace_stock = connection.execute(
            f"""
            SELECT source.store_slug, source.marketplace, source.article,
                   source.scheme, SUM(source.quantity) AS quantity
              FROM mp_stock source
             WHERE source.store_slug IN ({placeholders})
             GROUP BY source.store_slug, source.marketplace, source.article, source.scheme
            """,
            store_slugs,
        ).fetchall()
        fulfillment_stock = connection.execute(
            f"""
            SELECT source.store_slug, source.marketplace, source.article,
                   SUM(source.quantity) AS quantity
              FROM ff_stock source
             WHERE source.store_slug IN ({placeholders})
             GROUP BY source.store_slug, source.marketplace, source.article
            """,
            store_slugs,
        ).fetchall()
        transit_stock = connection.execute(
            f"""
            SELECT batch.store_slug, batch.to_marketplace AS marketplace,
                   item.to_article AS article,
                   SUM(item.sent_quantity - item.received_quantity - item.cancelled_quantity) AS quantity
              FROM ff_transit_items item
              JOIN ff_transit_batches batch ON batch.id = item.batch_id
             WHERE batch.store_slug IN ({placeholders})
               AND batch.status IN ('in_transit', 'partial')
             GROUP BY batch.store_slug, batch.to_marketplace, item.to_article
            HAVING SUM(item.sent_quantity - item.received_quantity - item.cancelled_quantity) > 0
            """,
            store_slugs,
        ).fetchall()
    finally:
        connection.close()

    return (
        [dict(row) for row in catalog],
        [dict(row) for row in marketplace_stock],
        [dict(row) for row in fulfillment_stock],
        [dict(row) for row in transit_stock],
    )
