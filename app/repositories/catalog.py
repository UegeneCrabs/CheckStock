from app.infrastructure.database import DatabaseConnection
from app.repositories.core import WRITE_LOCK, get_connection


def _nm_id(article: object) -> str:
    return str(article or "").partition(" / ")[0].strip()


def get_excluded_nm_ids(
    store_slug: str,
    marketplace: str = "WB",
    conn: DatabaseConnection | None = None,
) -> set[str]:
    own = conn or get_connection()
    rows = own.execute(
        """
        SELECT nm_id FROM catalog_product_exclusions
        WHERE store_slug = ? AND marketplace = ?
        """,
        (store_slug, marketplace),
    ).fetchall()
    if conn is None:
        own.close()
    return {str(row["nm_id"]) for row in rows}


def list_product_exclusions(store_slug: str, marketplace: str = "WB") -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT store_slug, marketplace, nm_id, status, updated_at
        FROM catalog_product_exclusions
        WHERE store_slug = ? AND marketplace = ?
        ORDER BY nm_id
        """,
        (store_slug, marketplace),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_product_exclusions(
    store_slug: str,
    marketplace: str,
    nm_ids: list[str] | tuple[str, ...] | set[str],
    *,
    status: str,
    updated_at: str,
) -> dict:
    normalized = tuple(sorted({str(value or "").strip() for value in nm_ids if str(value or "").strip()}))
    if not normalized:
        return {"marked": 0, "catalog_removed": 0, "stock_rows_removed": 0}

    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.executemany(
                """
            INSERT INTO catalog_product_exclusions
                (store_slug, marketplace, nm_id, status, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(store_slug, marketplace, nm_id) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
                [(store_slug, marketplace, nm_id, status, updated_at) for nm_id in normalized],
            )
            active_articles = [
                str(row["article"])
                for row in conn.execute(
                    "SELECT article FROM stock_items WHERE store_slug = ? AND marketplace = ?",
                    (store_slug, marketplace),
                ).fetchall()
                if _nm_id(row["article"]) in normalized
            ]
            stock_rows_removed = 0
            for article in active_articles:
                for table in ("mp_stock", "mp_warehouse_stock"):
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE store_slug = ? AND marketplace = ? AND article = ?",
                        (store_slug, marketplace, article),
                    )
                    stock_rows_removed += max(int(cursor.rowcount or 0), 0)
                conn.execute(
                    "DELETE FROM stock_items WHERE store_slug = ? AND marketplace = ? AND article = ?",
                    (store_slug, marketplace, article),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return {
        "marked": len(normalized),
        "catalog_removed": len(active_articles),
        "stock_rows_removed": stock_rows_removed,
    }


def get_catalog_items(
    store_slug: str,
    marketplace: str = "WB",
    include_service: bool = False,
    include_excluded: bool = False,
) -> list[dict]:

    conn = get_connection()
    sql = """
        SELECT article, barcode, name, mp_sku, mp_product_id, mp_updated_at, image_url
        FROM stock_items
        WHERE store_slug = ? AND marketplace = ?
    """
    if not include_service:
        sql += " AND is_service = 0"
    if not include_excluded:
        sql += """
            AND NOT EXISTS (
                SELECT 1 FROM catalog_product_exclusions excluded
                WHERE excluded.store_slug = stock_items.store_slug
                  AND excluded.marketplace = stock_items.marketplace
                  AND (
                      excluded.nm_id = stock_items.article
                      OR stock_items.article LIKE excluded.nm_id || ' / %'
                  )
            )
        """
    sql += " ORDER BY id"
    rows = conn.execute(sql, (store_slug, marketplace)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_stock_items(store_slug: str, marketplace: str, schemes: tuple[str, ...] | None = None) -> list[dict]:

    schemes = tuple(schemes or ("fbs", "rfbs", "fbo"))

    joins = []
    columns = []
    params: list = []

    for index, scheme in enumerate(schemes):
        alias = f"s{index}"
        columns.append(f"{alias}.quantity AS {scheme}_stock")
        joins.append(
            f"LEFT JOIN mp_stock {alias}"
            f" ON {alias}.store_slug = si.store_slug AND {alias}.article = si.article"
            f" AND {alias}.marketplace = ? AND {alias}.scheme = ?"
        )
        params.extend([marketplace, scheme])

    params.append(marketplace)
    params.extend([store_slug, marketplace])

    sql = f"""
        SELECT
            si.article,
            si.barcode,
            si.name,
            si.mp_updated_at,
            si.image_url,
            ff.total_qty AS ff_available,
            {", ".join(columns)}
        FROM stock_items si
        {" ".join(joins)}
        LEFT JOIN (
            SELECT store_slug, article, SUM(quantity) AS total_qty
            FROM ff_stock WHERE marketplace = ?
            GROUP BY store_slug, article
        ) ff
            ON ff.store_slug = si.store_slug AND ff.article = si.article
        WHERE si.store_slug = ? AND si.marketplace = ? AND si.is_service = 0
          AND NOT EXISTS (
              SELECT 1 FROM catalog_product_exclusions excluded
              WHERE excluded.store_slug = si.store_slug
                AND excluded.marketplace = si.marketplace
                AND (excluded.nm_id = si.article OR si.article LIKE excluded.nm_id || ' / %')
          )
        ORDER BY si.id
    """

    conn = get_connection()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def articles_with_own_stock(
    store_slug: str, marketplace: str, conn: DatabaseConnection | None = None
) -> set[str]:

    own = conn or get_connection()
    rows = own.execute(
        """
        SELECT article FROM ff_stock
         WHERE store_slug = ? AND marketplace = ? AND quantity <> 0
        UNION
        SELECT article FROM trash_stock
         WHERE store_slug = ? AND marketplace = ? AND quantity <> 0
        """,
        (store_slug, marketplace, store_slug, marketplace),
    ).fetchall()
    if conn is None:
        own.close()
    return {row["article"] for row in rows}


def replace_catalog(
    store_slug: str,
    marketplace: str,
    items: list[dict],
    updated_at: str,
    force_remove_articles: set[str] | None = None,
) -> dict:

    conn = get_connection()

    excluded_nm_ids = get_excluded_nm_ids(store_slug, marketplace, conn)
    items = [item for item in items if _nm_id(item.get("article")) not in excluded_nm_ids]
    protected = articles_with_own_stock(store_slug, marketplace, conn)

    existing = {
        row["article"]: row
        for row in conn.execute(
            "SELECT article, barcode, name, mp_sku, mp_product_id, is_service,"
            " mp_updated_at, image_url FROM stock_items"
            " WHERE store_slug = ? AND marketplace = ?",
            (store_slug, marketplace),
        )
    }
    forced = {
        str(article).strip() for article in (force_remove_articles or set()) if str(article).strip()
    } | {article for article in existing if _nm_id(article) in excluded_nm_ids}

    seen: set[str] = set()
    added = updated = 0

    for item in items:
        article = str(item.get("article") or "").strip()
        if not article:
            continue
        seen.add(article)

        row = (
            str(item.get("barcode") or ""),
            str(item.get("name") or ""),
            str(item.get("mp_sku") or "") or None,
            str(item.get("mp_product_id") or "") or None,
            1 if item.get("is_service") else 0,
            str(item.get("mp_updated_at") or "") or None,
            str(item.get("image_url") or "") or None,
        )

        old = existing.get(article)
        if old is None:
            conn.execute(
                """
                INSERT INTO stock_items
                    (store_slug, marketplace, article, barcode, name,
                     mp_sku, mp_product_id, is_service, mp_updated_at, image_url, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (store_slug, marketplace, article, *row, updated_at),
            )
            added += 1
            continue

        current = (
            old["barcode"],
            old["name"],
            old["mp_sku"],
            old["mp_product_id"],
            old["is_service"],
            old["mp_updated_at"],
            old["image_url"],
        )
        if current == row:
            continue

        conn.execute(
            """
            UPDATE stock_items
               SET barcode = ?, name = ?, mp_sku = ?, mp_product_id = ?,
                   is_service = ?, mp_updated_at = ?, image_url = ?, updated_at = ?
             WHERE store_slug = ? AND marketplace = ? AND article = ?
            """,
            (*row, updated_at, store_slug, marketplace, article),
        )
        updated += 1

    missing = set(existing) - seen
    forced_missing = missing & forced
    kept = sorted((missing & protected) - forced_missing)
    gone = sorted((missing - protected) | forced_missing)

    for article in gone:
        conn.execute(
            "DELETE FROM stock_items WHERE store_slug = ? AND marketplace = ? AND article = ?",
            (store_slug, marketplace, article),
        )

    conn.commit()
    conn.close()
    return {
        "added": added,
        "updated": updated,
        "removed": len(gone),
        "kept": len(kept),
        "forced_removed": len(forced_missing),
    }
