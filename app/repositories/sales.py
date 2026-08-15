from app.repositories.core import WRITE_LOCK, get_connection


def upsert_sales_order_lines(lines: list[dict], synced_at: str) -> int:

    if not lines:
        return 0

    columns = (
        "store_slug",
        "marketplace",
        "order_key",
        "line_key",
        "external_order_id",
        "scheme",
        "status",
        "substatus",
        "article",
        "barcode",
        "name",
        "ordered_at",
        "source_updated_at",
        "cancelled_at",
        "sold_at",
        "returned_at",
        "quantity",
        "cancelled_quantity",
        "sold_quantity",
        "return_quantity",
        "order_amount",
        "cancelled_amount",
        "sale_amount",
        "return_amount",
        "currency",
        "raw_json",
        "synced_at",
    )
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(
        f"{column} = excluded.{column}"
        for column in columns
        if column not in {"store_slug", "marketplace", "order_key", "line_key"}
    )

    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.executemany(
                f"""
                INSERT INTO sales_order_lines ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(store_slug, marketplace, order_key, line_key)
                DO UPDATE SET {updates}
                """,
                [
                    tuple(
                        line.get(
                            column,
                            synced_at
                            if column == "synced_at"
                            else (0 if column in {"return_quantity", "return_amount"} else None),
                        )
                        for column in columns
                    )
                    for line in lines
                ],
            )
            conn.commit()
        finally:
            conn.close()
    return len(lines)


def sales_has_history(store_slug: str, marketplace: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM sales_order_lines WHERE store_slug = ? AND marketplace = ? LIMIT 1",
        (store_slug, marketplace),
    ).fetchone()
    conn.close()
    return row is not None


def get_open_fbs_order_totals(store_slug: str, marketplace: str) -> dict[str, int]:
    """Количество ещё не отменённых и не выкупленных FBS-единиц по артикулу."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT article,
               SUM(
                   CASE
                       WHEN quantity - cancelled_quantity - sold_quantity > 0
                       THEN quantity - cancelled_quantity - sold_quantity
                       ELSE 0
                   END
               ) AS total
          FROM sales_order_lines
         WHERE store_slug = ?
           AND marketplace = ?
           AND scheme = 'fbs'
           AND article <> ''
         GROUP BY article
        HAVING SUM(
                   CASE
                       WHEN quantity - cancelled_quantity - sold_quantity > 0
                       THEN quantity - cancelled_quantity - sold_quantity
                       ELSE 0
                   END
               ) > 0
        """,
        (store_slug, marketplace),
    ).fetchall()
    conn.close()
    return {str(row["article"]): int(row["total"] or 0) for row in rows}


def record_sales_sync(
    store_slug: str,
    marketplace: str,
    ok: bool,
    error: str | None,
    rows_received: int,
    lookback_days: int,
    attempted_at: str,
) -> None:
    with WRITE_LOCK:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO sales_sync_state
                (store_slug, marketplace, last_attempt_at, last_success_at,
                 ok, error, rows_received, lookback_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_slug, marketplace) DO UPDATE SET
                last_attempt_at = excluded.last_attempt_at,
                last_success_at = COALESCE(excluded.last_success_at, sales_sync_state.last_success_at),
                ok = excluded.ok,
                error = excluded.error,
                rows_received = excluded.rows_received,
                lookback_days = CASE
                    WHEN sales_sync_state.lookback_days >= excluded.lookback_days
                    THEN sales_sync_state.lookback_days ELSE excluded.lookback_days END
            """,
            (
                store_slug,
                marketplace,
                attempted_at,
                attempted_at if ok else None,
                1 if ok else 0,
                error,
                rows_received,
                lookback_days,
            ),
        )
        conn.commit()
        conn.close()


def get_sales_sync_states(marketplace: str, store_slug: str | None = None) -> list[dict]:
    conn = get_connection()
    sql = "SELECT * FROM sales_sync_state WHERE marketplace = ?"
    params: list = [marketplace]
    if store_slug:
        sql += " AND store_slug = ?"
        params.append(store_slug)
    sql += " ORDER BY store_slug"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_sales_daily(
    date_from: str, date_to: str, marketplace: str, store_slug: str | None = None
) -> list[dict]:

    store_sql = " AND store_slug = ?" if store_slug else ""
    order_params: list = [marketplace, date_from, date_to]
    sale_params: list = [marketplace, date_from, date_to]
    if store_slug:
        order_params.append(store_slug)
        sale_params.append(store_slug)

    conn = get_connection()
    orders = conn.execute(
        f"""
        SELECT substr(ordered_at, 1, 10) AS day,
               SUM(CASE WHEN order_amount - cancelled_amount > 0
                        THEN order_amount - cancelled_amount ELSE 0 END) AS orders_amount,
               SUM(CASE WHEN scheme = 'fbo'
                         AND order_amount - cancelled_amount > 0
                        THEN order_amount - cancelled_amount ELSE 0 END) AS fbo_amount,
               SUM(CASE WHEN scheme = 'fbs'
                         AND order_amount - cancelled_amount > 0
                        THEN order_amount - cancelled_amount ELSE 0 END) AS fbs_amount,
               SUM(cancelled_amount) AS cancellations_amount,
               SUM(CASE WHEN quantity - cancelled_quantity > 0
                        THEN quantity - cancelled_quantity ELSE 0 END) AS orders_count,
               SUM(cancelled_quantity) AS cancellations_count
          FROM sales_order_lines
         WHERE marketplace = ? AND ordered_at >= ? AND ordered_at < ?{store_sql}
         GROUP BY substr(ordered_at, 1, 10)
        """,
        order_params,
    ).fetchall()
    sales = conn.execute(
        f"""
        SELECT substr(sold_at, 1, 10) AS day,
               SUM(sale_amount) AS sales_amount,
               SUM(sold_quantity) AS sales_count
          FROM sales_order_lines
         WHERE marketplace = ? AND sold_at >= ? AND sold_at < ?{store_sql}
         GROUP BY substr(sold_at, 1, 10)
        """,
        sale_params,
    ).fetchall()
    conn.close()

    by_day: dict[str, dict] = {}
    for row in orders:
        by_day[row["day"]] = dict(row)
    for row in sales:
        day = by_day.setdefault(row["day"], {"day": row["day"]})
        day.update(dict(row))
    return [by_day[day] for day in sorted(by_day)]


def get_sales_available_range(marketplace: str, store_slug: str | None = None) -> dict:
    conn = get_connection()
    sql = (
        "SELECT MIN(substr(ordered_at, 1, 10)) AS date_from, "
        "MAX(substr(ordered_at, 1, 10)) AS date_to "
        "FROM sales_order_lines WHERE marketplace = ?"
    )
    params: list = [marketplace]
    if store_slug:
        sql += " AND store_slug = ?"
        params.append(store_slug)
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else {"date_from": None, "date_to": None}


def get_sales_export_rows(
    date_from: str, date_to: str, marketplace: str, store_slug: str | None = None
) -> list[dict]:
    conn = get_connection()
    sql = (
        "SELECT store_slug, marketplace, external_order_id, ordered_at, sold_at, "
        "scheme, status, substatus, article, barcode, name, quantity, "
        "cancelled_quantity, sold_quantity, order_amount, cancelled_amount, "
        "sale_amount, currency FROM sales_order_lines "
        "WHERE marketplace = ? AND ordered_at >= ? AND ordered_at < ?"
    )
    params: list = [marketplace, date_from, date_to]
    if store_slug:
        sql += " AND store_slug = ?"
        params.append(store_slug)
    sql += " ORDER BY ordered_at, store_slug, external_order_id, line_key"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]
