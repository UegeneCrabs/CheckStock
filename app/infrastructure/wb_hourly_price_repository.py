"""Database adapters for hourly WB storefront prices."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app import db
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.wb_hourly_prices import WbCatalogItem, WbHourlyPriceObservation, WbHourlyTurnover
from app.infrastructure.database import database_for_path
from app.infrastructure.orm import WbDailyPriceRecord, WbFunnelDailyOrderRecord, WbHourlyPriceRecord


def _lock_price_history(session, dialect_name: str) -> None:
    """Serialize hourly writes and daily closing across worker processes."""
    if dialect_name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(2026101501)"))
    else:
        session.execute(text("BEGIN IMMEDIATE"))


def _average(rows: list[WbHourlyPriceRecord], field: str, *, integer: bool = False) -> float | int | None:
    values = [
        (Decimal(str(value)), Decimal(str(row.orders_turnover_rub or 0)))
        for row in rows
        if (value := getattr(row, field)) is not None
    ]
    if not values:
        return None
    weighted = [(value, weight) for value, weight in values if weight > 0]
    result = (
        sum((value * weight for value, weight in weighted), Decimal(0))
        / sum((weight for _, weight in weighted), Decimal(0))
        if weighted
        else sum((value for value, _ in values), Decimal(0)) / len(values)
    )
    return int(result.quantize(Decimal(1), rounding=ROUND_HALF_UP)) if integer else float(result)


def _daily_record(rows: list[WbHourlyPriceRecord], day: date) -> WbDailyPriceRecord:
    latest = max(rows, key=lambda row: row.observed_at)
    return WbDailyPriceRecord(
        store_slug=latest.store_slug,
        article=latest.article,
        observed_day=day.isoformat(),
        observed_at=latest.observed_at,
        nm_id=latest.nm_id,
        size_id=latest.size_id,
        tech_size_name=latest.tech_size_name,
        customer_price_with_spp=_average(rows, "customer_price_with_spp"),
        spp_percent=_average(rows, "spp_percent"),
        orders_turnover_updated_at=latest.orders_turnover_updated_at,
        customer_price_with_wallet=_average(rows, "customer_price_with_wallet"),
        wallet_discount_percent=_average(rows, "wallet_discount_percent"),
        product_kopecks=_average(rows, "product_kopecks", integer=True),
        logistics_kopecks=_average(rows, "logistics_kopecks", integer=True),
        price_fields_json=latest.price_fields_json,
        sale_conditions=latest.sale_conditions,
    )


class SqlAlchemyDailyPriceCloser:
    def __init__(self, database_path: Callable[[], Path]) -> None:
        self.database_path = database_path

    def close(self, today: date | None = None) -> int:
        today = today or datetime.now(MOSCOW_TIMEZONE).date()
        cutoff = datetime.combine(today, time.min, MOSCOW_TIMEZONE).astimezone(UTC).isoformat()
        database = database_for_path(self.database_path())
        with database.session_factory() as session:
            _lock_price_history(session, database.dialect_name)
            hourly = session.scalars(
                select(WbHourlyPriceRecord).where(WbHourlyPriceRecord.observed_at < cutoff)
            ).all()
            groups: dict[tuple[str, str, date], list[WbHourlyPriceRecord]] = defaultdict(list)
            for row in hourly:
                day = datetime.fromisoformat(row.observed_at).astimezone(MOSCOW_TIMEZONE).date()
                groups[(row.store_slug, row.article, day)].append(row)
            for (store, article, day), rows in groups.items():
                if session.get(WbDailyPriceRecord, (store, article, day.isoformat())) is not None:
                    raise RuntimeError(f"Late hourly price after daily close: {store}/{article}/{day}")
                session.add(_daily_record(rows, day))
            session.flush()
            if hourly:
                session.execute(delete(WbHourlyPriceRecord).where(WbHourlyPriceRecord.observed_at < cutoff))
            session.commit()
        return len(groups)


class SqlAlchemyHourlyPriceWriter:
    def __init__(self, database_path: Callable[[], Path]) -> None:
        self.database_path = database_path

    def upsert(self, rows: list[WbHourlyPriceObservation]) -> int:
        if not rows:
            return 0
        database = database_for_path(self.database_path())
        table = WbHourlyPriceRecord.__table__
        insert = postgres_insert if database.dialect_name == "postgresql" else sqlite_insert
        with database.session_factory() as session:
            _lock_price_history(session, database.dialect_name)
            today = datetime.now(MOSCOW_TIMEZONE).date()
            for row in rows:
                day = datetime.fromisoformat(row.observed_at).astimezone(MOSCOW_TIMEZONE).date()
                if (
                    day < today
                    and session.get(WbDailyPriceRecord, (row.store_slug, row.article, day.isoformat()))
                    is not None
                ):
                    raise RuntimeError(
                        f"Hourly observation arrived after daily close: {row.store_slug}/{row.article}/{day}"
                    )
            for start in range(0, len(rows), 500):
                statement = insert(table).values([asdict(row) for row in rows[start : start + 500]])
                statement = statement.on_conflict_do_update(
                    index_elements=[table.c.store_slug, table.c.article, table.c.observed_hour],
                    set_={
                        column.name: statement.excluded[column.name]
                        for column in table.c
                        if column.name not in {"store_slug", "article", "observed_hour"}
                    },
                )
                session.execute(statement)
            session.commit()
        return len(rows)

    def daily_turnover(self, store_slug: str, day: date) -> tuple[WbHourlyTurnover, ...]:
        database = database_for_path(self.database_path())
        table = WbFunnelDailyOrderRecord
        statement = select(
            table.article,
            table.orders_amount,
            table.cancel_amount,
            table.updated_at,
        ).where(
            table.store_slug == store_slug,
            table.day == day.isoformat(),
            table.source_version >= 3,
        )
        with database.session_factory() as session:
            rows = session.execute(statement).all()
        return tuple(
            WbHourlyTurnover(
                article=article,
                net_orders_amount=round(orders_amount - cancel_amount, 2),
                updated_at=updated_at,
            )
            for article, orders_amount, cancel_amount, updated_at in rows
        )


def catalog_items(store: str) -> tuple[WbCatalogItem, ...]:
    """Read the existing WB catalog without moving its compatibility API."""
    return tuple(
        WbCatalogItem(article=str(item.get("article") or "")) for item in db.get_catalog_items(store, "WB")
    )
