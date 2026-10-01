"""Stored Ozon Performance API product statistics."""

import json

from app.repositories.core import WRITE_LOCK, get_connection


def save_snapshot(
    store_slug: str, period_from: str, period_to: str, products: dict[str, dict], synced_at: str
) -> None:
    with WRITE_LOCK, get_connection() as connection:
        connection.execute(
            """
            INSERT INTO ozon_advertising_snapshots
                (store_slug, period_from, period_to, payload_json, synced_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(store_slug, period_from, period_to) DO UPDATE SET
                payload_json = excluded.payload_json,
                synced_at = excluded.synced_at
            """,
            (store_slug, period_from, period_to, json.dumps(products), synced_at),
        )
        connection.commit()


def load_snapshots(
    store_slugs: tuple[str, ...], period_from: str, period_to: str
) -> dict[str, dict]:
    if not store_slugs:
        return {}
    placeholders = ", ".join("?" for _ in store_slugs)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT store_slug, payload_json, synced_at
              FROM ozon_advertising_snapshots
             WHERE store_slug IN ({placeholders})
               AND period_from = ? AND period_to = ?
            """,
            (*store_slugs, period_from, period_to),
        ).fetchall()
    return {
        str(row["store_slug"]): {
            "products": json.loads(row["payload_json"]),
            "synced_at": row["synced_at"],
        }
        for row in rows
    }
