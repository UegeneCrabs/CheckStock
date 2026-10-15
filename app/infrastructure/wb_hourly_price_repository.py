"""Database adapters for hourly WB storefront prices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app import db
from app.dto.wb_hourly_prices import WbCatalogItem, WbHourlyPriceObservation, WbHourlyTurnover
from app.infrastructure.database import database_for_path
from app.infrastructure.orm import WbFunnelDailyOrderRecord, WbHourlyPriceRecord


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
