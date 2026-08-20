import secrets
from uuid import uuid4

from app.repositories.core import WRITE_LOCK, get_connection


def _eligible_rows(connection, store_slug: str, fulfillment: str, month_key: str) -> list[dict]:
    rows = connection.execute(
        """
        WITH ff AS (
            SELECT article, SUM(quantity) AS ff_quantity
            FROM ff_stock
            WHERE store_slug = ? AND marketplace = 'WB' AND fulfillment = ?
            GROUP BY article
        ),
        fbs AS (
            SELECT article, SUM(quantity) AS fbs_quantity
            FROM mp_warehouse_stock
            WHERE store_slug = ? AND marketplace = 'WB'
              AND scheme = 'fbs' AND warehouse = ?
            GROUP BY article
        )
        SELECT si.article, si.barcode, si.name,
               COALESCE(ff.ff_quantity, 0) AS ff_quantity,
               COALESCE(fbs.fbs_quantity, 0) AS fbs_quantity,
               CASE WHEN history.id IS NULL THEN 0 ELSE 1 END AS used_this_month
        FROM stock_items si
        LEFT JOIN ff ON ff.article = si.article
        LEFT JOIN fbs ON fbs.article = si.article
        LEFT JOIN stock_audit_randomizations history
          ON history.month_key = ?
         AND history.article = si.article
        WHERE si.store_slug = ? AND si.marketplace = 'WB' AND si.is_service = 0
          AND (COALESCE(ff.ff_quantity, 0) > 0 OR COALESCE(fbs.fbs_quantity, 0) > 0)
        ORDER BY si.article
        """,
        (store_slug, fulfillment, store_slug, fulfillment, month_key, store_slug),
    ).fetchall()
    return [dict(row) for row in rows]


def _used_count(connection, store_slug: str, month_key: str) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM stock_audit_randomizations
        WHERE store_slug = ? AND month_key = ?
        """,
        (store_slug, month_key),
    ).fetchone()
    return int(row["total"] or 0) if row is not None else 0


def _selection_status(connection, store_slug: str, fulfillment: str, month_key: str) -> dict:
    eligible = _eligible_rows(connection, store_slug, fulfillment, month_key)
    unused = [row for row in eligible if not row["used_this_month"]]
    return {
        "eligible": eligible,
        "unused": unused,
        "eligible_count": len(eligible),
        "remaining_count": len(unused),
        "used_count": _used_count(connection, store_slug, month_key),
    }


def generate_stock_audit_sample(
    store_slugs: tuple[str, ...],
    fulfillment: str,
    month_key: str,
    user_id: int | None,
    user_name: str,
    generated_at: str,
) -> dict:
    batch_key = uuid4().hex
    selections = []
    with WRITE_LOCK:
        connection = get_connection()
        try:
            for store_slug in store_slugs:
                status = _selection_status(connection, store_slug, fulfillment, month_key)
                if not status["unused"]:
                    message = (
                        "Все подходящие артикулы уже попадали в сверку в этом месяце"
                        if status["eligible_count"]
                        else "Нет артикулов с положительным остатком на ФФ или WB FBS"
                    )
                    selections.append(
                        {
                            "store_slug": store_slug,
                            "article": None,
                            "barcode": None,
                            "name": None,
                            "ff_quantity": None,
                            "fbs_quantity": None,
                            "eligible_count": status["eligible_count"],
                            "remaining_count": 0,
                            "used_count": status["used_count"],
                            "message": message,
                        }
                    )
                    continue

                chosen = dict(secrets.choice(status["unused"]))
                connection.execute(
                    """
                    INSERT INTO stock_audit_randomizations
                        (batch_key, month_key, fulfillment, store_slug, article,
                         ff_quantity, fbs_quantity, user_id, user_name, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_key,
                        month_key,
                        fulfillment,
                        store_slug,
                        chosen["article"],
                        chosen["ff_quantity"],
                        chosen["fbs_quantity"],
                        user_id,
                        user_name,
                        generated_at,
                    ),
                )
                selections.append(
                    {
                        "store_slug": store_slug,
                        "article": str(chosen["article"]),
                        "barcode": str(chosen["barcode"] or ""),
                        "name": str(chosen["name"] or ""),
                        "ff_quantity": int(chosen["ff_quantity"] or 0),
                        "fbs_quantity": int(chosen["fbs_quantity"] or 0),
                        "eligible_count": status["eligible_count"],
                        "remaining_count": status["remaining_count"] - 1,
                        "used_count": status["used_count"] + 1,
                        "message": "",
                    }
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    return {
        "batch_key": batch_key,
        "month_key": month_key,
        "fulfillment": fulfillment,
        "generated_at": generated_at,
        "items": selections,
    }


def get_stock_audit_state(
    store_slugs: tuple[str, ...], fulfillment: str, month_key: str
) -> dict:
    if not store_slugs:
        return {"generated_at": None, "items": []}

    connection = get_connection()
    try:
        placeholders = ", ".join("?" for _ in store_slugs)
        latest = connection.execute(
            f"""
            SELECT batch_key, generated_at
            FROM stock_audit_randomizations
            WHERE month_key = ? AND fulfillment = ?
              AND store_slug IN ({placeholders})
            ORDER BY generated_at DESC, id DESC
            LIMIT 1
            """,
            (month_key, fulfillment, *store_slugs),
        ).fetchone()
        latest_by_store: dict[str, dict] = {}
        if latest is not None:
            rows = connection.execute(
                """
                SELECT history.store_slug, history.article, history.ff_quantity,
                       history.fbs_quantity, history.generated_at,
                       si.barcode, si.name
                FROM stock_audit_randomizations history
                LEFT JOIN stock_items si
                  ON si.store_slug = history.store_slug
                 AND si.marketplace = 'WB'
                 AND si.article = history.article
                WHERE history.batch_key = ?
                """,
                (latest["batch_key"],),
            ).fetchall()
            latest_by_store = {str(row["store_slug"]): dict(row) for row in rows}

        items = []
        for store_slug in store_slugs:
            status = _selection_status(connection, store_slug, fulfillment, month_key)
            current = latest_by_store.get(store_slug, {})
            items.append(
                {
                    "store_slug": store_slug,
                    "article": current.get("article"),
                    "barcode": current.get("barcode"),
                    "name": current.get("name"),
                    "ff_quantity": current.get("ff_quantity"),
                    "fbs_quantity": current.get("fbs_quantity"),
                    "eligible_count": status["eligible_count"],
                    "remaining_count": status["remaining_count"],
                    "used_count": status["used_count"],
                    "message": "",
                }
            )
        return {
            "generated_at": str(latest["generated_at"]) if latest is not None else None,
            "items": items,
        }
    finally:
        connection.close()
