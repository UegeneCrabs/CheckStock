from app.repositories.core import WRITE_LOCK, get_connection


def get_rnp_catalog_page(
    store_slug: str,
    marketplace: str,
    date_from: str,
    date_to: str,
    search: str = "",
    limit: int = 25,
    offset: int = 0,
) -> dict:

    search = search.strip()
    search_sql = ""
    search_params: list = []
    if search:
        search_sql = (
            " AND (lower(si.name) LIKE lower(?) OR lower(si.article) LIKE lower(?) "
            "OR lower(si.barcode) LIKE lower(?) OR lower(COALESCE(si.mp_sku, '')) LIKE lower(?))"
        )
        needle = f"%{search}%"
        search_params = [needle, needle, needle, needle]

    common_params: list = [store_slug, marketplace, date_from, date_to]
    where_params = [store_slug, marketplace, *search_params]
    conn = get_connection()
    total = conn.execute(
        f"""
        SELECT COUNT(*) AS total
          FROM stock_items si
         WHERE si.store_slug = ? AND si.marketplace = ? AND si.is_service = 0
               {search_sql}
        """,
        where_params,
    ).fetchone()["total"]

    rows = conn.execute(
        f"""
        WITH activity AS (
            SELECT article,
                   SUM(CASE WHEN order_amount - cancelled_amount > 0
                            THEN order_amount - cancelled_amount ELSE 0 END) AS orders_amount,
                   SUM(CASE WHEN quantity - cancelled_quantity > 0
                            THEN quantity - cancelled_quantity ELSE 0 END) AS orders_count
              FROM sales_order_lines
             WHERE store_slug = ? AND marketplace = ?
               AND ordered_at >= ? AND ordered_at < ?
             GROUP BY article
        ), current_stock AS (
            SELECT article, SUM(quantity) AS quantity, MAX(updated_at) AS updated_at
              FROM mp_stock
             WHERE store_slug = ? AND marketplace = ?
             GROUP BY article
        )
        SELECT si.article, si.barcode, si.name, si.mp_sku, si.mp_product_id,
               si.image_url, si.mp_updated_at,
               COALESCE(a.orders_amount, 0) AS orders_amount,
               COALESCE(a.orders_count, 0) AS orders_count,
               COALESCE(ms.quantity, 0) AS current_stock,
               ms.updated_at AS stock_updated_at,
               NULL AS purchase_price, NULL AS other_cost,
               NULL AS list_price, NULL AS discounted_price,
               NULL AS buyer_price, NULL AS spp_percent
          FROM stock_items si
          LEFT JOIN activity a ON a.article = si.article
          LEFT JOIN current_stock ms ON ms.article = si.article
         WHERE si.store_slug = ? AND si.marketplace = ? AND si.is_service = 0
               {search_sql}
         ORDER BY COALESCE(a.orders_amount, 0) DESC,
                  COALESCE(a.orders_count, 0) DESC,
                  lower(si.name),
                  si.article
         LIMIT ? OFFSET ?
        """,
        [
            *common_params,
            store_slug,
            marketplace,
            store_slug,
            marketplace,
            *search_params,
            limit,
            offset,
        ],
    ).fetchall()
    conn.close()
    return {"total": int(total or 0), "items": [dict(row) for row in rows]}


