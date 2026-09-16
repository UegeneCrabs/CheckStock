"""Latest verified prices and attempts, shared by the collector and the website."""

import json
from datetime import UTC, datetime, timedelta

from app.core.domain import MOSCOW_TIMEZONE
from app.repositories import yandex_assortment
from app.repositories.core import WRITE_LOCK, get_connection
from app.yandex.price_calculation import discount_percent, discounted_price, paired_at, positive
from app.yandex.storefront_schedule import next_run_at

LEASE_SECONDS = 600
PRICE_MAX_AGE_SECONDS = 2 * 60 * 60


def cooldown(at: datetime | None = None) -> dict | None:
    """Respect only an explicit Retry-After from Yandex, never a locally imposed pause."""
    current = at or datetime.now(UTC)
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT finished_at, report_json FROM yandex_storefront_runs "
            "WHERE finished_at IS NOT NULL ORDER BY finished_at DESC",
        ).fetchall()
    latest = None
    for row in rows:
        report = json.loads(row["report_json"] or "{}")
        if report.get("status") != "blocked" or not report.get("server_retry_after"):
            continue
        deadline = datetime.fromisoformat(report["server_retry_after"])
        if deadline > current and (latest is None or deadline > latest[0]):
            latest = (deadline, report.get("error") or "Маркет ограничил доступ к витрине")
    if latest is None:
        return None
    deadline, reason = latest
    local = deadline.astimezone(MOSCOW_TIMEZONE)
    return {
        "ok": False,
        "status": "cooldown",
        "retry_after": deadline.isoformat(),
        "error": f"{reason}. Повторный обход разрешён не ранее {local:%d.%m.%Y %H:%M} МСК. Запросы к витрине не отправлялись.",
    }


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
                seller_price=NULL, seller_checked_at=NULL, status='pending', message=NULL,
                buyer_seller_price=NULL, pay_price=NULL, pay_checked_at=NULL,
                pay_seller_price=NULL, pay_buyer_price=NULL,
                spp_percent=NULL, spp_checked_at=NULL,
                pay_discount_percent=NULL, pay_discount_checked_at=NULL
                WHERE store_slug=? AND article=?""",
                (target["store_slug"], target["article"]),
            )
        connection.commit()


def price_basis(target: dict) -> dict:
    """Capture the seller quote before opening the storefront page."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT seller_price, seller_checked_at FROM yandex_storefront_prices WHERE store_slug=? AND article=?",
            (target["store_slug"], target["article"]),
        ).fetchone()
    return dict(row) if row else {}


def record(target: dict, result: dict) -> None:
    timestamp = now()
    trusted = result.get("status") in {"ok", "price_missing"} and result.get("currency", "RUR") in {
        "RUR",
        "RUB",
    }
    buyer = positive(result.get("buyer_price")) if trusted and result["status"] == "ok" else None
    pay = positive(result.get("pay_price")) if trusted else None
    basis = target.get("_price_basis", {})
    with WRITE_LOCK, get_connection() as connection:
        previous = connection.execute(
            "SELECT * FROM yandex_storefront_prices WHERE store_slug=? AND article=?",
            (target["store_slug"], target["article"]),
        ).fetchone()
        old = dict(previous) if previous else {}
        identity = json.loads(old.get("target_json") or "{}")
        if any(
            target.get(k) is not None and target[k] != identity.get(k)
            for k in ("business_id", "market_sku", "card_id")
        ):
            return  # A remapped article must not accept an in-flight old observation.
        connection.execute(
            """INSERT INTO yandex_storefront_prices (store_slug, article, status, checked_at, message)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(store_slug, article) DO UPDATE SET status=excluded.status,
            checked_at=excluded.checked_at, message=excluded.message""",
            (target["store_slug"], target["article"], result["status"], timestamp, result.get("message")),
        )
        seller = positive(basis.get("seller_price"))
        paired = paired_at(basis.get("seller_checked_at"), timestamp) and seller == old.get("seller_price")
        if buyer is not None:
            connection.execute(
                """UPDATE yandex_storefront_prices SET buyer_price=?, price_checked_at=?, currency=?, buyer_seller_price=?
                WHERE store_slug=? AND article=?""",
                (
                    buyer,
                    timestamp,
                    result.get("currency", "RUR"),
                    seller,
                    target["store_slug"],
                    target["article"],
                ),
            )
            percent = discount_percent(seller, buyer) if paired else None
            if percent is not None:
                connection.execute(
                    "UPDATE yandex_storefront_prices SET spp_percent=?, spp_checked_at=? WHERE store_slug=? AND article=?",
                    (percent, timestamp, target["store_slug"], target["article"]),
                )
        if pay is not None and (buyer is None or pay <= buyer):
            connection.execute(
                "UPDATE yandex_storefront_prices SET pay_price=?, pay_checked_at=?, pay_seller_price=?, pay_buyer_price=? WHERE store_slug=? AND article=?",
                (pay, timestamp, seller, buyer, target["store_slug"], target["article"]),
            )
            percent = discount_percent(buyer, pay)
            if percent is not None:
                connection.execute(
                    "UPDATE yandex_storefront_prices SET pay_discount_percent=?, pay_discount_checked_at=? WHERE store_slug=? AND article=?",
                    (percent, timestamp, target["store_slug"], target["article"]),
                )
        connection.commit()


