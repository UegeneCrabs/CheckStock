from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    cast,
    insert,
    literal,
    null,
    select,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.constants import OZON_ADVERTISING, AdvertisingError, AdvertisingSource, DatabaseDialect
from app.core.constants import AdvertisingDailyLoadState as LoadStatus
from app.dto.ozon_advertising import AdvertisingPeriod
from app.infrastructure.async_database import AsyncDatabase
from app.ozon.advertising_article_days import ArticleDay, kopecks

schema = MetaData()


def article_columns() -> tuple[Column[str], Column[date], Column[str], Column[int]]:
    return (
        Column("store_slug", String, primary_key=True),
        Column("day", Date, primary_key=True),
        Column("article", String, primary_key=True),
        Column("spend_kopecks", BigInteger, nullable=False),
    )


cpo = Table("ozon_cpo_all_article_daily", schema, *article_columns())
cpc = Table(
    "ozon_cpc_article_daily",
    schema,
    *article_columns(),
    Column("impressions", BigInteger, nullable=False),
    Column("clicks", BigInteger, nullable=False),
    Column("traffic_complete", Boolean, nullable=False),
)
loads = Table(
    "ozon_advertising_daily_loads",
    schema,
    Column("store_slug", String, primary_key=True),
    Column("day", Date, primary_key=True),
    Column("source", String, primary_key=True),
    Column("status", String, nullable=False),
    Column("last_attempt_at", DateTime, nullable=False),
    Column("last_applied_at", DateTime),
    Column("source_rows", Integer),
    Column("unallocated_kopecks", BigInteger),
    Column("error_code", String),
)


history = Table(
    "ozon_advertising_article_history",
    schema,
    Column("store_slug", String, primary_key=True),
    Column("next_day", Date, nullable=False),
)


def context_key() -> tuple[Column[str], Column[date], Column[str]]:
    return (
        Column("store_slug", String, primary_key=True),
        Column("day", Date, primary_key=True),
        Column("source", String, primary_key=True),
    )


