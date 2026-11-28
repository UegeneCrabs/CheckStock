import argparse
import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import aiosqlite
from sqlalchemy import inspect

from app.config import settings
from app.core.constants import TIME, AdvertisingSource, Marketplace
from app.core.constants import AdvertisingDailyLoadState as LoadStatus
from app.dto.orders import OzonArticleIndex, OzonCatalogArticle
from app.dto.ozon_advertising import AdvertisingPeriod, AdvertisingSnapshot, PerformanceCredentials
from app.economics.ozon_advertising_articles import AdvertisingArticleResolver
from app.economics.ozon_advertising_breakdown import safe_error, source_snapshot
from app.infrastructure.async_database import AsyncDatabase, table_for
from app.infrastructure.async_resources import AsyncResources
from app.infrastructure.orm import OrmBase
from app.infrastructure.ozon_advertising_orm import (
    OzonAdvertisingCooldownRecord,
    OzonAdvertisingReportRecord,
    OzonAdvertisingRequestRecord,
    OzonAdvertisingResponseRecord,
)
from app.infrastructure.ozon_article_daily_repository import ArticleDailyRepository, schema
from app.infrastructure.ozon_limits_orm import (
    OzonApiControlRecord,
    OzonPerformanceBudgetRecord,
    OzonPerformanceLeaseRecord,
)
from app.ozon import tokens
from app.ozon.advertising_article_days import article_days, attach_report_articles
from app.ozon.advertising_normalizer import all_product_facts, period_days
from app.ozon.async_api import AsyncSellerApi
from app.ozon.async_performance import AsyncPerformanceClient, PerformanceApiFailure
from app.ozon.limit_identity import performance_identity


def windows(period: AdvertisingPeriod, today: date) -> Iterator[tuple[AdvertisingSource, AdvertisingPeriod]]:
    if period.end >= today:
        raise ValueError("Choose completed Moscow calendar days")
    yield AdvertisingSource.ALL_PRODUCTS, period
    yesterday = today - timedelta(days=1)
    historical_end = min(period.end, yesterday - timedelta(days=1))
    if historical_end >= period.start:
        yield AdvertisingSource.CPC, AdvertisingPeriod(start=period.start, end=historical_end)
    if period.end == yesterday:
        yield AdvertisingSource.CPC, AdvertisingPeriod(start=yesterday, end=yesterday)


async def apply(repo: ArticleDailyRepository, snapshot: AdvertisingSnapshot) -> bool:
    ready = True
    if snapshot.corrections or snapshot.over_expenses:
        print(
            "Window-level adjustments excluded from article totals:",
            snapshot.period.start,
            snapshot.period.end,
            sum(item.amount for item in snapshot.corrections) + snapshot.over_expenses,
        )
    for day in article_days(snapshot):
        status = await repo.record(snapshot.store_slug, snapshot.source, day.day, snapshot.synced_at, day)
        print(
            snapshot.store_slug,
            day.day,
            snapshot.source.value,
            status.value,
            day.source_rows,
            len(day.articles),
            day.unallocated_kopecks,
        )
        ready &= status == LoadStatus.READY
    return ready


async def catalog_index(store: str, catalog: Path | None) -> OzonArticleIndex:
    if catalog is None:
        return OzonArticleIndex(items=())
    async with aiosqlite.connect(catalog.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        async with conn.execute(
            "SELECT mp_sku, article FROM stock_items WHERE store_slug=? AND marketplace=? AND mp_sku IS NOT NULL",
            (store, Marketplace.OZON.value),
        ) as rows:
            return OzonArticleIndex(
                items=tuple(
                    OzonCatalogArticle(sku=str(sku), article=article)
                    for sku, article in await rows.fetchall()
                )
            )


async def fetch(
    repo: ArticleDailyRepository, store: str, period: AdvertisingPeriod, catalog: Path | None
) -> bool:
    client_id, secret = tokens.get_performance_credentials(store)
    ready = True
    with TemporaryDirectory(prefix="checkstock-ozon-http-") as temporary:
        transport_db = AsyncDatabase(url="", path=Path(temporary) / "transport.sqlite")
        async with AsyncResources(database=transport_db) as resources:
            records = (
                OzonAdvertisingReportRecord,
                OzonAdvertisingResponseRecord,
                OzonAdvertisingRequestRecord,
                OzonAdvertisingCooldownRecord,
                OzonApiControlRecord,
                OzonPerformanceBudgetRecord,
                OzonPerformanceLeaseRecord,
            )
            async with transport_db.transaction(write=True) as conn:
                await conn.run_sync(
                    lambda sync: OrmBase.metadata.create_all(
                        sync, tables=[table_for(model) for model in records]
                    )
                )
            client = AsyncPerformanceClient(
                resources.http,
                resources.advertising,
                store,
                PerformanceCredentials(client_id=client_id, client_secret=secret),
                limits=resources.ozon_limits,
                identity=performance_identity(store, client_id),
            )
            resolver = AdvertisingArticleResolver(
                store,
                await catalog_index(store, catalog),
                AsyncSellerApi(resources.http, tokens.get_order_credentials(store)),
            )
            for source, window in windows(period, datetime.now(TIME.moscow_timezone).date()):
                started = datetime.now(UTC)
                try:
                    if source == AdvertisingSource.ALL_PRODUCTS:
                        report = await client.all_product_orders(window)
                        snapshot = AdvertisingSnapshot(
                            store_slug=store,
                            source=source,
                            period=window,
                            synced_at=started,
                            facts=all_product_facts(report),
                        )
                        snapshot = attach_report_articles(await resolver.attach(snapshot), report)
                    else:
                        snapshot = await resolver.attach(await source_snapshot(client, store, source, window))
                    ready &= await apply(repo, snapshot.model_copy(update={"synced_at": started}))
                except (ValueError, PerformanceApiFailure) as exc:
                    for day in period_days(window):
                        await repo.record(store, source, day, started, error=safe_error(exc))
                    print(store, source.value, window.start, window.end, safe_error(exc))
                    ready = False
    return ready


async def main() -> bool:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    live = sub.add_parser("fetch")
    live.add_argument("--store", required=True)
    live.add_argument("--catalog", type=Path)
    live.add_argument("--start", type=date.fromisoformat, required=True)
    live.add_argument("--end", type=date.fromisoformat, required=True)
    sub.add_parser("replay").add_argument("snapshots", type=Path, nargs="+")
    args = parser.parse_args()
    path = args.database.resolve()
    if path == settings.database_path.resolve():
        raise ValueError("Choose a separate experimental SQLite database")
    path.parent.mkdir(parents=True, exist_ok=True)
    database = AsyncDatabase(url="", path=path)
    try:
        async with database.transaction() as conn:
            existing = await conn.run_sync(lambda sync: inspect(sync).get_table_names())
            if set(existing).difference(schema.tables):
                raise ValueError("Output database contains other tables")
        repo = ArticleDailyRepository(database)
        await repo.initialize()
        if args.command == "fetch":
            return await fetch(
                repo, args.store, AdvertisingPeriod(start=args.start, end=args.end), args.catalog
            )
        ready = True
        for snapshot in args.snapshots:
            ready &= await apply(repo, AdvertisingSnapshot.model_validate_json(snapshot.read_bytes()))
        return ready
    finally:
        await database.close()


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
