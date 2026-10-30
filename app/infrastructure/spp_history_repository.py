"""Portable read-only SQLAlchemy queries for SQLite and PostgreSQL."""

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.domain import MOSCOW_TIMEZONE
from app.dto.spp_history import DailySppRow, HourlySppRow, SppHistoryKind, SppHistoryQuery
from app.infrastructure.orm import WbDailyPriceRecord, WbHourlyPriceRecord


def history_statement(query: SppHistoryQuery) -> Select:
    hourly = query.kind == SppHistoryKind.HOURLY
    model = WbHourlyPriceRecord if hourly else WbDailyPriceRecord
    if hourly:
        column = model.observed_at
        start = datetime.combine(query.date_from, time.min, MOSCOW_TIMEZONE).astimezone(UTC).isoformat()
        end = (
            datetime.combine(query.date_to + timedelta(days=1), time.min, MOSCOW_TIMEZONE)
            .astimezone(UTC)
            .isoformat()
        )
        columns = (
            model.article,
            model.observed_at,
            model.customer_price_with_spp,
            model.spp_percent,
            model.customer_price_with_wallet,
        )
    else:
        column = model.observed_day
        start, end = query.date_from.isoformat(), (query.date_to + timedelta(days=1)).isoformat()
        columns = (
            model.article,
            model.observed_day,
            model.observed_at,
            model.customer_price_with_spp,
            model.spp_percent,
            model.customer_price_with_wallet,
        )
    statement = select(*columns).where(model.store_slug == query.store, column >= start, column < end)
    if query.article is not None:
        statement = statement.where(model.article == query.article)
    return statement.order_by(column, model.article)


class SqlAlchemySppHistoryRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def read(self, query: SppHistoryQuery) -> list[HourlySppRow] | list[DailySppRow]:
        row_type = HourlySppRow if query.kind == SppHistoryKind.HOURLY else DailySppRow
        with self.sessions() as session:
            return [row_type(*row) for row in session.execute(history_statement(query))]
