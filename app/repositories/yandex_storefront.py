"""Latest verified prices and attempts, shared by the collector and the website."""

import json
from datetime import UTC, datetime, timedelta

from app.repositories import yandex_assortment
from app.repositories.core import WRITE_LOCK, get_connection
from app.yandex.storefront_schedule import next_run_at

LEASE_SECONDS = 600
PRICE_MAX_AGE_SECONDS = 2 * 60 * 60


def now() -> str:
    return datetime.now(UTC).isoformat()


def acquire(owner: str) -> bool:
    timestamp = now()
    expiry = (datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)).isoformat()
    with WRITE_LOCK, get_connection() as connection:
        result = connection.execute(
            """INSERT INTO yandex_storefront_lease (name, owner, expires_at) VALUES ('prices', ?, ?)
            ON CONFLICT(name) DO UPDATE SET owner=excluded.owner, expires_at=excluded.expires_at
            WHERE yandex_storefront_lease.expires_at < ? OR yandex_storefront_lease.owner = ?""",
            (owner, expiry, timestamp, owner),
        )
        connection.commit()
        return result.rowcount == 1


def release(owner: str) -> None:
    with WRITE_LOCK, get_connection() as connection:
        connection.execute("DELETE FROM yandex_storefront_lease WHERE name='prices' AND owner=?", (owner,))
        connection.commit()


def save_target(target: dict) -> None:
    encoded = json.dumps(target, ensure_ascii=False, allow_nan=False)
    with WRITE_LOCK, get_connection() as connection:
        previous = connection.execute(
            "SELECT target_json FROM yandex_storefront_prices WHERE store_slug=? AND article=?",
            (target["store_slug"], target["article"]),
        ).fetchone()
        old = json.loads(previous["target_json"] or "{}") if previous else {}
        changed = any(old.get(k) != target.get(k) for k in ("business_id", "market_sku", "card_id"))
        connection.execute(
            """INSERT INTO yandex_storefront_prices (store_slug, article, target_json, status, checked_at)
            VALUES (?, ?, ?, 'pending', ?)
            ON CONFLICT(store_slug, article) DO UPDATE SET target_json=excluded.target_json""",
            (target["store_slug"], target["article"], encoded, now()),
        )
        if changed:
            connection.execute(
                """UPDATE yandex_storefront_prices SET buyer_price=NULL, price_checked_at=NULL,
                seller_price=NULL, seller_checked_at=NULL, status='pending', message=NULL
                WHERE store_slug=? AND article=?""",
                (target["store_slug"], target["article"]),
            )
        connection.commit()


def record(target: dict, result: dict) -> None:
    timestamp = now()
    success = result.get("status") == "ok" and result.get("buyer_price") is not None
    with WRITE_LOCK, get_connection() as connection:
        connection.execute(
            """INSERT INTO yandex_storefront_prices (store_slug, article, status, checked_at, message)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(store_slug, article) DO UPDATE SET status=excluded.status,
            checked_at=excluded.checked_at, message=excluded.message""",
            (target["store_slug"], target["article"], result["status"], timestamp, result.get("message")),
        )
        if success:
            connection.execute(
                """UPDATE yandex_storefront_prices SET buyer_price=?, price_checked_at=?, currency=?
                WHERE store_slug=? AND article=?""",
                (result["buyer_price"], timestamp, result.get("currency", "RUR"), target["store_slug"], target["article"]),
            )
        connection.commit()


def seller_price(target: dict, value: float | None) -> None:
    with WRITE_LOCK, get_connection() as connection:
        connection.execute(
            """UPDATE yandex_storefront_prices SET seller_price=?, seller_checked_at=?
            WHERE store_slug=? AND article=?""",
            (value, now(), target["store_slug"], target["article"]),
        )
        connection.commit()


def add_missing_catalog_item(store_slug: str, product: dict) -> None:
    """Import only an explicitly selected, API-confirmed offer; preserve existing catalog rows."""
    with WRITE_LOCK, get_connection() as connection:
        connection.execute(
            """INSERT INTO stock_items
            (store_slug, marketplace, article, barcode, name, mp_sku, mp_updated_at, image_url, updated_at)
            VALUES (?, 'YANDEX MARKET', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_slug, marketplace, article) DO NOTHING""",
            (store_slug, product["article"], product["barcode"], product["name"],
             str(product.get("market_sku") or "") or None, product.get("updated_at"), product.get("image_url"), now()),
        )
        connection.commit()


def refresh_assortment() -> None:
    with WRITE_LOCK, get_connection() as connection:
        yandex_assortment.refresh(connection, yandex_assortment.load_active_products(), now())
        connection.commit()


def get_prices(store_slug: str) -> dict[str, dict]:
    with get_connection() as connection:
        return {row["article"]: dict(row) for row in connection.execute(
            "SELECT * FROM yandex_storefront_prices WHERE store_slug=?", (store_slug,),
        )}


def fresh(timestamp: str | None, at: datetime | None = None) -> bool:
    if not timestamp:
        return False
    try:
        checked = datetime.fromisoformat(timestamp)
        current = at or datetime.now(UTC)

        deadline = max(checked + timedelta(seconds=PRICE_MAX_AGE_SECONDS),
                       next_run_at(checked) + timedelta(hours=1))
        return checked <= current <= deadline
    except (ValueError, TypeError):
        return False


def start_run(run_id: str) -> None:
    with WRITE_LOCK, get_connection() as connection:
        connection.execute("INSERT INTO yandex_storefront_runs (run_id, started_at) VALUES (?, ?)", (run_id, now()))
        connection.commit()


def finish_run(run_id: str, report: dict) -> None:
    with WRITE_LOCK, get_connection() as connection:
        connection.execute(
            "UPDATE yandex_storefront_runs SET finished_at=?, report_json=? WHERE run_id=?",
            (now(), json.dumps(report, ensure_ascii=False, allow_nan=False), run_id),
        )
        connection.commit()
