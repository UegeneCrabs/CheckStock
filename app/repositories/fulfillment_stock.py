import hashlib

from app.domain import DEFAULT_MARKETPLACE
from app.repositories.catalog import get_catalog_items
from app.repositories.core import get_connection


def upsert_ff_stock(
    store_slug: str,
    article: str,
    fulfillment: str,
    quantity: int,
    updated_at: str,
    marketplace: str = DEFAULT_MARKETPLACE,
) -> None:

    conn = get_connection()
    if quantity:
        conn.execute(
            """
            INSERT INTO ff_stock (store_slug, article, fulfillment, marketplace, quantity, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_slug, article, fulfillment, marketplace)
            DO UPDATE SET quantity = excluded.quantity, updated_at = excluded.updated_at
            """,
            (store_slug, article, fulfillment, marketplace, quantity, updated_at),
        )
    else:
        conn.execute(
            """
            DELETE FROM ff_stock
            WHERE store_slug = ? AND article = ? AND fulfillment = ? AND marketplace = ?
            """,
            (store_slug, article, fulfillment, marketplace),
        )
    conn.commit()
    conn.close()


def increment_ff_stock(
    store_slug: str,
    article: str,
    fulfillment: str,
    delta: int,
    updated_at: str,
    marketplace: str = DEFAULT_MARKETPLACE,
) -> None:

    if not delta:
        return

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO ff_stock (store_slug, article, fulfillment, marketplace, quantity, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(store_slug, article, fulfillment, marketplace)
        DO UPDATE SET quantity = ff_stock.quantity + excluded.quantity, updated_at = excluded.updated_at
        """,
        (store_slug, article, fulfillment, marketplace, delta, updated_at),
    )

    conn.execute(
        """
        DELETE FROM ff_stock
        WHERE store_slug = ? AND article = ? AND fulfillment = ? AND marketplace = ? AND quantity = 0
        """,
        (store_slug, article, fulfillment, marketplace),
    )
    conn.commit()
    conn.close()


def find_existing_delivery(
    store_slug: str, sheet_url: str | None, table_title: str, marketplace: str = DEFAULT_MARKETPLACE
) -> dict | None:

    conn = get_connection()
    row = None
    if sheet_url:
        row = conn.execute(
            "SELECT * FROM ff_stock_deliveries"
            " WHERE store_slug = ? AND marketplace = ? AND sheet_url = ? LIMIT 1",
            (store_slug, marketplace, sheet_url),
        ).fetchone()
    if row is None and table_title:
        row = conn.execute(
            "SELECT * FROM ff_stock_deliveries"
            " WHERE store_slug = ? AND marketplace = ? AND table_title = ? LIMIT 1",
            (store_slug, marketplace, table_title),
        ).fetchone()
    conn.close()
    return dict(row) if row is not None else None


def record_sync_health(
    store_slug: str, marketplace: str, scope: str, ok: bool, error: str | None, checked_at: str
) -> None:

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO sync_health (store_slug, marketplace, scope, ok, error, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(store_slug, marketplace, scope)
        DO UPDATE SET ok = excluded.ok, error = excluded.error,
                      checked_at = excluded.checked_at
        """,
        (store_slug, marketplace, scope, 1 if ok else 0, error, checked_at),
    )
    conn.commit()
    conn.close()


def get_sync_health(store_slug: str) -> list[dict]:

    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM sync_health WHERE store_slug = ? AND ok = 0 ORDER BY marketplace, scope",
        (store_slug,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def source_fingerprint(source_type: str, sheet_url: str | None, file_bytes: bytes | None) -> str | None:

    if source_type == "sheet" and sheet_url:
        return f"sheet:{sheet_url.strip()}"
    if source_type == "file" and file_bytes:
        return "file:" + hashlib.sha256(file_bytes).hexdigest()
    return None


def find_used_source(store_slug: str, kind: str, fingerprint: str | None) -> dict | None:

    if not fingerprint:
        return None
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM used_sources WHERE store_slug = ? AND kind = ? AND fingerprint = ?",
        (store_slug, kind, fingerprint),
    ).fetchone()
    conn.close()
    return dict(row) if row is not None else None


