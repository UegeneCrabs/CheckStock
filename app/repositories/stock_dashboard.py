from app.repositories.core import get_connection


def get_inventory_rows(sales_since: str, frozen_since: str) -> list[dict]:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            WITH mp AS (
                SELECT store_slug, marketplace, article,
                       SUM(quantity) AS marketplace_stock,
                       MAX(updated_at) AS stock_updated_at
                  FROM mp_stock
                 GROUP BY store_slug, marketplace, article
            ),
            ff AS (
                SELECT store_slug, marketplace, article,
                       SUM(quantity) AS fulfillment_stock
                  FROM ff_stock
                 GROUP BY store_slug, marketplace, article
            ),
            sales AS (
                SELECT store_slug, marketplace, article,
                       SUM(CASE WHEN sold_at >= ? THEN sold_quantity ELSE 0 END) AS sold_30,
                       SUM(CASE WHEN sold_at >= ? THEN sold_quantity ELSE 0 END) AS sold_60,
                       MAX(sold_at) AS last_sold_at,
                       COUNT(*) AS sale_rows
                  FROM sales_order_lines
                 WHERE sold_at IS NOT NULL
                 GROUP BY store_slug, marketplace, article
            ),
            sales_state AS (
                SELECT store_slug, marketplace,
                       MAX(CASE WHEN last_success_at IS NOT NULL THEN 1 ELSE 0 END) AS loaded,
                       MAX(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS has_error
                  FROM sales_sync_state
                 GROUP BY store_slug, marketplace
            ),
            keys AS (
                SELECT store_slug, marketplace, article
                  FROM stock_items
                 WHERE is_service = 0
                UNION
                SELECT store_slug, marketplace, article FROM mp
                UNION
                SELECT store_slug, marketplace, article FROM ff
            )
            SELECT keys.store_slug, keys.marketplace, keys.article,
                   si.id AS catalog_id, si.barcode, si.name,
                   COALESCE(mp.marketplace_stock, 0) AS marketplace_stock,
                   COALESCE(ff.fulfillment_stock, 0) AS fulfillment_stock,
                   COALESCE(sales.sold_30, 0) AS sold_30,
                   COALESCE(sales.sold_60, 0) AS sold_60,
                   sales.last_sold_at,
                   CASE WHEN COALESCE(sales_state.loaded, 0) = 1
                              OR COALESCE(sales.sale_rows, 0) > 0
                        THEN 1 ELSE 0 END AS sales_loaded,
                   COALESCE(sales_state.has_error, 0) AS sales_error,
                   NULL AS purchase_price,
                   mp.stock_updated_at
              FROM keys
              LEFT JOIN stock_items si
                ON si.store_slug = keys.store_slug
               AND si.marketplace = keys.marketplace
               AND si.article = keys.article
               AND si.is_service = 0
              LEFT JOIN mp
                ON mp.store_slug = keys.store_slug
               AND mp.marketplace = keys.marketplace
               AND mp.article = keys.article
              LEFT JOIN ff
                ON ff.store_slug = keys.store_slug
               AND ff.marketplace = keys.marketplace
               AND ff.article = keys.article
              LEFT JOIN sales
                ON sales.store_slug = keys.store_slug
               AND sales.marketplace = keys.marketplace
               AND sales.article = keys.article
              LEFT JOIN sales_state
                ON sales_state.store_slug = keys.store_slug
               AND sales_state.marketplace = keys.marketplace
             ORDER BY keys.store_slug, keys.marketplace, COALESCE(si.id, 999999), keys.article
            """,
            (sales_since, frozen_since),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
