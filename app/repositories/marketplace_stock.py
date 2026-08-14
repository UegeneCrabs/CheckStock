from app.repositories.core import get_connection


def upsert_mp_stock(
    store_slug: str,
    article: str,
    marketplace: str,
    scheme: str,
    quantity: int,
    updated_at: str,
) -> None:

    conn = get_connection()
    if quantity:
        conn.execute(
            """
            INSERT INTO mp_stock (store_slug, article, marketplace, scheme, quantity, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_slug, article, marketplace, scheme)
            DO UPDATE SET quantity = excluded.quantity, updated_at = excluded.updated_at
            """,
            (store_slug, article, marketplace, scheme, quantity, updated_at),
        )
    else:
        conn.execute(
            """
            DELETE FROM mp_stock
            WHERE store_slug = ? AND article = ? AND marketplace = ? AND scheme = ?
            """,
            (store_slug, article, marketplace, scheme),
        )
    conn.commit()
    conn.close()


def replace_mp_warehouse_stock(
    store_slug: str,
    marketplace: str,
    scheme: str,
    entries: list[tuple[str, str, str | None, int, str]],
) -> None:

    conn = get_connection()
    conn.execute(
        "DELETE FROM mp_warehouse_stock WHERE store_slug = ? AND marketplace = ? AND scheme = ?",
        (store_slug, marketplace, scheme),
    )
    conn.executemany(
        """
        INSERT INTO mp_warehouse_stock
            (store_slug, article, marketplace, scheme, warehouse, cluster, quantity, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (store_slug, article, marketplace, scheme, warehouse, cluster, quantity, updated_at)
            for article, warehouse, cluster, quantity, updated_at in entries
            if quantity
        ],
    )
    conn.commit()
    conn.close()


def delete_mp_stock_scheme_variants(
    store_slug: str,
    marketplace: str,
    canonical_scheme: str,
) -> None:

    conn = get_connection()
    params = (store_slug, marketplace, canonical_scheme, f"{canonical_scheme}%")
    for table in ("mp_stock", "mp_warehouse_stock"):
        conn.execute(
            f"""
            DELETE FROM {table}
            WHERE store_slug = ? AND marketplace = ?
              AND scheme <> ? AND scheme LIKE ?
            """,
            params,
        )
    conn.commit()
    conn.close()


def get_warehouse_clusters(marketplace: str) -> dict[str, str]:

    conn = get_connection()
    rows = conn.execute(
        "SELECT warehouse, cluster FROM mp_warehouse_cluster WHERE marketplace = ?",
        (marketplace,),
    ).fetchall()
    conn.close()
    return {row["warehouse"]: row["cluster"] for row in rows}


def save_warehouse_clusters(marketplace: str, mapping: dict[str, str], updated_at: str) -> int:

    if not mapping:
        return 0

    conn = get_connection()
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM mp_warehouse_cluster WHERE marketplace = ?",
        (marketplace,),
    ).fetchone()["n"]

    conn.executemany(
        """
        INSERT INTO mp_warehouse_cluster (marketplace, warehouse, cluster, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(marketplace, warehouse)
        DO UPDATE SET cluster = excluded.cluster, updated_at = excluded.updated_at
        """,
        [(marketplace, w, c, updated_at) for w, c in mapping.items() if w and c],
    )
    after = conn.execute(
        "SELECT COUNT(*) AS n FROM mp_warehouse_cluster WHERE marketplace = ?",
        (marketplace,),
    ).fetchone()["n"]
    conn.commit()
    conn.close()
    return after - before


def get_mp_warehouse_details(
    store_slug: str, marketplace: str, scheme: str, group_by_cluster: bool = False
) -> list[dict]:

    column = "COALESCE(NULLIF(ws.cluster, ''), ws.warehouse)" if group_by_cluster else "ws.warehouse"
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT si.article, si.barcode, si.name, si.image_url,
               {column} AS warehouse,
               SUM(ws.quantity) AS quantity
        FROM stock_items si
        JOIN mp_warehouse_stock ws
            ON ws.store_slug = si.store_slug AND ws.article = si.article
        WHERE si.store_slug = ? AND si.marketplace = ? AND si.is_service = 0
          AND ws.marketplace = ? AND ws.scheme = ?
        GROUP BY si.id, {column}
        ORDER BY si.id, {column}
        """,
        (store_slug, marketplace, marketplace, scheme),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_mp_fbs_warehouse_details(
    store_slug: str,
    marketplace: str,
    article: str | None = None,
) -> list[dict]:

    article_filter = " AND si.article = ?" if article else ""
    params: list = [store_slug, marketplace, marketplace]
    if article:
        params.append(article)

    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT si.article, si.barcode, si.name, si.image_url,
               ws.scheme, ws.warehouse, SUM(ws.quantity) AS quantity
        FROM stock_items si
        JOIN mp_warehouse_stock ws
            ON ws.store_slug = si.store_slug AND ws.article = si.article
        WHERE si.store_slug = ? AND si.marketplace = ? AND si.is_service = 0
          AND ws.marketplace = ? AND ws.scheme LIKE 'fbs%'
          {article_filter}
        GROUP BY si.id, ws.scheme, ws.warehouse
        ORDER BY si.id, ws.warehouse
        """,
        params,
    ).fetchall()
    conn.close()

    data = [dict(row) for row in rows]
    canonical_articles = {str(row["article"]) for row in data if row["scheme"] == "fbs"}
    merged: dict[tuple[str, str], dict] = {}
    for row in data:
        row_article = str(row["article"])
        if row_article in canonical_articles and row["scheme"] != "fbs":
            continue
        key = (row_article, str(row["warehouse"]))
        if key not in merged:
            merged[key] = {field: value for field, value in row.items() if field != "scheme"}
        else:
            merged[key]["quantity"] = int(merged[key]["quantity"] or 0) + int(row["quantity"] or 0)
    return list(merged.values())


def get_mp_stock_by_warehouse(
    store_slug: str, marketplace: str, scheme: str, warehouse: str
) -> dict[str, int]:

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT article, SUM(quantity) AS quantity FROM mp_warehouse_stock
        WHERE store_slug = ? AND marketplace = ? AND scheme = ? AND warehouse = ?
        GROUP BY article
        """,
        (store_slug, marketplace, scheme, warehouse),
    ).fetchall()
    conn.close()
    return {row["article"]: row["quantity"] for row in rows}


def get_mp_stock_totals(store_slug: str, marketplace: str, scheme: str) -> dict[str, int]:

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT article, quantity FROM mp_stock
        WHERE store_slug = ? AND marketplace = ? AND scheme = ?
        """,
        (store_slug, marketplace, scheme),
    ).fetchall()
    conn.close()
    return {row["article"]: row["quantity"] for row in rows}


def replace_ff_warehouse_map(store_slug: str, entries: list[tuple[str, int, str, str]]) -> None:

    conn = get_connection()
    conn.execute("DELETE FROM ff_warehouse_map WHERE store_slug = ?", (store_slug,))
    conn.executemany(
        """
        INSERT INTO ff_warehouse_map (store_slug, fulfillment, wb_warehouse_id, wb_warehouse_name, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (store_slug, fulfillment, wb_warehouse_id, wb_warehouse_name, updated_at)
            for fulfillment, wb_warehouse_id, wb_warehouse_name, updated_at in entries
        ],
    )
    conn.commit()
    conn.close()