def get_rnp_product_daily(
    store_slug: str, marketplace: str, date_from: str, date_to: str, articles: list[str]
) -> list[dict]:

    if not articles:
        return []
    placeholders = ", ".join("?" for _ in articles)
    base_params = [store_slug, marketplace, date_from, date_to, *articles]
    conn = get_connection()
    order_rows = conn.execute(
        f"""
        SELECT article, substr(ordered_at, 1, 10) AS day,
               SUM(CASE WHEN order_amount - cancelled_amount > 0
                        THEN order_amount - cancelled_amount ELSE 0 END) AS orders_amount,
               SUM(CASE WHEN quantity - cancelled_quantity > 0
                        THEN quantity - cancelled_quantity ELSE 0 END) AS orders_count,
               SUM(cancelled_amount) AS cancellations_amount,
               SUM(cancelled_quantity) AS cancellations_count
          FROM sales_order_lines
         WHERE store_slug = ? AND marketplace = ?
           AND ordered_at >= ? AND ordered_at < ?
           AND article IN ({placeholders})
         GROUP BY article, substr(ordered_at, 1, 10)
        """,
        base_params,
    ).fetchall()
    sale_rows = conn.execute(
        f"""
        SELECT sol.article, substr(sol.sold_at, 1, 10) AS day,
               SUM(sol.sale_amount) AS sales_amount,
               SUM(sol.sold_quantity) AS sales_count,
               NULL AS gross_profit,
               0 AS costed_sales_count
          FROM sales_order_lines sol
         WHERE sol.store_slug = ? AND sol.marketplace = ?
           AND sol.sold_at >= ? AND sol.sold_at < ?
           AND sol.article IN ({placeholders})
         GROUP BY sol.article, substr(sol.sold_at, 1, 10)
        """,
        base_params,
    ).fetchall()
    return_rows = conn.execute(
        f"""
        SELECT article, substr(returned_at, 1, 10) AS day,
               SUM(return_amount) AS return_amount,
               SUM(return_quantity) AS return_count
          FROM sales_order_lines
         WHERE store_slug = ? AND marketplace = ?
           AND returned_at >= ? AND returned_at < ?
           AND article IN ({placeholders})
         GROUP BY article, substr(returned_at, 1, 10)
        """,
        base_params,
    ).fetchall()
    conn.close()

    merged: dict[tuple[str, str], dict] = {}
    for row in order_rows:
        item = dict(row)
        merged[(str(item["article"]), str(item["day"]))] = item
    for row in sale_rows:
        item = dict(row)
        target = merged.setdefault(
            (str(item["article"]), str(item["day"])),
            {"article": item["article"], "day": item["day"]},
        )
        target.update(item)
    for row in return_rows:
        item = dict(row)
        target = merged.setdefault(
            (str(item["article"]), str(item["day"])),
            {"article": item["article"], "day": item["day"]},
        )
        target.update(item)
    return [merged[key] for key in sorted(merged)]


def get_rnp_daily_totals(store_slug: str, marketplace: str, date_from: str, date_to: str) -> list[dict]:

    conn = get_connection()
    order_rows = conn.execute(
        """
        SELECT substr(ordered_at, 1, 10) AS day,
               SUM(CASE WHEN order_amount - cancelled_amount > 0
                        THEN order_amount - cancelled_amount ELSE 0 END) AS orders_amount,
               SUM(CASE WHEN quantity - cancelled_quantity > 0
                        THEN quantity - cancelled_quantity ELSE 0 END) AS orders_count,
               SUM(cancelled_amount) AS cancellations_amount,
               SUM(cancelled_quantity) AS cancellations_count
          FROM sales_order_lines
         WHERE store_slug = ? AND marketplace = ?
           AND ordered_at >= ? AND ordered_at < ?
         GROUP BY substr(ordered_at, 1, 10)
        """,
        (store_slug, marketplace, date_from, date_to),
    ).fetchall()
    sale_rows = conn.execute(
        """
        SELECT substr(sol.sold_at, 1, 10) AS day,
               SUM(sol.sale_amount) AS sales_amount,
               SUM(sol.sold_quantity) AS sales_count,
               NULL AS gross_profit,
               0 AS costed_sales_count
          FROM sales_order_lines sol
         WHERE sol.store_slug = ? AND sol.marketplace = ?
           AND sol.sold_at >= ? AND sol.sold_at < ?
         GROUP BY substr(sol.sold_at, 1, 10)
        """,
        (store_slug, marketplace, date_from, date_to),
    ).fetchall()
    return_rows = conn.execute(
        """
        SELECT substr(returned_at, 1, 10) AS day,
               SUM(return_amount) AS return_amount,
               SUM(return_quantity) AS return_count
          FROM sales_order_lines
         WHERE store_slug = ? AND marketplace = ?
           AND returned_at >= ? AND returned_at < ?
         GROUP BY substr(returned_at, 1, 10)
        """,
        (store_slug, marketplace, date_from, date_to),
    ).fetchall()
    conn.close()

    merged: dict[str, dict] = {}
    for row in order_rows:
        item = dict(row)
        merged[str(item["day"])] = item
    for row in sale_rows:
        item = dict(row)
        target = merged.setdefault(str(item["day"]), {"day": item["day"]})
        target.update(item)
    for row in return_rows:
        item = dict(row)
        target = merged.setdefault(str(item["day"]), {"day": item["day"]})
        target.update(item)
    return [merged[key] for key in sorted(merged)]


