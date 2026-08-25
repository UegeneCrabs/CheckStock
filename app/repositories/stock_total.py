from app.repositories.core import get_connection


def get_source_rows(
    store_slugs: tuple[str, ...],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Load catalog, marketplace and fulfillment rows for the total stock view."""

    if not store_slugs:
        return [], [], []

    placeholders = ", ".join("?" for _ in store_slugs)
    exclusion = """
        AND NOT EXISTS (
            SELECT 1
              FROM catalog_product_exclusions excluded
             WHERE excluded.store_slug = source.store_slug
               AND excluded.marketplace = source.marketplace
               AND (
                    excluded.nm_id = source.article
                    OR source.article LIKE excluded.nm_id || ' / %'
               )
        )
    """

    connection = get_connection()
    try:
        catalog = connection.execute(
            f"""
            SELECT source.id, source.store_slug, source.marketplace, source.article,
                   source.barcode, source.name
              FROM stock_items source
             WHERE source.store_slug IN ({placeholders})
               AND source.is_service = 0
               {exclusion}
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
               {exclusion}
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
               {exclusion}
             GROUP BY source.store_slug, source.marketplace, source.article
            """,
            store_slugs,
        ).fetchall()
    finally:
        connection.close()

    return (
        [dict(row) for row in catalog],
        [dict(row) for row in marketplace_stock],
        [dict(row) for row in fulfillment_stock],
    )
