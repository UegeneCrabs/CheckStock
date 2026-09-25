"""Source coverage is a successful atomic load, never a product's numeric value."""

from datetime import UTC, date, datetime, timedelta

from app.repositories.core import get_connection


def days(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def mark_loaded(conn, store, source, start, end):
    """Must share the source replacement's transaction; no independent commit."""
    conn.executemany(
        "INSERT INTO economics_source_days (marketplace,store_slug,source,day,updated_at) VALUES ('WB',?,?,?,?) "
        "ON CONFLICT(marketplace,store_slug,source,day) DO UPDATE SET updated_at=excluded.updated_at",
        [(store, source, day, datetime.now(UTC).isoformat()) for day in days(start, end)],
    )


def wb_days(stores, start, end):
    result = {store: {"orders": set(), "advertising": set()} for store in stores}
    if not stores:
        return result
    marks = ",".join("?" for _ in stores)
    with get_connection() as conn:
        for row in conn.execute(
            f"SELECT store_slug,source,day FROM economics_source_days WHERE marketplace='WB' AND store_slug IN ({marks}) AND day>=? AND day<=?",
            (*stores, start, end),
        ):
            result[row["store_slug"]][row["source"]].add(row["day"])
        # Legacy v4 funnel days were also replaced in one transaction after all
        # pages succeeded. Empty legacy days have no proof and remain unknown.
        for row in conn.execute(
            f"SELECT DISTINCT store_slug,day FROM wb_funnel_daily_orders WHERE store_slug IN ({marks}) AND day>=? AND day<=? AND source_version>=4",
            (*stores, start, end),
        ):
            result[row["store_slug"]]["orders"].add(row["day"])
        # The legacy advertising state proves the entire successful window,
        # including zeros; individual ad rows cannot establish full coverage.
        for row in conn.execute(
            f"SELECT store_slug,period_from,period_to FROM unit_economics_1c_wb_advertising_sync_state WHERE marketplace='WB' AND store_slug IN ({marks}) AND status='ok'",
            stores,
        ):
            if row["period_from"] and row["period_to"]:
                result[row["store_slug"]]["advertising"].update(days(max(start, row["period_from"]), min(end, row["period_to"])))
    return result
