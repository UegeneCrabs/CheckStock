"""Isolated Yandex snapshots; existing sales and WB calculations are read-only here."""

import json
from datetime import date, timedelta

from app.repositories.core import WRITE_LOCK, get_connection

MARKETPLACE = "YANDEX MARKET"
ORDER_HISTORY_DAYS = 21


def get_snapshots(store_slug: str) -> dict[str, dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM unit_economics_yandex_snapshots WHERE store_slug=?",
            (store_slug,),
        ).fetchall()
    result = {}
    for row in rows:
        snapshot = dict(row)
        snapshot["data"] = json.loads(snapshot.pop("data_json") or "null")
        result[snapshot["source"]] = snapshot
    return result


def save_snapshot(
    store_slug: str,
    source: str,
    data: list[dict],
    period_from: str,
    period_to: str,
    now: str,
) -> None:
    with WRITE_LOCK, get_connection() as conn:
        if source == "orders":
            previous = conn.execute(
                "SELECT * FROM unit_economics_yandex_snapshots WHERE store_slug=? AND source='orders'",
                (store_slug,),
            ).fetchone()
            data, period_from, period_to = _merge_orders_window(
                dict(previous) if previous else {}, data, period_from, period_to
            )
        conn.execute(
            """
            INSERT INTO unit_economics_yandex_snapshots
                (store_slug, source, period_from, period_to, data_json,
                 last_success_at, last_attempt_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(store_slug, source) DO UPDATE SET
                period_from=excluded.period_from, period_to=excluded.period_to,
                data_json=excluded.data_json, last_success_at=excluded.last_success_at,
                last_attempt_at=excluded.last_attempt_at, error=NULL
            """,
            (
                store_slug,
                source,
                period_from,
                period_to,
                json.dumps(data, ensure_ascii=False, allow_nan=False),
                now,
                now,
            ),
        )
        conn.commit()


def _merge_orders_window(
    previous: dict, rows: list[dict], start: str, end: str
) -> tuple[list[dict], str, str]:
    """Retain 21 days; replace empty days too, and never count gaps as loaded history."""
    old_rows = json.loads(previous.get("data_json") or "null")
    period_from, period_to = start, end
    if old_rows is not None:
        old_start, old_end = previous["period_from"], previous["period_to"]
        if (
            old_start <= (date.fromisoformat(end) + timedelta(days=1)).isoformat()
            and old_end >= (date.fromisoformat(start) - timedelta(days=1)).isoformat()
        ):
            period_from, period_to = min(start, old_start), max(end, old_end)
    cutoff = (date.fromisoformat(period_to) - timedelta(days=ORDER_HISTORY_DAYS - 1)).isoformat()
    merged = {
        (row["article"], row["day"]): row
        for row in old_rows or []
        if not start <= row["day"] <= end and cutoff <= row["day"] <= period_to
    }
    merged.update({(row["article"], row["day"]): row for row in rows if start <= row["day"] <= end})
    return list(merged.values()), max(period_from, cutoff), period_to


def record_error(store_slug: str, source: str, error: str, now: str) -> None:
    """Keep the last successful payload and its actual period on any API failure."""
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            """
            INSERT INTO unit_economics_yandex_snapshots (store_slug, source, last_attempt_at, error)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(store_slug, source) DO UPDATE SET
                last_attempt_at=excluded.last_attempt_at, error=excluded.error
            """,
            (store_slug, source, now, error),
        )
        conn.commit()


def get_daily_orders(store_slug: str, date_from: str, date_to_exclusive: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT article, substr(ordered_at, 1, 10) AS day,
                   SUM(quantity) AS orders_count, SUM(order_amount) AS orders_amount,
                   SUM(cancelled_quantity) AS cancel_count,
                   SUM(cancelled_amount) AS cancel_amount,
                   SUM(sold_quantity) AS sold_count
              FROM sales_order_lines
             WHERE store_slug=? AND marketplace=? AND ordered_at>=? AND ordered_at<?
             GROUP BY article, substr(ordered_at, 1, 10)
            """,
            (store_slug, MARKETPLACE, date_from, date_to_exclusive),
        ).fetchall()
    return [dict(row) for row in rows]
