from app.infrastructure.database import DatabaseConnection
from app.repositories.core import get_connection


def get_catalog_items(store_slug: str, marketplace: str = "WB", include_service: bool = False) -> list[dict]:

    conn = get_connection()
    sql = """
        SELECT article, barcode, name, mp_sku, mp_product_id, mp_updated_at, image_url
        FROM stock_items
        WHERE store_slug = ? AND marketplace = ?
    """
    if not include_service:
        sql += " AND is_service = 0"
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

    protected = articles_with_own_stock(store_slug, marketplace, conn)
    forced = {str(article).strip() for article in (force_remove_articles or set()) if str(article).strip()}

    existing = {
        row["article"]: row
        for row in conn.execute(
            "SELECT article, barcode, name, mp_sku, mp_product_id, is_service,"
            " mp_updated_at, image_url FROM stock_items"
            " WHERE store_slug = ? AND marketplace = ?",
            (store_slug, marketplace),
        )
    }

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
