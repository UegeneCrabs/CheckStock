"""Date bounds of saved, versioned WB funnel orders."""

from app.repositories.core import get_connection


def funnel_bounds(stores: tuple[str, ...], today: str) -> list[dict]:
    if not stores:
        return []
    marks = ",".join("?" for _ in stores)
    with get_connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT store_slug,article,MIN(day) AS first_day,MAX(day) AS last_day "
                f"FROM wb_funnel_daily_orders WHERE store_slug IN ({marks}) "
                "AND source_version>=3 AND day<=? GROUP BY store_slug,article",
                (*stores, today),
            )
        ]


def coverage_bounds(stores: tuple[str, ...], today: str) -> list[dict]:
    """Successful empty days also belong to the available order history."""
    if not stores:
        return []
    marks = ",".join("?" for _ in stores)
    with get_connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT store_slug,MIN(day) AS first_day,MAX(day) AS last_day "
                f"FROM economics_source_days WHERE store_slug IN ({marks}) "
                "AND marketplace='WB' AND source='orders' AND day<=? GROUP BY store_slug",
                (*stores, today),
            )
        ]
