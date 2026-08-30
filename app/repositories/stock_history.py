from app.repositories.core import WRITE_LOCK, get_connection

SUPPORTED_MARKETPLACE_SCHEMES = ("fbs", "fbo")


def replace_marketplace_stock_daily_history(
    store_slug: str,
    marketplace: str,
    scheme: str,
    day: str,
    captured_at: str,
) -> int:
    if scheme not in SUPPORTED_MARKETPLACE_SCHEMES:
        raise ValueError(f"unsupported marketplace stock scheme: {scheme}")

    source_schemes = ("fbs", "rfbs") if marketplace == "OZON" and scheme == "fbs" else (scheme,)
    scheme_placeholders = ", ".join("?" for _ in source_schemes)

    with WRITE_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                f"""
                SELECT items.store_slug, items.marketplace, items.article,
                       COALESCE(SUM(stock.quantity), 0) AS quantity
                  FROM stock_items items
                  LEFT JOIN mp_stock stock
                    ON stock.store_slug=items.store_slug
                   AND stock.marketplace=items.marketplace
                   AND stock.article=items.article
                   AND stock.scheme IN ({scheme_placeholders})
                 WHERE items.store_slug=? AND items.marketplace=? AND items.is_service=0
                 GROUP BY items.store_slug, items.marketplace, items.article
                 ORDER BY items.id
                """,
                (*source_schemes, store_slug, marketplace),
            ).fetchall()
            conn.execute(
                """
                DELETE FROM marketplace_stock_daily_history
                 WHERE store_slug=? AND marketplace=? AND scheme=? AND day=?
                """,
                (store_slug, marketplace, scheme, day),
            )
            conn.executemany(
                """
                INSERT INTO marketplace_stock_daily_history
                    (store_slug, marketplace, article, scheme, day, quantity, captured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        str(row["store_slug"]),
                        str(row["marketplace"]),
                        str(row["article"]),
                        scheme,
                        day,
                        int(row["quantity"] or 0),
                        captured_at,
                    )
                    for row in rows
                ),
            )
            conn.commit()
            return len(rows)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def replace_fulfillment_stock_daily_history(day: str, captured_at: str) -> int:
    """Save an end-of-day FF snapshot, preserving zeroes for previously seen product/FF pairs."""

    with WRITE_LOCK:
        conn = get_connection()
        try:
            previous_rows = conn.execute(
                """
                SELECT DISTINCT history.store_slug, history.marketplace,
                       history.article, history.fulfillment
                  FROM fulfillment_stock_daily_history history
                  JOIN stock_items items
                    ON items.store_slug=history.store_slug
                   AND items.marketplace=history.marketplace
                   AND items.article=history.article
                 WHERE items.is_service=0
                """
            ).fetchall()
            current_rows = conn.execute(
                """
                SELECT stock.store_slug, stock.marketplace, stock.article,
                       stock.fulfillment, stock.quantity
                  FROM ff_stock stock
                  JOIN stock_items items
                    ON items.store_slug=stock.store_slug
                   AND items.marketplace=stock.marketplace
                   AND items.article=stock.article
                 WHERE items.is_service=0
                """
            ).fetchall()

            quantities = {
                (
                    str(row["store_slug"]),
                    str(row["marketplace"]),
                    str(row["article"]),
                    str(row["fulfillment"]),
                ): 0
                for row in previous_rows
            }
            for row in current_rows:
                key = (
                    str(row["store_slug"]),
                    str(row["marketplace"]),
                    str(row["article"]),
                    str(row["fulfillment"]),
                )
                quantities[key] = int(row["quantity"] or 0)

            conn.execute("DELETE FROM fulfillment_stock_daily_history WHERE day=?", (day,))
            conn.executemany(
                """
                INSERT INTO fulfillment_stock_daily_history
                    (store_slug, marketplace, article, fulfillment, day, quantity, captured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ((*key, day, quantity, captured_at) for key, quantity in sorted(quantities.items())),
            )
            conn.commit()
            return len(quantities)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def get_daily_stock_history(
    store_slugs: tuple[str, ...],
    marketplace: str,
    date_from: str,
    date_to: str,
) -> list[dict]:
    if not store_slugs:
        return []

    placeholders = ", ".join("?" for _ in store_slugs)
    params = (*store_slugs, marketplace, date_from, date_to)
    conn = get_connection()
    try:
        marketplace_rows = conn.execute(
            f"""
            SELECT store_slug, article, day,
                   SUM(CASE WHEN scheme='fbs' THEN quantity END) AS fbs,
                   SUM(CASE WHEN scheme='fbo' THEN quantity END) AS fbo
              FROM marketplace_stock_daily_history
             WHERE store_slug IN ({placeholders}) AND marketplace=?
               AND day>=? AND day<=?
             GROUP BY store_slug, article, day
            """,
            params,
        ).fetchall()
        fulfillment_rows = conn.execute(
            f"""
            SELECT store_slug, article, day, SUM(quantity) AS fulfillment
              FROM fulfillment_stock_daily_history
             WHERE store_slug IN ({placeholders}) AND marketplace=?
               AND day>=? AND day<=?
             GROUP BY store_slug, article, day
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    by_key: dict[tuple[str, str, str], dict] = {}
    for row in marketplace_rows:
        key = (str(row["store_slug"]), str(row["article"]), str(row["day"]))
        by_key[key] = {
            "store_slug": key[0],
            "article": key[1],
            "day": key[2],
            "fbs": int(row["fbs"]) if row["fbs"] is not None else None,
            "fbo": int(row["fbo"]) if row["fbo"] is not None else None,
            "fulfillment": None,
        }
    for row in fulfillment_rows:
        key = (str(row["store_slug"]), str(row["article"]), str(row["day"]))
        item = by_key.setdefault(
            key,
            {
                "store_slug": key[0],
                "article": key[1],
                "day": key[2],
                "fbs": None,
                "fbo": None,
                "fulfillment": None,
            },
        )
        item["fulfillment"] = int(row["fulfillment"] or 0)
    return [by_key[key] for key in sorted(by_key)]


def get_fulfillment_stock_daily_history(
    store_slug: str,
    marketplace: str,
    article: str,
    date_from: str,
    date_to: str,
) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT store_slug, marketplace, article, fulfillment,
                   day, quantity, captured_at
              FROM fulfillment_stock_daily_history
             WHERE store_slug=? AND marketplace=? AND article=?
               AND day>=? AND day<=?
             ORDER BY day, fulfillment
            """,
            (store_slug, marketplace, article, date_from, date_to),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]
