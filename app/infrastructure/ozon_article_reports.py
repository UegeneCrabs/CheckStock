from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import aiosqlite

from app.core.constants import (
    OZON_ADVERTISING,
    AdvertisingAllocation,
    AdvertisingDailyLoadState,
    AdvertisingError,
    AdvertisingKind,
    AdvertisingSource,
    AdvertisingState,
)
from app.dto.ozon_advertising import (
    AdvertisingCorrection,
    AdvertisingFact,
    AdvertisingPeriod,
    AdvertisingSnapshot,
    AdvertisingSourceStatus,
    PerformanceCampaign,
)
from app.dto.ozon_advertising import (
    AdvertisingReportVersion as ReportVersion,
)
from app.ozon.advertising_article_days import ARTICLE_SOURCES


@dataclass(frozen=True, slots=True)
class ArticleReports:
    statuses: tuple[AdvertisingSourceStatus, ...] = ()
    covered: tuple[ReportVersion, ...] = ()


def utc_stamp(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


async def read_article_reports(
    path: Path, stores: tuple[str, ...], period: AdvertisingPeriod, article: str
) -> ArticleReports:
    if not stores or not path.is_file():
        return ArticleReports()
    markers = ",".join("?" for _ in stores)
    scope = f"store_slug IN ({markers}) AND day BETWEEN ? AND ?"
    params = (*stores, period.start.isoformat(), period.end.isoformat())
    async with aiosqlite.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        await conn.execute("BEGIN")
        if not await conn.execute_fetchall(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ozon_advertising_article_day_exceptions'"
        ):
            return ArticleReports()
        metadata = await conn.execute_fetchall(
            f"""
            SELECT store_slug,day,source,status,last_attempt_at,last_applied_at,error_code,
                   period_from,period_to,traffic_collected,campaign_id,finalized
            FROM ozon_advertising_daily_loads LEFT JOIN ozon_advertising_article_day_context USING(store_slug,day,source)
            WHERE {scope} AND (period_from IS NOT NULL OR last_applied_at IS NULL)""",
            params,
        )
        if not metadata:
            return ArticleReports()
        stores_with_data = tuple(sorted({row[0] for row in metadata}))
        parts = []
        fact_params: list[str] = []
        for source, table in (
            (AdvertisingSource.CPC, "ozon_cpc_article_daily"),
            (AdvertisingSource.ALL_PRODUCTS, "ozon_cpo_all_article_daily"),
        ):
            counters = "impressions, clicks" if source == AdvertisingSource.CPC else "NULL, NULL"
            where_stores = ",".join("?" for _ in stores_with_data)
            parts.append(
                f"SELECT store_slug,day,?,article,spend_kopecks,{counters} FROM {table} WHERE store_slug IN ({where_stores}) AND day BETWEEN ? AND ?"
                + (" AND article=?" if article else "")
            )
            fact_params.extend(
                (source.value, *stores_with_data, period.start.isoformat(), period.end.isoformat())
            )
            if article:
                fact_params.append(article)
        amounts = await conn.execute_fetchall(" UNION ALL ".join(parts), fact_params)
        extra = await conn.execute_fetchall(
            f"""SELECT store_slug,day,source,article,raw_type,allocation,amount_kopecks,impressions,clicks
            FROM ozon_advertising_article_day_exceptions WHERE {scope}"""
            + (" AND (article=? OR article IS NULL)" if article else ""),
            (*params, article) if article else params,
        )
        corrections = await conn.execute_fetchall(
            f"""
            SELECT store_slug,day,source,campaign_id,kind,amount_kopecks FROM ozon_advertising_article_day_adjustments
            WHERE {scope} ORDER BY store_slug,day,source,position""",
            params,
        )
    facts: dict[tuple[str, str, str], list[AdvertisingFact]] = defaultdict(list)
    adjustments: dict[tuple[str, str, str], list[AdvertisingCorrection]] = defaultdict(list)
    money = Decimal(OZON_ADVERTISING.money_quantum)
    replaced = set()
    for store, day, raw_source, item, raw, allocation, cents, views, clicks in extra:
        source = AdvertisingSource(raw_source)
        replaced.add((store, day, source, item))
        facts[store, day, source].append(
            AdvertisingFact(
                day=date.fromisoformat(day),
                kind=ARTICLE_SOURCES[source],
                article=item,
                amount=cents * money,
                impressions=views,
                clicks=clicks,
                allocation=AdvertisingAllocation(allocation),
                raw_type=raw,
            )
        )
    for store, day, raw_source, item, cents, views, clicks in amounts:
        if (store, day, raw_source, item) in replaced:
            continue
        source = AdvertisingSource(raw_source)
        facts[store, day, source].append(
            AdvertisingFact(
                day=date.fromisoformat(day),
                kind=ARTICLE_SOURCES[source],
                article=item,
                amount=cents * money,
                impressions=views,
                clicks=clicks,
                allocation=AdvertisingAllocation.DIRECT,
                raw_type=OZON_ADVERTISING.sku_type
                if source == AdvertisingSource.CPC
                else OZON_ADVERTISING.all_type,
            )
        )
    for store, day, source, campaign, kind, cents in corrections:
        adjustments[store, day, source].append(
            AdvertisingCorrection(campaign_id=campaign, kind=AdvertisingKind(kind), amount=cents * money)
        )
    reports = []
    coverage: dict[ReportVersion, int] = defaultdict(int)
    for (
        store,
        day,
        raw_source,
        status,
        attempt,
        applied,
        error,
        start,
        end,
        traffic,
        campaign,
        finalized,
    ) in metadata:
        source = AdvertisingSource(raw_source)
        day_period = AdvertisingPeriod(start=date.fromisoformat(day), end=date.fromisoformat(day))
        if applied is None:
            reports.append(
                AdvertisingSourceStatus(
                    store_slug=store,
                    source=source,
                    period=day_period,
                    state=AdvertisingState.MISSING
                    if error
                    in (AdvertisingError.CAMPAIGN_NOT_FOUND, AdvertisingError.HISTORICAL_CAMPAIGN_ABSENT)
                    else AdvertisingState.ERROR,
                    attempted_at=utc_stamp(attempt),
                    error=error,
                )
            )
            continue
        version = ReportVersion(
            store,
            source,
            AdvertisingPeriod(start=date.fromisoformat(start), end=date.fromisoformat(end)),
            utc_stamp(applied),
        )
        coverage[version] += 1
        snapshot = AdvertisingSnapshot(
            store_slug=store,
            source=source,
            period=version.period,
            facts=tuple(facts[store, day, source]),
            corrections=tuple(adjustments[store, day, source]),
            campaigns=(
                PerformanceCampaign(
                    id=campaign,
                    advObjectType=OZON_ADVERTISING.sku_type,
                    payment_type=OZON_ADVERTISING.cpc_payment,
                ),
            )
            if campaign
            else (),
            traffic_collected=bool(traffic),
            finalized=bool(finalized),
            synced_at=version.synced_at,
        )
        reports.append(
            AdvertisingSourceStatus(
                store_slug=store,
                source=source,
                period=day_period,
                state=AdvertisingState.READY
                if status == AdvertisingDailyLoadState.READY
                else AdvertisingState.ERROR,
                attempted_at=utc_stamp(attempt),
                error=error,
                snapshot=snapshot,
            )
        )
    covered = tuple(
        key
        for key, n in coverage.items()
        if n == (min(key.period.end, period.end) - max(key.period.start, period.start)).days + 1
    )
    return ArticleReports(tuple(reports), covered)
