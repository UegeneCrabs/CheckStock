from app.infrastructure.database import DatabaseConnection, repository_connection
from app.repositories import yandex_assortment
from app.repositories.catalog_reconciliation import reconcile_renames
from app.repositories.core import get_connection
from app.stock.catalog_identity import barcodes


def get_catalog_items(
    store_slug: str,
    marketplace: str = "WB",
    include_service: bool = False,
    *,
    connection: DatabaseConnection | None = None,
) -> list[dict]:

    with repository_connection(connection) as conn:
        sql = """
            SELECT article, barcode, name, mp_sku, mp_product_id, mp_updated_at, image_url
            FROM stock_items
            WHERE store_slug = ? AND marketplace = ?
        """
        if not include_service:
            sql += " AND is_service = 0"
        sql += " ORDER BY id"
        rows = conn.execute(sql, (store_slug, marketplace)).fetchall()
        result = [dict(row) for row in rows]
        attach_barcodes(conn, store_slug, marketplace, result)
        return result


def attach_barcodes(conn, store_slug: str, marketplace: str, items: list[dict]) -> None:
    aliases: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT si.article, cb.barcode FROM catalog_barcodes cb "
        "JOIN stock_items si ON si.id=cb.stock_item_id "
        "WHERE si.store_slug=? AND si.marketplace=? ORDER BY cb.barcode",
        (store_slug, marketplace),
    ):
        aliases.setdefault(row["article"], []).append(row["barcode"])
    for item in items:
        item["barcodes"] = sorted(set(aliases.get(item["article"], [])) | barcodes(item))
    article_aliases: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT article,target_article FROM catalog_article_aliases WHERE store_slug=? AND marketplace=?",
        (store_slug, marketplace),
    ):
        article_aliases.setdefault(row["target_article"], []).append(row["article"])
    for item in items:
        item["article_aliases"] = article_aliases.get(item["article"], [])


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
    result = [dict(row) for row in rows]
    attach_barcodes(conn, store_slug, marketplace, result)
    conn.close()
    return result


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
        UNION
        SELECT item.to_article AS article FROM ff_transit_items item
          JOIN ff_transit_batches batch ON batch.id=item.batch_id
         WHERE batch.store_slug=? AND batch.to_marketplace=?
           AND item.sent_quantity > item.received_quantity + item.cancelled_quantity
        UNION
        SELECT item.from_article AS article FROM ff_transit_items item
          JOIN ff_transit_batches batch ON batch.id=item.batch_id
         WHERE batch.store_slug=? AND batch.from_marketplace=?
           AND item.sent_quantity > item.received_quantity + item.cancelled_quantity
        """,
        (store_slug, marketplace) * 4,
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
    with get_connection() as conn:
        return _replace_catalog(conn, store_slug, marketplace, items, updated_at, force_remove_articles)


def _replace_catalog(conn, store_slug, marketplace, items, updated_at, force_remove_articles):

    yandex_active = yandex_assortment.load_active_products() if marketplace == "YANDEX MARKET" else None
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
    forced = {str(article).strip() for article in (force_remove_articles or set()) if str(article).strip()}

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
        aliases = barcodes(item) | ({old["barcode"]} if old and old["barcode"] else set())
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
            _save_barcodes(conn, store_slug, marketplace, article, aliases)
            added += 1
            continue

        _save_barcodes(conn, store_slug, marketplace, article, aliases)

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
    reconciled = reconcile_renames(conn, store_slug, marketplace, existing, items, updated_at)
    protected -= reconciled
    forced_missing = missing & forced
    kept = sorted((missing & protected) - forced_missing)
    gone = sorted((missing - protected) | forced_missing)

    for article in gone:
        conn.execute(
            "DELETE FROM stock_items WHERE store_slug = ? AND marketplace = ? AND article = ?",
            (store_slug, marketplace, article),
        )

    if yandex_active is not None:
        yandex_assortment.refresh(conn, yandex_active, updated_at, store_slug)
    conn.commit()
    return {
        "added": added,
        "updated": updated,
        "removed": len(gone),
        "kept": len(kept),
        "forced_removed": len(forced_missing),
        "reconciled": len(reconciled),
    }


def _save_barcodes(conn, store_slug, marketplace, article, aliases):
    for barcode in aliases:
        conn.execute(
            "INSERT INTO catalog_barcodes (stock_item_id, barcode) "
            "SELECT id, ? FROM stock_items WHERE store_slug=? AND marketplace=? AND article=? "
            "ON CONFLICT(stock_item_id, barcode) DO NOTHING",
            (barcode, store_slug, marketplace, article),
        )
