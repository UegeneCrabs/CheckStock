from app.dto.unit_economics_1c import (
    UnitEconomics1CCabinetSettings,
    UnitEconomics1CCabinetSettingsRequest,
    UnitEconomics1CCabinetSettingsWebRequest,
    UnitEconomics1CProductSettings,
    UnitEconomics1CProductSettingsRequest,
)
from app.repositories.core import WRITE_LOCK, get_connection

DAILY_PRICE_COLUMNS = (
    "store_slug",
    "article",
    "day",
    "marketplace",
    "nm_id",
    "size_id",
    "tech_size_name",
    "vendor_code",
    "currency",
    "seller_base_price",
    "retail_price",
    "club_discounted_price",
    "customer_price_with_spp",
    "customer_price_with_wallet",
    "customer_price_window_days",
    "customer_price_orders_count",
    "last_order_at",
    "orders_synced_at",
    "retail_synced_at",
    "updated_at",
)

DAILY_ADVERTISING_COLUMNS = (
    "store_slug",
    "nm_id",
    "day",
    "marketplace",
    "spend",
    "synced_at",
)


def list_active_wb_stock_items(store_slug: str | None = None) -> list[dict]:
    params: tuple[object, ...] = ()
    store_filter = ""
    if store_slug:
        store_filter = " AND items.store_slug=?"
        params = (store_slug,)
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT items.id, items.store_slug, items.article, items.barcode, items.name
          FROM stock_items items
         WHERE items.marketplace='WB' AND items.is_service=0
           {store_filter}
           AND NOT EXISTS (
               SELECT 1 FROM catalog_product_exclusions excluded
                WHERE excluded.store_slug=items.store_slug
                  AND excluded.marketplace=items.marketplace
                  AND (
                      excluded.nm_id=items.article
                      OR items.article LIKE excluded.nm_id || ' / %'
                  )
           )
         ORDER BY items.store_slug, items.id
        """,
        params,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def replace_product_classifications(rows: list[dict], synced_at: str) -> int:
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM unit_economics_1c_product_classifications")
            conn.executemany(
                """
                INSERT INTO unit_economics_1c_product_classifications
                    (stock_item_id, abc_code, turnover_days, source_article,
                     source_barcode, source_row, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["stock_item_id"],
                        row.get("abc_code"),
                        row["turnover_days"],
                        row.get("source_article"),
                        row.get("source_barcode"),
                        row.get("source_row"),
                        synced_at,
                    )
                    for row in rows
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return len(rows)


def replace_wb_commissions(rows: list[dict], synced_at: str) -> int:
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM unit_economics_1c_wb_commissions")
            conn.executemany(
                """
                INSERT INTO unit_economics_1c_wb_commissions
                    (category_key, category, commission_percent, synced_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        row["category_key"],
                        row["category"],
                        row["commission_percent"],
                        synced_at,
                    )
                    for row in rows
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return len(rows)


def replace_product_categories(store_slug: str, rows: list[dict], synced_at: str) -> int:
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                DELETE FROM unit_economics_1c_product_categories
                 WHERE stock_item_id IN (
                     SELECT id FROM stock_items WHERE store_slug=? AND marketplace='WB'
                 )
                """,
                (store_slug,),
            )
            conn.executemany(
                """
                INSERT INTO unit_economics_1c_product_categories
                    (stock_item_id, wb_subject_id, category, category_key, synced_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["stock_item_id"],
                        row.get("wb_subject_id"),
                        row.get("category"),
                        row.get("category_key"),
                        synced_at,
                    )
                    for row in rows
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return len(rows)


def product_classifications_due(threshold: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN classification.synced_at>=? THEN 1 ELSE 0 END) AS fresh
          FROM stock_items items
          LEFT JOIN unit_economics_1c_product_classifications classification
            ON classification.stock_item_id=items.id
         WHERE items.marketplace='WB' AND items.is_service=0
        """,
        (threshold,),
    ).fetchone()
    conn.close()
    return bool(row and int(row["total"] or 0) != int(row["fresh"] or 0))


