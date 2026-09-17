"""Persistent visibility of Yandex products in unit economics only."""

import json
from pathlib import Path

from app.core.stores import STORES
from app.infrastructure.database import DatabaseConnection
from app.repositories.core import get_connection

ASSORTMENT_PATH = Path(__file__).resolve().parents[1] / "yandex" / "unit_economics_assortment.json"


def load_active_products() -> set[tuple[str, str]]:
    rows = json.loads(ASSORTMENT_PATH.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Список актуальных товаров ЯМ не должен быть пустым")
    products = set()
    for row in rows:
        store, article = row["store_slug"], row["article"]
        if store not in STORES or not isinstance(article, str) or not article.strip():
            raise ValueError("Некорректный магазин или артикул в списке товаров ЯМ")
        key = (store, article.strip())
        if key in products:
            raise ValueError("Повтор товара в списке актуальных товаров ЯМ")
        products.add(key)
    return products


def refresh(
    connection: DatabaseConnection,
    active: set[tuple[str, str]],
    now: str,
    store_slug: str | None = None,
) -> None:
    """Classify the catalog and retain approved products that are not yet in it."""
    products = {
        (row["store_slug"], row["article"])
        for row in connection.execute(
            """
            SELECT store_slug, article FROM stock_items WHERE marketplace='YANDEX MARKET'
            UNION SELECT store_slug, article FROM unit_economics_yandex_assortment
            """
        )
    } | active
    for store, article in sorted(products):
        if store_slug is not None and store != store_slug:
            continue
        connection.execute(
            """
            INSERT INTO unit_economics_yandex_assortment (store_slug, article, is_legacy, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(store_slug, article) DO UPDATE SET
                is_legacy=excluded.is_legacy, updated_at=excluded.updated_at
            WHERE unit_economics_yandex_assortment.is_legacy <> excluded.is_legacy
            """,
            (store, article, int((store, article) not in active), now),
        )


def active_articles(store_slug: str) -> set[str]:
    """All non-archived catalog offers; dashboard visibility depends on stock/turnover."""
    with get_connection() as connection:
        articles = {
            row["article"]
            for row in connection.execute(
                "SELECT article FROM stock_items WHERE store_slug=? "
                "AND marketplace='YANDEX MARKET' AND is_service=0",
                (store_slug,),
            )
        }
    return articles - archived_articles(store_slug)


def archived_articles(store_slug: str) -> set[str]:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT data_json FROM unit_economics_yandex_snapshots WHERE store_slug=? AND source='archive'",
            (store_slug,),
        ).fetchone()
    return {item["article"] for item in json.loads(row["data_json"] or "[]")} if row else set()
