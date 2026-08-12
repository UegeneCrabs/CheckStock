from app.repositories.core import WRITE_LOCK, get_connection


def get_fulfillments() -> list[str]:
    conn = get_connection()
    rows = conn.execute("SELECT name FROM fulfillments ORDER BY id").fetchall()
    conn.close()
    return [row["name"] for row in rows]


def get_fulfillment_unit_rates() -> list[dict]:

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            f.name,
            r.storage_per_m3_day,
            r.acceptance_per_unit,
            r.fulfillment_per_unit,
            r.updated_at
        FROM fulfillments f
        LEFT JOIN fulfillment_unit_rates r ON r.fulfillment = f.name
        ORDER BY f.id
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def upsert_fulfillment_unit_rates(entries: list[dict], updated_at: str) -> int:

    if not entries:
        return 0

    with WRITE_LOCK:
        conn = get_connection()
        try:
            known_names = {
                str(row["name"]) for row in conn.execute("SELECT name FROM fulfillments").fetchall()
            }
            unknown = {str(entry.get("name") or "") for entry in entries} - known_names
            if unknown:
                raise ValueError(f"Unknown fulfillments: {', '.join(sorted(unknown))}")

            conn.executemany(
                """
                INSERT INTO fulfillment_unit_rates
                    (fulfillment, storage_per_m3_day, acceptance_per_unit,
                     fulfillment_per_unit, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(fulfillment) DO UPDATE SET
                    storage_per_m3_day = excluded.storage_per_m3_day,
                    acceptance_per_unit = excluded.acceptance_per_unit,
                    fulfillment_per_unit = excluded.fulfillment_per_unit,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        entry["name"],
                        entry.get("storage"),
                        entry.get("accept"),
                        entry.get("fulfillment"),
                        updated_at,
                    )
                    for entry in entries
                ],
            )
            conn.commit()
        finally:
            conn.close()
    return len(entries)


def replace_unit_costs(store_slug: str, entries: list[dict], source_sheet_gid: str, updated_at: str) -> int:

    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM unit_costs WHERE store_slug = ?", (store_slug,))
            conn.executemany(
                """
                INSERT INTO unit_costs
                    (store_slug, article, purchase_price, other_cost,
                     source_sheet_gid, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        store_slug,
                        entry["article"],
                        entry["purchase_price"],
                        entry.get("other_cost"),
                        source_sheet_gid,
                        updated_at,
                    )
                    for entry in entries
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return len(entries)


def get_unit_costs(store_slug: str) -> dict[str, dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT article, purchase_price, other_cost, source_sheet_gid, updated_at
        FROM unit_costs WHERE store_slug = ?
        """,
        (store_slug,),
    ).fetchall()
    conn.close()
    return {str(row["article"]): dict(row) for row in rows}


def upsert_wb_unit_references(store_slug: str, entries: list[dict], updated_at: str) -> int:

    if not entries:
        return 0
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.executemany(
                """
                INSERT INTO wb_unit_metrics
                    (store_slug, article, nm_id, tech_size, subject_id, category,
                     length_cm, width_cm, height_cm, volume_l, weight_kg,
                     commission_fbs_rate, reference_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, article) DO UPDATE SET
                    nm_id = excluded.nm_id,
                    tech_size = excluded.tech_size,
                    subject_id = excluded.subject_id,
                    category = excluded.category,
                    length_cm = excluded.length_cm,
                    width_cm = excluded.width_cm,
                    height_cm = excluded.height_cm,
                    volume_l = excluded.volume_l,
                    weight_kg = excluded.weight_kg,
                    commission_fbs_rate = excluded.commission_fbs_rate,
                    reference_updated_at = excluded.reference_updated_at
                """,
                [
                    (
                        store_slug,
                        entry["article"],
                        entry.get("nm_id"),
                        entry.get("tech_size") or "",
                        entry.get("subject_id"),
                        entry.get("category") or "",
                        entry.get("length_cm"),
                        entry.get("width_cm"),
                        entry.get("height_cm"),
                        entry.get("volume_l"),
                        entry.get("weight_kg"),
                        entry.get("commission_fbs_rate"),
                        updated_at,
                    )
                    for entry in entries
                ],
            )
            conn.commit()
        finally:
            conn.close()
    return len(entries)


def upsert_wb_unit_prices(store_slug: str, entries: list[dict], updated_at: str) -> int:

    if not entries:
        return 0
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.executemany(
                """
                INSERT INTO wb_unit_metrics
                    (store_slug, article, nm_id, tech_size, list_price,
                     discounted_price, club_discounted_price, buyer_price,
                     spp_percent, buyer_price_observed_at, price_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, article) DO UPDATE SET
                    nm_id = excluded.nm_id,
                    tech_size = excluded.tech_size,
                    list_price = COALESCE(excluded.list_price, wb_unit_metrics.list_price),
                    discounted_price = COALESCE(excluded.discounted_price, wb_unit_metrics.discounted_price),
                    club_discounted_price = COALESCE(excluded.club_discounted_price, wb_unit_metrics.club_discounted_price),
                    buyer_price = COALESCE(excluded.buyer_price, wb_unit_metrics.buyer_price),
                    spp_percent = COALESCE(excluded.spp_percent, wb_unit_metrics.spp_percent),
                    buyer_price_observed_at = COALESCE(
                        excluded.buyer_price_observed_at,
                        wb_unit_metrics.buyer_price_observed_at
                    ),
                    price_updated_at = excluded.price_updated_at
                """,
                [
                    (
                        store_slug,
                        entry["article"],
                        entry.get("nm_id"),
                        entry.get("tech_size") or "",
                        entry.get("list_price"),
                        entry.get("discounted_price"),
                        entry.get("club_discounted_price"),
                        entry.get("buyer_price"),
                        entry.get("spp_percent"),
                        entry.get("buyer_price_observed_at"),
                        updated_at,
                    )
                    for entry in entries
                ],
            )
            conn.commit()
        finally:
            conn.close()
    return len(entries)


def get_wb_unit_metrics(store_slug: str) -> dict[str, dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM wb_unit_metrics WHERE store_slug = ?",
        (store_slug,),
    ).fetchall()
    conn.close()
    return {str(row["article"]): dict(row) for row in rows}


def get_wb_price_last_sync(store_slug: str) -> str | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(price_updated_at) AS updated_at FROM wb_unit_metrics WHERE store_slug = ?",
        (store_slug,),
    ).fetchone()
    conn.close()
    return row["updated_at"] if row else None