def record_used_source(
    store_slug: str,
    kind: str,
    fingerprint: str | None,
    label: str,
    source_type: str,
    operation_id: int | None,
    user_name: str,
    created_at: str,
) -> None:

    if not fingerprint:
        return
    conn = get_connection()
    conn.execute(
        """
        INSERT OR IGNORE INTO used_sources
            (store_slug, kind, fingerprint, label, source_type,
             operation_id, user_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (store_slug, kind, fingerprint, label, source_type, operation_id, user_name, created_at),
    )
    conn.commit()
    conn.close()


def record_delivery(
    store_slug: str,
    fulfillment: str,
    source_type: str,
    sheet_url: str | None,
    table_title: str,
    total_rows: int,
    matched: int,
    unmatched: int,
    created_at: str,
    marketplace: str = DEFAULT_MARKETPLACE,
) -> None:

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO ff_stock_deliveries
            (store_slug, fulfillment, marketplace, source_type, sheet_url, table_title,
             total_rows, matched, unmatched, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            store_slug,
            fulfillment,
            marketplace,
            source_type,
            sheet_url,
            table_title,
            total_rows,
            matched,
            unmatched,
            created_at,
        ),
    )
    conn.commit()
    conn.close()


def get_ff_available_totals(
    store_slug: str,
    fulfillment: str | None = None,
    marketplace: str | None = None,
) -> dict[str, int]:

    where = ["store_slug = ?"]
    params: list = [store_slug]
    if fulfillment:
        where.append("fulfillment = ?")
        params.append(fulfillment)
    if marketplace:
        where.append("marketplace = ?")
        params.append(marketplace)

    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT article, SUM(quantity) AS total
        FROM ff_stock
        WHERE {" AND ".join(where)}
        GROUP BY article
        """,
        params,
    ).fetchall()
    conn.close()
    return {row["article"]: row["total"] for row in rows}


def search_catalog(
    store_slug: str,
    query: str,
    limit: int = 15,
    fulfillment: str | None = None,
    marketplace: str | None = None,
) -> list[dict]:

    query = (query or "").strip().casefold()
    if not query:
        return []

    stock_map = None
    if fulfillment and marketplace:
        stock_map = get_ff_available_totals(store_slug, fulfillment, marketplace)

    matches = []
    for item in get_catalog_items(store_slug, marketplace or "WB"):
        if stock_map is not None and not stock_map.get(item["article"]):
            continue

        article = item["article"].casefold()
        barcode = item["barcode"].casefold()
        name = item["name"].casefold()

        if article.startswith(query):
            rank = 0
        elif barcode.startswith(query):
            rank = 1
        elif query in article:
            rank = 2
        elif query in barcode:
            rank = 3
        elif query in name:
            rank = 4
        else:
            continue

        row = dict(item)
        if stock_map is not None:
            row["stock"] = stock_map.get(item["article"], 0)
        matches.append((rank, item["article"], row))

    matches.sort(key=lambda m: (m[0], m[1]))
    return [m[2] for m in matches[:limit]]


def get_ff_stock_one(store_slug: str, article: str, fulfillment: str, marketplace: str) -> int:

    conn = get_connection()
    row = conn.execute(
        """
        SELECT quantity FROM ff_stock
        WHERE store_slug = ? AND article = ? AND fulfillment = ? AND marketplace = ?
        """,
        (store_slug, article, fulfillment, marketplace),
    ).fetchone()
    conn.close()
    return row["quantity"] if row else 0


