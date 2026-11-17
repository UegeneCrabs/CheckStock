import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, time
from pathlib import Path

from app.core.constants import YANDEX_PRICE_HISTORY_LOCK
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.yandex_price_history import YandexPriceObservation
from app.economics.spp_report import _weighted_average
from app.infrastructure.database import DatabaseConnection, database_for_path
from app.yandex.order_basis import net_amount


def lock_history(connection: DatabaseConnection) -> None:
    if connection.dialect_name == "postgresql":
        connection.execute("SELECT pg_advisory_xact_lock(?)", (YANDEX_PRICE_HISTORY_LOCK,))
    else:
        connection.execute("BEGIN IMMEDIATE")


class YandexPriceHistoryWriter:
    def __init__(self, connection: DatabaseConnection) -> None:
        self.connection = connection

    def save(self, observation: YandexPriceObservation) -> None:
        connection = self.connection
        day = observation.observed_at.astimezone(MOSCOW_TIMEZONE).date()
        if connection.execute(
            "SELECT 1 FROM yandex_daily_prices WHERE store_slug=? AND article=? AND observed_day=?",
            (observation.store_slug, observation.article, day.isoformat()),
        ).fetchone():
            raise RuntimeError("Yandex price observation arrived after daily close")
        turnover = connection.execute(
            """SELECT loaded.updated_at, metric.data_json
               FROM unit_economics_yandex_loaded_days loaded
               LEFT JOIN unit_economics_yandex_daily_metrics metric
                 ON metric.store_slug=loaded.store_slug AND metric.source=loaded.source
                AND metric.day=loaded.day AND metric.article=?
               WHERE loaded.store_slug=? AND loaded.source='orders' AND loaded.day=?""",
            (observation.article, observation.store_slug, day.isoformat()),
        ).fetchone()
        values = asdict(observation)
        observed = observation.observed_at.astimezone(UTC)
        values["observed_at"] = observed.isoformat()
        values["observed_hour"] = observed.replace(minute=0, second=0, microsecond=0).isoformat()
        values["orders_turnover_rub"] = (
            net_amount(json.loads(turnover["data_json"] or "{}")) if turnover is not None else None
        )
        values["orders_turnover_updated_at"] = turnover["updated_at"] if turnover is not None else None
        columns = tuple(values)
        updates = ",".join(
            f"{column}=excluded.{column}"
            for column in columns
            if column not in {"store_slug", "article", "observed_hour"}
        )
        connection.execute(
            f"INSERT INTO yandex_hourly_prices ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
            f"ON CONFLICT(store_slug,article,observed_hour) DO UPDATE SET {updates}",
            tuple(values.values()),
        )


class YandexDailyPriceRepository:
    def __init__(self, database_path: Callable[[], Path]) -> None:
        self.database_path = database_path

    def close(self, today: date) -> int:
        cutoff = datetime.combine(today, time.min, MOSCOW_TIMEZONE).astimezone(UTC).isoformat()
        with database_for_path(self.database_path()).connect() as connection:
            lock_history(connection)
            rows = connection.execute(
                "SELECT * FROM yandex_hourly_prices WHERE observed_at<?", (cutoff,)
            ).fetchall()
            groups = defaultdict(list)
            for row in rows:
                day = datetime.fromisoformat(row["observed_at"]).astimezone(MOSCOW_TIMEZONE).date()
                groups[(row["store_slug"], row["article"], day)].append(row)
            for (_store, _article, day), observations in groups.items():
                latest = max(observations, key=lambda row: row["observed_at"])
                values = dict(latest)
                del values["observed_hour"]
                del values["orders_turnover_rub"]
                values["observed_day"] = day.isoformat()
                for field in ("seller_price", "customer_price_with_spp", "spp_percent"):
                    values[field] = _weighted_average(
                        [(row[field], row["orders_turnover_rub"]) for row in observations]
                    )
                connection.execute(
                    f"INSERT INTO yandex_daily_prices ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                    tuple(values.values()),
                )
            connection.execute("DELETE FROM yandex_hourly_prices WHERE observed_at<?", (cutoff,))
            connection.commit()
        return len(groups)
