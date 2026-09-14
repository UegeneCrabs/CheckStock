"""Daily Yandex history and current source snapshots, independent of WB ledgers."""

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
            if previous:
                migrate_order_snapshot(conn, dict(previous))
            _replace_daily(conn, store_slug, source, data, period_from, period_to, now)
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
    """Bound the compatibility cache; full history lives in the daily tables."""
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


def days_between(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first:
        raise ValueError("Конец периода раньше начала")
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def _replace_daily(conn, store: str, source: str, rows: list[dict], start: str, end: str, now: str):
    if source not in {"orders", "advertising"}:
        raise ValueError("Неизвестный источник дневной истории")
    days = days_between(start, end)
    encoded = []
    keys = set()
    for row in rows:
        key = (row["day"], row["article"])
        if not start <= key[0] <= end or key in keys or not key[1]:
            raise ValueError("Некорректный день или повтор товара в дневном отчёте")
        keys.add(key)
        encoded.append((store, source, *key, json.dumps(row, ensure_ascii=False, allow_nan=False), now))
    conn.execute(
        "DELETE FROM unit_economics_yandex_daily_metrics WHERE store_slug=? AND source=? AND day>=? AND day<=?",
        (store, source, start, end),
    )
    conn.executemany(
        "INSERT INTO unit_economics_yandex_daily_metrics (store_slug,source,day,article,data_json,updated_at) "
        "VALUES (?,?,?,?,?,?)",
        encoded,
    )
    conn.executemany(
        "INSERT INTO unit_economics_yandex_loaded_days (store_slug,source,day,updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(store_slug,source,day) DO UPDATE SET updated_at=excluded.updated_at",
        [(store, source, day, now) for day in days],
    )


def save_daily(store: str, source: str, rows: list[dict], start: str, end: str, now: str) -> None:
    """Replace only a fully downloaded window, including successful empty days."""
    with WRITE_LOCK, get_connection() as conn:
        _replace_daily(conn, store, source, rows, start, end, now)
        conn.commit()


def migrate_order_snapshot(conn, snapshot: dict) -> None:
    """Backfill known days once. Never turn a missing day into a zero or overwrite newer history."""
    if snapshot.get("data_json") is None:
        return
    store = snapshot["store_slug"]
    loaded = {
        row["day"]
        for row in conn.execute(
            "SELECT day FROM unit_economics_yandex_loaded_days WHERE store_slug=? AND source='orders'",
            (store,),
        )
    }
    rows = json.loads(snapshot["data_json"])
    for day in days_between(snapshot["period_from"], snapshot["period_to"]):
        if day not in loaded:
            _replace_daily(
                conn,
                store,
                "orders",
                [r for r in rows if r["day"] == day],
                day,
                day,
                snapshot["last_success_at"],
            )


def get_history(store: str, source: str, start: str, end: str) -> tuple[list[dict], set[str]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT data_json FROM unit_economics_yandex_daily_metrics "
            "WHERE store_slug=? AND source=? AND day>=? AND day<=? ORDER BY day,article",
            (store, source, start, end),
        ).fetchall()
        loaded = conn.execute(
            "SELECT day FROM unit_economics_yandex_loaded_days WHERE store_slug=? AND source=? AND day>=? AND day<=?",
            (store, source, start, end),
        ).fetchall()
    return [json.loads(row["data_json"]) for row in rows], {row["day"] for row in loaded}


def get_buyout_settings(store: str) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT buyout_period_days,default_buyout_percent FROM unit_economics_1c_cabinet_settings "
            "WHERE store_slug=? AND marketplace=?",
            (store, MARKETPLACE),
        ).fetchone()
    return dict(row) if row else {"buyout_period_days": 14, "default_buyout_percent": None}


def save_buyout_settings(
    store: str, period: int, default: float | None, now: str, user_id: int, user_name: str
):
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            "INSERT INTO unit_economics_1c_cabinet_settings "
            "(store_slug,marketplace,buyout_period_days,default_buyout_percent,updated_at,updated_by_user_id,updated_by_name) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(store_slug,marketplace) DO UPDATE SET "
            "buyout_period_days=excluded.buyout_period_days,default_buyout_percent=excluded.default_buyout_percent,"
            "updated_at=excluded.updated_at,updated_by_user_id=excluded.updated_by_user_id,updated_by_name=excluded.updated_by_name",
            (store, MARKETPLACE, period, default, now, user_id, user_name),
        )
        conn.commit()


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
