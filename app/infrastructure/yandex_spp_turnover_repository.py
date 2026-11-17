import json
from datetime import date

from app.dto.spp_report import SppTurnover
from app.repositories.core import get_connection
from app.yandex.order_basis import net_amount


def load_turnover(stores: tuple[str, ...], date_from: str, date_to: str) -> tuple[SppTurnover, ...]:
    if not stores:
        return ()
    placeholders = ",".join("?" for _ in stores)
    with get_connection() as connection:
        rows = connection.execute(
            f"""SELECT loaded.store_slug, loaded.day, items.article, metric.data_json
                FROM unit_economics_yandex_loaded_days loaded
                JOIN stock_items items ON items.store_slug=loaded.store_slug
                 AND items.marketplace='YANDEX MARKET' AND items.is_service=0
                LEFT JOIN unit_economics_yandex_daily_metrics metric
                  ON metric.store_slug=loaded.store_slug AND metric.source=loaded.source
                 AND metric.day=loaded.day AND metric.article=items.article
                WHERE loaded.store_slug IN ({placeholders}) AND loaded.source='orders'
                  AND loaded.day>=? AND loaded.day<=?""",
            (*stores, date_from, date_to),
        ).fetchall()
    return tuple(
        SppTurnover(
            store_slug=row["store_slug"],
            article=row["article"],
            day=date.fromisoformat(row["day"]),
            net_orders_amount=net_amount(json.loads(row["data_json"] or "{}")),
        )
        for row in rows
    )
