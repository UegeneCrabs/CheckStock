import json

from app.dto.ephemerides_stock import EphemeridesStockSnapshot
from app.repositories.core import WRITE_LOCK, get_connection


def freeze_stock(snapshot: EphemeridesStockSnapshot) -> None:
    payload = json.dumps([item.model_dump(mode="json") for item in snapshot.products], ensure_ascii=False)
    with WRITE_LOCK, get_connection() as connection:
        connection.execute(
            "INSERT INTO ephemerides_stock_snapshots (store_slug, week, products_json, captured_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(store_slug, week) DO NOTHING",
            (snapshot.store, snapshot.week.isoformat(), payload, snapshot.captured_at.isoformat()),
        )
        connection.commit()