def apply_ff_transfer(
    store_slug: str,
    entries: list[tuple[str, str, int]],
    from_fulfillment: str,
    from_marketplace: str,
    to_fulfillment: str,
    to_marketplace: str,
    user_id: int | None,
    user_name: str,
    created_at: str,
) -> None:

    conn = get_connection()
    try:
        conn.execute("BEGIN")

        for from_article, to_article, quantity in entries:
            conn.execute(
                """
                UPDATE ff_stock SET quantity = quantity - ?, updated_at = ?
                WHERE store_slug = ? AND article = ? AND fulfillment = ? AND marketplace = ?
                """,
                (quantity, created_at, store_slug, from_article, from_fulfillment, from_marketplace),
            )

            conn.execute(
                """
                INSERT INTO ff_stock (store_slug, article, fulfillment, marketplace, quantity, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, article, fulfillment, marketplace)
                DO UPDATE SET quantity = ff_stock.quantity + excluded.quantity,
                              updated_at = excluded.updated_at
                """,
                (store_slug, to_article, to_fulfillment, to_marketplace, quantity, created_at),
            )
            conn.execute(
                """
                INSERT INTO ff_transfers
                    (store_slug, article, quantity, from_fulfillment, from_marketplace,
                     to_fulfillment, to_marketplace, user_id, user_name, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    store_slug,
                    to_article,
                    quantity,
                    from_fulfillment,
                    from_marketplace,
                    to_fulfillment,
                    to_marketplace,
                    user_id,
                    user_name,
                    created_at,
                ),
            )

        conn.execute("DELETE FROM ff_stock WHERE store_slug = ? AND quantity = 0", (store_slug,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_ff_shipment(
    store_slug: str,
    entries: list[tuple[str, int]],
    fulfillment: str,
    marketplace: str,
    created_at: str,
) -> None:

    conn = get_connection()
    try:
        conn.execute("BEGIN")

        for article, quantity in entries:
            conn.execute(
                """
                UPDATE ff_stock SET quantity = quantity - ?, updated_at = ?
                WHERE store_slug = ? AND article = ? AND fulfillment = ? AND marketplace = ?
                """,
                (quantity, created_at, store_slug, article, fulfillment, marketplace),
            )

        negative = conn.execute(
            """
            SELECT article, quantity FROM ff_stock
            WHERE store_slug = ? AND fulfillment = ? AND marketplace = ? AND quantity < 0
            """,
            (store_slug, fulfillment, marketplace),
        ).fetchall()
        if negative:
            raise ValueError(
                "отгрузка увела бы остаток в минус: "
                + ", ".join(f"{r['article']} -> {r['quantity']}" for r in negative)
            )

        conn.execute("DELETE FROM ff_stock WHERE store_slug = ? AND quantity = 0", (store_slug,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_ff_trash(
    store_slug: str,
    entries: list[tuple[str, int]],
    fulfillment: str,
    marketplace: str,
    created_at: str,
) -> None:

    conn = get_connection()
    try:
        conn.execute("BEGIN")

        for article, quantity in entries:
            conn.execute(
                """
                UPDATE ff_stock SET quantity = quantity - ?, updated_at = ?
                WHERE store_slug = ? AND article = ? AND fulfillment = ? AND marketplace = ?
                """,
                (quantity, created_at, store_slug, article, fulfillment, marketplace),
            )
            conn.execute(
                """
                INSERT INTO trash_stock
                    (store_slug, article, marketplace, fulfillment, quantity, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, article, marketplace, fulfillment)
                DO UPDATE SET quantity = trash_stock.quantity + excluded.quantity,
                              updated_at = excluded.updated_at
                """,
                (store_slug, article, marketplace, fulfillment, quantity, created_at),
            )

        negative = conn.execute(
            """
            SELECT article, quantity FROM ff_stock
            WHERE store_slug = ? AND fulfillment = ? AND marketplace = ? AND quantity < 0
            """,
            (store_slug, fulfillment, marketplace),
        ).fetchall()
        if negative:
            raise ValueError(
                "списание в мусорку увело бы остаток в минус: "
                + ", ".join(f"{r['article']} -> {r['quantity']}" for r in negative)
            )

        conn.execute("DELETE FROM ff_stock WHERE store_slug = ? AND quantity = 0", (store_slug,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_trash_checked(
    store_slug: str, marketplace: str, article: str, fulfillment: str, checked: bool
) -> None:

    conn = get_connection()
    conn.execute(
        """
        UPDATE trash_stock SET checked = ?
        WHERE store_slug = ? AND marketplace = ? AND article = ? AND fulfillment = ?
        """,
        (1 if checked else 0, store_slug, marketplace, article, fulfillment),
    )
    conn.commit()
    conn.close()


def apply_ff_surplus(
    store_slug: str,
    entries: list[tuple[str, int]],
    fulfillment: str,
    marketplace: str,
    created_at: str,
) -> None:

    conn = get_connection()
    try:
        conn.execute("BEGIN")

        for article, quantity in entries:
            conn.execute(
                """
                INSERT INTO trash_stock
                    (store_slug, article, marketplace, fulfillment, quantity, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, article, marketplace, fulfillment)
                DO UPDATE SET quantity = trash_stock.quantity + excluded.quantity,
                              updated_at = excluded.updated_at
                """,
                (store_slug, article, marketplace, fulfillment, -quantity, created_at),
            )
            conn.execute(
                """
                INSERT INTO ff_stock (store_slug, article, fulfillment, marketplace, quantity, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, article, fulfillment, marketplace)
                DO UPDATE SET quantity = ff_stock.quantity + excluded.quantity,
                              updated_at = excluded.updated_at
                """,
                (store_slug, article, fulfillment, marketplace, quantity, created_at),
            )

        conn.execute("DELETE FROM trash_stock WHERE store_slug = ? AND quantity = 0", (store_slug,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_trash_details(store_slug: str, marketplace: str) -> list[dict]:

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT si.article, si.barcode, si.name, si.image_url,
               t.fulfillment AS warehouse,
               t.quantity AS quantity,
               t.checked AS checked
        FROM trash_stock t
        JOIN stock_items si
            ON si.store_slug = t.store_slug AND si.article = t.article
           AND si.marketplace = t.marketplace
        WHERE t.store_slug = ? AND t.marketplace = ? AND t.quantity <> 0
        ORDER BY si.id, t.fulfillment
        """,
        (store_slug, marketplace),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_ff_transfers(store_slug: str | None = None, limit: int = 200) -> list[dict]:
    conn = get_connection()
    if store_slug:
        rows = conn.execute(
            "SELECT * FROM ff_transfers WHERE store_slug = ? ORDER BY id DESC LIMIT ?",
            (store_slug, limit),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM ff_transfers ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_ff_warehouse_details_by_mp(store_slug: str, marketplace: str) -> list[dict]:

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT si.article, si.barcode, si.name, si.image_url,
               ff.fulfillment AS warehouse,
               SUM(ff.quantity) AS quantity
        FROM stock_items si
        JOIN ff_stock ff
            ON ff.store_slug = si.store_slug AND ff.article = si.article
        WHERE si.store_slug = ? AND si.marketplace = ? AND si.is_service = 0
          AND ff.marketplace = ?
        GROUP BY si.id, ff.fulfillment
        ORDER BY si.id, ff.fulfillment
        """,
        (store_slug, marketplace, marketplace),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