def get_rnp_stock_total(store_slug: str, marketplace: str) -> int:
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS quantity FROM mp_stock "
        "WHERE store_slug = ? AND marketplace = ?",
        (store_slug, marketplace),
    ).fetchone()
    conn.close()
    return int(row["quantity"] or 0) if row else 0


def get_rnp_strategies(store_slug: str, marketplace: str, articles: list[str]) -> dict[str, dict]:
    if not articles:
        return {}
    placeholders = ", ".join("?" for _ in articles)
    conn = get_connection()
    rows = conn.execute(
        f"SELECT * FROM rnp_strategies WHERE store_slug = ? AND marketplace = ? "
        f"AND article IN ({placeholders})",
        (store_slug, marketplace, *articles),
    ).fetchall()
    conn.close()
    return {str(row["article"]): dict(row) for row in rows}


def save_rnp_strategy(
    store_slug: str,
    marketplace: str,
    article: str,
    strategy: str,
    date_from: str,
    date_to: str,
    updated_by: str,
    updated_at: str,
) -> dict:
    with WRITE_LOCK:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO rnp_strategies
                (store_slug, marketplace, article, strategy, date_from, date_to,
                 updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_slug, marketplace, article) DO UPDATE SET
                strategy = excluded.strategy,
                date_from = excluded.date_from,
                date_to = excluded.date_to,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
            """,
            (store_slug, marketplace, article, strategy, date_from, date_to, updated_by, updated_at),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM rnp_strategies WHERE store_slug = ? AND marketplace = ? AND article = ?",
            (store_slug, marketplace, article),
        ).fetchone()
        conn.close()
    return dict(row)


def get_rnp_action_logs(
    store_slug: str, marketplace: str, date_from: str, date_to: str, articles: list[str]
) -> list[dict]:
    if not articles:
        return []
    placeholders = ", ".join("?" for _ in articles)
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT id, article, action_date, note, user_name, created_at
          FROM rnp_action_log
         WHERE store_slug = ? AND marketplace = ?
           AND action_date >= ? AND action_date < ?
           AND article IN ({placeholders})
         ORDER BY action_date, id
        """,
        (store_slug, marketplace, date_from, date_to, *articles),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_rnp_action_log(
    store_slug: str,
    marketplace: str,
    article: str,
    action_date: str,
    note: str,
    user_id: int | None,
    user_name: str,
    created_at: str,
) -> dict:
    with WRITE_LOCK:
        conn = get_connection()
        cursor = conn.execute(
            """
            INSERT INTO rnp_action_log
                (store_slug, marketplace, article, action_date, note,
                 user_id, user_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (store_slug, marketplace, article, action_date, note, user_id, user_name, created_at),
        )
        row_id = cursor.lastrowid
        conn.commit()
        row = conn.execute(
            "SELECT id, article, action_date, note, user_name, created_at FROM rnp_action_log WHERE id = ?",
            (row_id,),
        ).fetchone()
        conn.close()
    return dict(row)


def rnp_article_exists(store_slug: str, marketplace: str, article: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM stock_items WHERE store_slug = ? AND marketplace = ? "
        "AND article = ? AND is_service = 0 LIMIT 1",
        (store_slug, marketplace, article),
    ).fetchone()
    conn.close()
    return row is not None