contexts = Table(
    "ozon_advertising_article_day_context",
    schema,
    *context_key(),
    Column("period_from", Date, nullable=False),
    Column("period_to", Date, nullable=False),
    Column("traffic_collected", Boolean, nullable=False),
    Column("campaign_id", String),
    Column("finalized", Boolean, nullable=False),
)
adjustments = Table(
    "ozon_advertising_article_day_adjustments",
    schema,
    *context_key(),
    Column("position", Integer, primary_key=True),
    Column("campaign_id", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("amount_kopecks", BigInteger, nullable=False),
)


exceptions = Table(
    "ozon_advertising_article_day_exceptions",
    schema,
    *context_key(),
    Column("position", Integer, primary_key=True),
    Column("article", String),
    Column("raw_type", String, nullable=False),
    Column("allocation", String, nullable=False),
    Column("amount_kopecks", BigInteger, nullable=False),
    Column("impressions", BigInteger),
    Column("clicks", BigInteger),
)


Index("ix_ozon_cpo_daily_store_article_day", cpo.c.store_slug, cpo.c.article, cpo.c.day)
Index("ix_ozon_cpc_daily_store_article_day", cpc.c.store_slug, cpc.c.article, cpc.c.day)
tables = {AdvertisingSource.ALL_PRODUCTS: cpo, AdvertisingSource.CPC: cpc}


@dataclass(frozen=True, slots=True, kw_only=True)
class ArticleRow:
    source: AdvertisingSource
    day: date
    article: str
    spend_kopecks: int
    impressions: int | None
    clicks: int | None
    traffic_complete: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class DayCoverage:
    day: date
    source: AdvertisingSource
    status: LoadStatus
    last_applied_at: datetime | None
    unallocated_kopecks: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class DailyRead:
    rows: tuple[ArticleRow, ...]
    coverage: tuple[DayCoverage, ...]


class ArticleDailyRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def initialize(self) -> None:
        async with self.database.transaction(write=True) as conn:
            await conn.run_sync(schema.create_all)

    async def pending_today(
        self, store: str, day: date, slot_start: datetime
    ) -> tuple[AdvertisingSource, ...]:
        since = slot_start.astimezone(UTC).replace(tzinfo=None)
        async with self.database.transaction() as conn:
            ready = set(
                await conn.scalars(
                    select(loads.c.source).where(
                        loads.c.store_slug == store,
                        loads.c.day == day,
                        loads.c.status == LoadStatus.READY,
                        loads.c.last_applied_at >= since,
                    )
                )
            )
        return tuple(source for source in tables if source not in ready)

    async def pending_history(
        self, store: str, today: date, close_through: date
    ) -> dict[AdvertisingSource, set[date]]:
        pending: dict[AdvertisingSource, set[date]] = {source: set() for source in tables}
        async with self.database.transaction(write=True) as conn:
            make_insert = (
                postgres_insert if conn.dialect.name == DatabaseDialect.POSTGRESQL else sqlite_insert
            )
            seed = today - timedelta(days=OZON_ADVERTISING.period_days)
            await conn.execute(
                make_insert(history).values(store_slug=store, next_day=seed).on_conflict_do_nothing()
            )
            start = (
                await conn.execute(select(history.c.next_day).filter_by(store_slug=store).with_for_update())
            ).scalar_one()
            if start > close_through:
                return pending
            days = {start + timedelta(days=i) for i in range((close_through - start).days + 1)}
            for source in tables:
                pending[source] = days.copy()
            key = and_(*(loads.c[col] == contexts.c[col] for col in ("store_slug", "day", "source")))
            rows = await conn.execute(
                select(
                    loads.c.source,
                    loads.c.day,
                    loads.c.status,
                    contexts.c.finalized,
                    loads.c.error_code,
                    loads.c.last_applied_at,
                )
                .select_from(loads.outerjoin(contexts, key))
                .where(loads.c.store_slug == store, loads.c.day.between(start, close_through))
            )
            for raw_source, day, status, finalized, error, applied in rows:
                source = AdvertisingSource(raw_source)
                if (status == LoadStatus.READY and finalized) or (
                    error == AdvertisingError.HISTORICAL_CAMPAIGN_ABSENT and applied is None
                ):
                    pending[source].discard(day)
            next_day = min(
                (day for missing in pending.values() for day in missing),
                default=close_through + timedelta(days=1),
            )
            if next_day != start:
                await conn.execute(update(history).filter_by(store_slug=store).values(next_day=next_day))
        return pending

    async def record(
        self,
        store: str,
        source: AdvertisingSource,
        day: date,
        version: datetime,
        data: ArticleDay | None = None,
        *,
        error: str | None = None,
    ) -> LoadStatus:
        if not store or version.utcoffset() is None or (data is None) != bool(error):
            raise ValueError("Invalid daily load")
        if data is not None and data.day != day:
            raise ValueError("Daily load date mismatch")
        timestamp = version.astimezone(UTC).replace(tzinfo=None)
        table = tables[source]
        key = {"store_slug": store, "day": day, "source": source.value}
        scope = and_(table.c.store_slug == store, table.c.day == day)
        async with self.database.transaction(write=True) as conn:
            make_insert = (
                postgres_insert if conn.dialect.name == DatabaseDialect.POSTGRESQL else sqlite_insert
            )
            initial = make_insert(loads).values(**key, status=LoadStatus.ERROR, last_attempt_at=timestamp)
            await conn.execute(initial.on_conflict_do_nothing(index_elements=list(key)))
            state = (await conn.execute(select(loads).filter_by(**key).with_for_update())).one()
            if state.last_attempt_at > timestamp:
                return LoadStatus(state.status)
            if state.last_applied_at == timestamp and (
                data is None
                or data.context is None
                or (await conn.execute(select(contexts.c.day).filter_by(**key))).first()
            ):
                return LoadStatus(state.status)
            status = LoadStatus.ERROR
            values: dict[str, object] = {"last_attempt_at": timestamp, "error_code": error}
            if data is not None:
                previous = set((await conn.scalars(select(table.c.article).where(scope))).all())
                if previous.difference(data.articles):
                    status = LoadStatus.REVIEW
                    values["error_code"] = AdvertisingError.PREVIOUS_ARTICLE_MISSING
                else:
                    status = LoadStatus.READY
                    rows = []
                    for article, amount in sorted(data.articles.items()):
                        row: dict[str, object] = {
                            "store_slug": store,
                            "day": day,
                            "article": article,
                            "spend_kopecks": amount.spend_kopecks,
                        }
                        if source == AdvertisingSource.CPC:
                            row.update(
                                impressions=amount.impressions,
                                clicks=amount.clicks,
                                traffic_complete=amount.traffic_complete,
                            )
                        rows.append(row)
                    await conn.execute(table.delete().where(scope))
                    if rows:
                        await conn.execute(insert(table), rows)
                    await conn.execute(contexts.delete().filter_by(**key))
                    await conn.execute(adjustments.delete().filter_by(**key))
                    await conn.execute(exceptions.delete().filter_by(**key))
                    if data.exceptions and data.context is not None:
                        await conn.execute(
                            insert(exceptions),
                            [
                                dict(
                                    **key,
                                    position=i,
                                    article=f.article,
                                    raw_type=f.raw_type,
                                    allocation=f.allocation.value,
                                    amount_kopecks=kopecks(f.amount),
                                    impressions=f.impressions,
                                    clicks=f.clicks,
                                )
                                for i, f in enumerate(data.exceptions)
                            ],
                        )
                    if data.context is not None:
                        meta = data.context
                        await conn.execute(
                            insert(contexts).values(
                                **key,
                                period_from=meta.period.start,
                                period_to=meta.period.end,
                                traffic_collected=meta.traffic_collected,
                                campaign_id=meta.campaign_id,
                                finalized=meta.finalized,
                            )
                        )
                        if meta.corrections:
                            await conn.execute(
                                insert(adjustments),
                                [
                                    dict(
                                        **key,
                                        position=i,
                                        campaign_id=c.campaign_id,
                                        kind=c.kind.value,
                                        amount_kopecks=kopecks(c.amount),
                                    )
                                    for i, c in enumerate(meta.corrections)
                                ],
                            )
                    values.update(
                        last_applied_at=timestamp,
                        source_rows=data.source_rows,
                        unallocated_kopecks=data.unallocated_kopecks,
                    )
            await conn.execute(update(loads).filter_by(**key).values(**values, status=status))
            return status

    async def read(self, store: str, period: AdvertisingPeriod, article: str | None = None) -> DailyRead:
        parts = []
        for source, table in tables.items():
            has_traffic = source == AdvertisingSource.CPC
            stmt = select(
                table.c.day,
                literal(source.value),
                table.c.article,
                table.c.spend_kopecks,
                table.c.impressions if has_traffic else cast(null(), BigInteger),
                table.c.clicks if has_traffic else cast(null(), BigInteger),
                table.c.traffic_complete if has_traffic else cast(null(), Boolean),
                cast(null(), String),
                cast(null(), DateTime),
                cast(null(), BigInteger),
            ).where(table.c.store_slug == store, table.c.day.between(period.start, period.end))
            if article is not None:
                stmt = stmt.where(table.c.article == article)
            parts.append(stmt)
        coverage_query = select(
            loads.c.day,
            loads.c.source,
            null(),
            null(),
            null(),
            null(),
            null(),
            loads.c.status,
            loads.c.last_applied_at,
            loads.c.unallocated_kopecks,
        ).where(loads.c.store_slug == store, loads.c.day.between(period.start, period.end))
        async with self.database.transaction() as conn:
            records = await conn.execute(union_all(*parts, coverage_query))
            rows = []
            coverage = []
            for day, source, item, spend, views, clicks, complete, status, applied, unallocated in records:
                if item is None:
                    coverage.append(
                        DayCoverage(
                            day=day,
                            source=AdvertisingSource(source),
                            status=LoadStatus(status),
                            last_applied_at=applied,
                            unallocated_kopecks=unallocated,
                        )
                    )
                else:
                    rows.append(
                        ArticleRow(
                            day=day,
                            source=AdvertisingSource(source),
                            article=item,
                            spend_kopecks=spend,
                            impressions=views,
                            clicks=clicks,
                            traffic_complete=complete,
                        )
                    )
            return DailyRead(rows=tuple(rows), coverage=tuple(coverage))


@asynccontextmanager
async def open_article_repository(path: Path) -> AsyncIterator[ArticleDailyRepository]:
    path.parent.mkdir(parents=True, exist_ok=True)
    database = AsyncDatabase(url="", path=path)
    try:
        repository = ArticleDailyRepository(database)
        await repository.initialize()
        yield repository
    finally:
        await database.close()