def get_ff_warehouse_map(store_slug: str) -> list[dict]:

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT fulfillment, wb_warehouse_id, wb_warehouse_name, updated_at
        FROM ff_warehouse_map WHERE store_slug = ?
        ORDER BY fulfillment
        """,
        (store_slug,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_last_sync_at(marketplace: str | None = None) -> str | None:

    conn = get_connection()
    if marketplace:
        row = conn.execute(
            "SELECT MAX(updated_at) AS last_sync FROM mp_stock WHERE marketplace = ?",
            (marketplace,),
        ).fetchone()
    else:
        row = conn.execute("SELECT MAX(updated_at) AS last_sync FROM mp_stock").fetchone()
    conn.close()
    return row["last_sync"] if row is not None else None


def get_stock_overview() -> dict[str, dict]:

    conn = get_connection()
    marketplaces_aggregate = (
        "STRING_AGG(DISTINCT marketplace, ',')"
        if conn.dialect_name == "postgresql"
        else "GROUP_CONCAT(DISTINCT marketplace)"
    )
    catalog_rows = conn.execute(
        f"""
        SELECT store_slug, COUNT(*) AS sku_count,
               COUNT(DISTINCT marketplace) AS marketplace_count,
               {marketplaces_aggregate} AS marketplaces
        FROM stock_items
        WHERE is_service = 0
        GROUP BY store_slug
        """
    ).fetchall()
    marketplace_rows = conn.execute(
        """
        SELECT store_slug, COALESCE(SUM(quantity), 0) AS quantity,
               MAX(updated_at) AS last_sync
        FROM mp_stock
        GROUP BY store_slug
        """
    ).fetchall()
    fulfillment_rows = conn.execute(
        """
        SELECT store_slug, COALESCE(SUM(quantity), 0) AS quantity
        FROM ff_stock
        GROUP BY store_slug
        """
    ).fetchall()
    conn.close()

    result: dict[str, dict] = {}
    for row in catalog_rows:
        result[row["store_slug"]] = {
            "sku_count": int(row["sku_count"] or 0),
            "marketplace_count": int(row["marketplace_count"] or 0),
            "marketplaces": [value for value in str(row["marketplaces"] or "").split(",") if value],
            "marketplace_stock": 0,
            "fulfillment_stock": 0,
            "last_sync": None,
        }

    for row in marketplace_rows:
        item = result.setdefault(row["store_slug"], {})
        item["marketplace_stock"] = int(row["quantity"] or 0)
        item["last_sync"] = row["last_sync"]

    for row in fulfillment_rows:
        item = result.setdefault(row["store_slug"], {})
        item["fulfillment_stock"] = int(row["quantity"] or 0)

    for item in result.values():
        item.setdefault("sku_count", 0)
        item.setdefault("marketplace_count", 0)
        item.setdefault("marketplaces", [])
        item.setdefault("marketplace_stock", 0)
        item.setdefault("fulfillment_stock", 0)
        item.setdefault("last_sync", None)
        item["total_stock"] = item["marketplace_stock"] + item["fulfillment_stock"]
    return result