def seller_price(target: dict, value: float | None) -> None:
    value = positive(value)
    if value is None:
        return  # A missing API price must not erase the last observed seller price.
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
            (
                store_slug,
                product["article"],
                product["barcode"],
                product["name"],
                str(product.get("market_sku") or "") or None,
                product.get("updated_at"),
                product.get("image_url"),
                now(),
            ),
        )
        connection.commit()


def refresh_assortment() -> None:
    with WRITE_LOCK, get_connection() as connection:
        yandex_assortment.refresh(connection, yandex_assortment.load_active_products(), now())
        connection.commit()


def resolved_prices(row: dict) -> dict:
    """Observed prices while current; estimates keep the last real discounts indefinitely."""
    seller = positive(row.get("seller_price"))
    buyer_current = (
        row.get("status") == "ok"
        and row.get("price_checked_at") == row.get("checked_at")
        and fresh(row.get("price_checked_at"))
        and (row.get("buyer_seller_price") is None or row["buyer_seller_price"] == seller)
    )
    buyer = (
        positive(row.get("buyer_price"))
        if buyer_current
        else discounted_price(seller, row.get("spp_percent"))
    )
    pay_current = (
        row.get("status") in {"ok", "price_missing"}
        and row.get("pay_checked_at") == row.get("checked_at")
        and fresh(row.get("pay_checked_at"))
        and (row.get("pay_seller_price") is None or row["pay_seller_price"] == seller)
        and (row.get("pay_buyer_price") is None or row["pay_buyer_price"] == buyer)
    )
    pay = (
        positive(row.get("pay_price"))
        if pay_current
        else discounted_price(buyer, row.get("pay_discount_percent"))
    )
    origins = {
        "seller_price": "API: цена продавца"
        if fresh(row.get("seller_checked_at"))
        else "Последняя цена продавца из API",
        "buyer_price": "Витрина: цена с СПП" if buyer_current else "Расчётная: по последнему СПП",
        "pay_price": "Витрина: цена с Пэй" if pay_current else "Расчётная: по последней скидке Пэй",
    }
    values = {"seller_price": seller, "buyer_price": buyer, "pay_price": pay}
    for key, value in values.items():
        if value is None:
            origins[key] = "Нет цены или сохранённой скидки"
    return {
        **values,
        "origins": origins,
        "buyer_estimated": buyer is not None and not buyer_current,
        "pay_estimated": pay is not None and not pay_current,
        **{
            key: row.get(key)
            for key in (
                "spp_percent",
                "spp_checked_at",
                "pay_discount_percent",
                "pay_discount_checked_at",
                "price_checked_at",
                "pay_checked_at",
                "seller_checked_at",
                "checked_at",
                "status",
                "message",
            )
        },
    }


def get_prices(store_slug: str) -> dict[str, dict]:
    with get_connection() as connection:
        return {
            row["article"]: dict(row)
            for row in connection.execute(
                "SELECT * FROM yandex_storefront_prices WHERE store_slug=?",
                (store_slug,),
            )
        }


def fresh(timestamp: str | None, at: datetime | None = None) -> bool:
    if not timestamp:
        return False
    try:
        checked = datetime.fromisoformat(timestamp)
        current = at or datetime.now(UTC)

        deadline = max(
            checked + timedelta(seconds=PRICE_MAX_AGE_SECONDS), next_run_at(checked) + timedelta(hours=1)
        )
        return checked <= current <= deadline
    except (ValueError, TypeError):
        return False


def start_run(run_id: str) -> None:
    with WRITE_LOCK, get_connection() as connection:
        connection.execute(
            "INSERT INTO yandex_storefront_runs (run_id, started_at) VALUES (?, ?)", (run_id, now())
        )
        connection.commit()


def finish_run(run_id: str, report: dict) -> None:
    with WRITE_LOCK, get_connection() as connection:
        connection.execute(
            "UPDATE yandex_storefront_runs SET finished_at=?, report_json=? WHERE run_id=?",
            (now(), json.dumps(report, ensure_ascii=False, allow_nan=False), run_id),
        )
        connection.commit()
