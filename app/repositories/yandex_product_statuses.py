"""Persistent product newness, independent of assortment visibility and economics."""

from datetime import date, timedelta

from app.repositories import yandex_assortment
from app.repositories.core import WRITE_LOCK, get_connection


def check_period(today: date) -> tuple[date, date]:
    """Seven complete days before the most recent 21 complete days (Moscow)."""
    return today - timedelta(days=28), today - timedelta(days=22)


def get_statuses(store_slug: str) -> dict[str, dict]:
    with get_connection() as connection:
        return {
            row["article"]: dict(row)
            for row in connection.execute(
                "SELECT * FROM unit_economics_yandex_product_statuses WHERE store_slug=?",
                (store_slug,),
            )
        }


def pending_articles(store_slug: str, today: date) -> set[str]:
    statuses = get_statuses(store_slug)
    return {
        article
        for article in yandex_assortment.active_articles(store_slug)
        if (statuses.get(article) or {}).get("status") != "old"
        and str((statuses.get(article) or {}).get("checked_on") or "") < today.isoformat()
    }


def save_check(store_slug: str, articles: set[str], orders: list[dict], today: date, now: str) -> int:
    """Call only after the whole week loaded; never reclassify an ordinary product."""
    start, end = (value.isoformat() for value in check_period(today))
    first_orders = {}
    for row in orders:
        article, day = row["article"], row["day"]
        if article in articles and start <= day <= end and int(row["orders_count"]) > 0:
            first_orders[article] = min(first_orders.get(article, day), day)
    with WRITE_LOCK, get_connection() as connection:
        for article in sorted(articles):
            order_date = first_orders.get(article)
            connection.execute(
                """
                INSERT INTO unit_economics_yandex_product_statuses
                    (store_slug, article, status, checked_on, period_from, period_to, order_date, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, article) DO UPDATE SET
                    status=excluded.status, checked_on=excluded.checked_on,
                    period_from=excluded.period_from, period_to=excluded.period_to,
                    order_date=excluded.order_date, updated_at=excluded.updated_at
                WHERE unit_economics_yandex_product_statuses.status <> 'old'
                    AND unit_economics_yandex_product_statuses.checked_on < excluded.checked_on
                """,
                (
                    store_slug,
                    article,
                    "old" if order_date else "new",
                    today.isoformat(),
                    start,
                    end,
                    order_date,
                    now,
                ),
            )
        connection.commit()
    return len(articles)