def wb_commissions_due(threshold: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS total, MAX(synced_at) AS synced_at FROM unit_economics_1c_wb_commissions"
    ).fetchone()
    conn.close()
    return not row or int(row["total"] or 0) == 0 or str(row["synced_at"] or "") < threshold


def product_categories_due(store_slug: str, threshold: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN category.synced_at>=? THEN 1 ELSE 0 END) AS fresh
          FROM stock_items items
          LEFT JOIN unit_economics_1c_product_categories category
            ON category.stock_item_id=items.id
         WHERE items.store_slug=? AND items.marketplace='WB' AND items.is_service=0
        """,
        (threshold, store_slug),
    ).fetchone()
    conn.close()
    return bool(row and int(row["total"] or 0) != int(row["fresh"] or 0))


def get_product_reference_rows(store_slugs: tuple[str, ...]) -> list[dict]:
    if not store_slugs:
        return []
    placeholders = ", ".join("?" for _ in store_slugs)
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT items.store_slug, items.article,
               COALESCE(source.abc_code, classification.abc_code) AS abc_code,
               CASE UPPER(COALESCE(source.abc_code, classification.abc_code))
                   WHEN 'A' THEN 30
                   WHEN 'B' THEN 28
                   ELSE 21
               END AS turnover_days,
               source.purchase_price, source.fulfillment_cost, source.team_commission_percent,
               source.tag_raw, source.goal_week, source.goal_day,
               source.stock_status, source.stock_end_week,
               source.supplier_external_raw, source.fact_sales, source.plan_sales,
               source.source_sheet_title, source.source_row, source.synced_at AS source_synced_at,
               category.wb_subject_id, category.category,
               commission.commission_percent AS subject_commission_percent
          FROM stock_items items
          LEFT JOIN unit_economics_1c_product_classifications classification
            ON classification.stock_item_id=items.id
          LEFT JOIN unit_economics_1c_source_values source
            ON source.stock_item_id=items.id
          LEFT JOIN unit_economics_1c_product_categories category
            ON category.stock_item_id=items.id
          LEFT JOIN unit_economics_1c_wb_commissions commission
            ON commission.category_key=category.category_key
         WHERE items.store_slug IN ({placeholders})
           AND items.marketplace='WB' AND items.is_service=0
        """,
        store_slugs,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def replace_source_values(
    rows: list[dict],
    team_commissions: dict[str, float],
    synced_at: str,
) -> int:
    """Atomically replace the current Google Sheets snapshot without keeping history."""

    columns = (
        "stock_item_id",
        "purchase_price",
        "fulfillment_cost",
        "team_commission_percent",
        "tag_raw",
        "goal_week",
        "goal_day",
        "stock_status",
        "stock_end_week",
        "supplier_external_raw",
        "abc_code",
        "fact_sales",
        "plan_sales",
        "source_sheet_id",
        "source_sheet_title",
        "source_row",
        "synced_at",
    )
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM unit_economics_1c_source_values")
            conn.executemany(
                f"""
                INSERT INTO unit_economics_1c_source_values ({", ".join(columns)})
                VALUES ({", ".join("?" for _ in columns)})
                """,
                (
                    [row.get(column) if column != "synced_at" else synced_at for column in columns]
                    for row in rows
                ),
            )
            conn.executemany(
                """
                INSERT INTO unit_economics_1c_cabinet_settings
                    (store_slug, marketplace, team_commission_percent,
                     updated_at, updated_by_user_id, updated_by_name)
                VALUES (?, 'WB', ?, ?, 0, 'Google Sheets')
                ON CONFLICT(store_slug, marketplace) DO UPDATE SET
                    team_commission_percent=excluded.team_commission_percent,
                    updated_at=excluded.updated_at,
                    updated_by_user_id=excluded.updated_by_user_id,
                    updated_by_name=excluded.updated_by_name
                """,
                ((store_slug, commission, synced_at) for store_slug, commission in team_commissions.items()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return len(rows)


def get_cabinet_settings(store_slug: str) -> UnitEconomics1CCabinetSettings:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM unit_economics_1c_cabinet_settings WHERE store_slug=? AND marketplace='WB'",
        (store_slug,),
    ).fetchone()
    conn.close()
    if row is None:
        return UnitEconomics1CCabinetSettings(store_slug=store_slug)
    return UnitEconomics1CCabinetSettings.from_row(row)


def list_cabinet_settings(store_slugs: tuple[str, ...]) -> tuple[UnitEconomics1CCabinetSettings, ...]:
    return tuple(get_cabinet_settings(store_slug) for store_slug in store_slugs)


def save_cabinet_settings(
    store_slug: str,
    values: UnitEconomics1CCabinetSettingsRequest | UnitEconomics1CCabinetSettingsWebRequest,
    *,
    updated_at: str,
    updated_by_user_id: int,
    updated_by_name: str,
) -> UnitEconomics1CCabinetSettings:
    payload = values.model_dump(mode="python")
    columns = tuple(payload)
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                f"""
                INSERT INTO unit_economics_1c_cabinet_settings
                    (store_slug, marketplace, {", ".join(columns)},
                     updated_at, updated_by_user_id, updated_by_name)
                VALUES (?, 'WB', {", ".join("?" for _ in columns)}, ?, ?, ?)
                ON CONFLICT(store_slug, marketplace) DO UPDATE SET
                    {", ".join(f"{column}=excluded.{column}" for column in columns)},
                    updated_at=excluded.updated_at,
                    updated_by_user_id=excluded.updated_by_user_id,
                    updated_by_name=excluded.updated_by_name
                """,
                (
                    store_slug,
                    *(payload[column] for column in columns),
                    updated_at,
                    updated_by_user_id,
                    updated_by_name,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return get_cabinet_settings(store_slug)


def list_product_settings(store_slugs: tuple[str, ...]) -> tuple[UnitEconomics1CProductSettings, ...]:
    if not store_slugs:
        return ()
    placeholders = ", ".join("?" for _ in store_slugs)
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT *
          FROM unit_economics_1c_product_settings
         WHERE store_slug IN ({placeholders}) AND marketplace='WB'
         ORDER BY store_slug, article
        """,
        store_slugs,
    ).fetchall()
    conn.close()
    return tuple(UnitEconomics1CProductSettings.from_row(row) for row in rows)


def get_product_settings(store_slug: str, article: str) -> UnitEconomics1CProductSettings:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT *
          FROM unit_economics_1c_product_settings
         WHERE store_slug=? AND marketplace='WB' AND article=?
        """,
        (store_slug, article),
    ).fetchone()
    conn.close()
    if row is None:
        return UnitEconomics1CProductSettings(store_slug=store_slug, article=article)
    return UnitEconomics1CProductSettings.from_row(row)


def save_product_settings(
    store_slug: str,
    values: UnitEconomics1CProductSettingsRequest,
    *,
    updated_at: str,
    updated_by_user_id: int,
    updated_by_name: str,
) -> UnitEconomics1CProductSettings:
    payload = values.model_dump(mode="python", exclude={"article"})
    columns = tuple(payload)
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                f"""
                INSERT INTO unit_economics_1c_product_settings
                    (store_slug, marketplace, article, {", ".join(columns)},
                     updated_at, updated_by_user_id, updated_by_name)
                VALUES (?, 'WB', ?, {", ".join("?" for _ in columns)}, ?, ?, ?)
                ON CONFLICT(store_slug, marketplace, article) DO UPDATE SET
                    {", ".join(f"{column}=excluded.{column}" for column in columns)},
                    updated_at=excluded.updated_at,
                    updated_by_user_id=excluded.updated_by_user_id,
                    updated_by_name=excluded.updated_by_name
                """,
                (
                    store_slug,
                    values.article,
                    *(payload[column] for column in columns),
                    updated_at,
                    updated_by_user_id,
                    updated_by_name,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return get_product_settings(store_slug, values.article)


def upsert_daily_prices(rows: list[dict]) -> int:
    if not rows:
        return 0
    update_columns = DAILY_PRICE_COLUMNS[3:]
    placeholders = ", ".join("?" for _ in DAILY_PRICE_COLUMNS)
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.executemany(
                f"""
                INSERT INTO unit_economics_1c_wb_daily_prices
                    ({", ".join(DAILY_PRICE_COLUMNS)})
                VALUES ({placeholders})
                ON CONFLICT(store_slug, article, day) DO UPDATE SET
                    {", ".join(f"{column}=excluded.{column}" for column in update_columns)}
                """,
                ([row.get(column) for column in DAILY_PRICE_COLUMNS] for row in rows),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return len(rows)


def get_latest_daily_prices(store_slugs: tuple[str, ...]) -> list[dict]:
    if not store_slugs:
        return []
    placeholders = ", ".join("?" for _ in store_slugs)
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT prices.*
          FROM unit_economics_1c_wb_daily_prices prices
         WHERE prices.store_slug IN ({placeholders})
           AND prices.day = (
               SELECT MAX(candidate.day)
                 FROM unit_economics_1c_wb_daily_prices candidate
                WHERE candidate.store_slug = prices.store_slug
                  AND candidate.article = prices.article
           )
         ORDER BY prices.store_slug, prices.article
        """,
        store_slugs,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_latest_reliable_spp_prices(store_slug: str) -> list[dict]:
    """Return the newest price pair where WB actually exposed a buyer discount."""

    conn = get_connection()
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT article, retail_price, customer_price_with_spp, day, updated_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY article
                       ORDER BY day DESC, updated_at DESC
                   ) AS row_number
              FROM unit_economics_1c_wb_daily_prices
             WHERE store_slug=? AND marketplace='WB'
               AND retail_price IS NOT NULL AND retail_price > 0
               AND customer_price_with_spp IS NOT NULL
               AND customer_price_with_spp > 0
               AND customer_price_with_spp <= retail_price * 0.995
        )
        SELECT article, retail_price, customer_price_with_spp, day, updated_at
          FROM ranked
         WHERE row_number=1
         ORDER BY article
        """,
        (store_slug,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_daily_price_history(store_slug: str, article: str, limit: int = 365) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT *
          FROM unit_economics_1c_wb_daily_prices
         WHERE store_slug=? AND article=?
         ORDER BY day DESC
         LIMIT ?
        """,
        (store_slug, article, max(1, limit)),
    ).fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]


def get_wb_order_price_rows(store_slug: str, date_from: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT ordered_at, barcode, raw_json
          FROM sales_order_lines
         WHERE store_slug=?
           AND marketplace='WB'
           AND substr(ordered_at, 1, 10) >= ?
           AND cancelled_quantity=0
           AND raw_json IS NOT NULL
         ORDER BY ordered_at
        """,
        (store_slug, date_from),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def record_price_sync_state(
    store_slug: str,
    *,
    status: str,
    orders_ok: bool,
    retail_ok: bool,
    attempted_at: str,
    rows_saved: int,
    error: str | None,
) -> None:
    success_at = attempted_at if status in {"ok", "fallback"} else None
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO unit_economics_1c_wb_price_sync_state
                    (store_slug, marketplace, status, orders_ok, retail_ok,
                     last_attempt_at, last_success_at, rows_saved, error)
                VALUES (?, 'WB', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, marketplace) DO UPDATE SET
                    status=excluded.status,
                    orders_ok=excluded.orders_ok,
                    retail_ok=excluded.retail_ok,
                    last_attempt_at=excluded.last_attempt_at,
                    last_success_at=COALESCE(
                        excluded.last_success_at,
                        unit_economics_1c_wb_price_sync_state.last_success_at
                    ),
                    rows_saved=excluded.rows_saved,
                    error=excluded.error
                """,
                (
                    store_slug,
                    status,
                    int(orders_ok),
                    int(retail_ok),
                    attempted_at,
                    success_at,
                    rows_saved,
                    error,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def list_price_sync_states(store_slugs: tuple[str, ...]) -> list[dict]:
    if not store_slugs:
        return []
    placeholders = ", ".join("?" for _ in store_slugs)
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT *
          FROM unit_economics_1c_wb_price_sync_state
         WHERE store_slug IN ({placeholders}) AND marketplace='WB'
         ORDER BY store_slug
        """,
        store_slugs,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def replace_daily_advertising(
    store_slug: str,
    date_from: str,
    date_to: str,
    rows: list[dict],
) -> int:
    placeholders = ", ".join("?" for _ in DAILY_ADVERTISING_COLUMNS)
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                DELETE FROM unit_economics_1c_wb_daily_advertising
                 WHERE store_slug=? AND marketplace='WB' AND day>=? AND day<=?
                """,
                (store_slug, date_from, date_to),
            )
            if rows:
                conn.executemany(
                    f"""
                    INSERT INTO unit_economics_1c_wb_daily_advertising
                        ({", ".join(DAILY_ADVERTISING_COLUMNS)})
                    VALUES ({placeholders})
                    """,
                    ([row.get(column) for column in DAILY_ADVERTISING_COLUMNS] for row in rows),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return len(rows)


def get_daily_advertising(store_slugs: tuple[str, ...], date_from: str, date_to: str) -> list[dict]:
    if not store_slugs:
        return []
    placeholders = ", ".join("?" for _ in store_slugs)
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT store_slug, nm_id, day, spend, synced_at
          FROM unit_economics_1c_wb_daily_advertising
         WHERE store_slug IN ({placeholders})
           AND marketplace='WB'
           AND day>=? AND day<=?
         ORDER BY store_slug, nm_id, day
        """,
        (*store_slugs, date_from, date_to),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_wb_order_metric_rows(store_slugs: tuple[str, ...], date_from: str, date_to: str) -> list[dict]:
    if not store_slugs:
        return []
    placeholders = ", ".join("?" for _ in store_slugs)
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT store_slug, article, ordered_at, quantity, cancelled_quantity,
               sold_quantity, return_quantity, order_amount, cancelled_amount, raw_json
          FROM sales_order_lines
         WHERE store_slug IN ({placeholders})
           AND marketplace='WB'
           AND ordered_at>=? AND ordered_at<?
         ORDER BY store_slug, ordered_at
        """,
        (*store_slugs, date_from, date_to),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def record_advertising_sync_state(
    store_slug: str,
    *,
    status: str,
    date_from: str,
    date_to: str,
    attempted_at: str,
    rows_saved: int,
    campaigns_count: int,
    error: str | None,
) -> None:
    success_at = attempted_at if status == "ok" else None
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO unit_economics_1c_wb_advertising_sync_state
                    (store_slug, marketplace, status, period_from, period_to,
                     last_attempt_at, last_success_at, rows_saved, campaigns_count, error)
                VALUES (?, 'WB', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, marketplace) DO UPDATE SET
                    status=excluded.status,
                    period_from=excluded.period_from,
                    period_to=excluded.period_to,
                    last_attempt_at=excluded.last_attempt_at,
                    last_success_at=COALESCE(
                        excluded.last_success_at,
                        unit_economics_1c_wb_advertising_sync_state.last_success_at
                    ),
                    rows_saved=excluded.rows_saved,
                    campaigns_count=excluded.campaigns_count,
                    error=excluded.error
                """,
                (
                    store_slug,
                    status,
                    date_from,
                    date_to,
                    attempted_at,
                    success_at,
                    rows_saved,
                    campaigns_count,
                    error,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def list_advertising_sync_states(store_slugs: tuple[str, ...]) -> list[dict]:
    if not store_slugs:
        return []
    placeholders = ", ".join("?" for _ in store_slugs)
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT *
          FROM unit_economics_1c_wb_advertising_sync_state
         WHERE store_slug IN ({placeholders}) AND marketplace='WB'
         ORDER BY store_slug
        """,
        store_slugs,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
