import asyncio
from datetime import UTC, datetime, time
from decimal import Decimal

from pydantic import ValidationError

from app.core.constants import (
    OZON_ADVERTISING,
    TIME,
    AdvertisingError,
    AdvertisingKind,
    AdvertisingSource,
    AdvertisingState,
    HTTPMethod,
    HTTPStatus,
)
from app.core.logging_config import tagged_logger
from app.core.stores import STORES
from app.dto.ozon_advertising import (
    AdvertisingCorrection,
    AdvertisingFact,
    AdvertisingImport,
    AdvertisingPeriod,
    AdvertisingSnapshot,
    AdvertisingSourceStatus,
    AdvertisingSyncResult,
    PerformanceCredentials,
    PerformanceReportRequest,
)
from app.economics.ozon_advertising_articles import AdvertisingArticleResolver
from app.infrastructure.async_resources import AsyncResources
from app.jobs import async_locks
from app.ozon import tokens
from app.ozon.advertising_normalizer import (
    all_product_facts,
    campaign_kind,
    completed_period,
    import_plan,
    selected_facts,
)
from app.ozon.async_api import AsyncSellerApi
from app.ozon.async_performance import AsyncPerformanceClient, PerformanceApiFailure
from app.ozon.limit_identity import performance_identity

logger = tagged_logger(__name__, "economics", "ozon", "ozon_advertising_breakdown")


async def campaign_snapshot(
    client: AsyncPerformanceClient, store: str, period: AdvertisingPeriod
) -> AdvertisingSnapshot:
    campaigns = {item.id: item for item in await client.campaigns(period)}
    daily = await client.daily(period)
    if any(not period.start <= row.day <= period.end for row in daily.rows):
        raise ValueError(AdvertisingError.CAMPAIGN_DATE_OUTSIDE_PERIOD)
    if any(row.campaign_id not in campaigns for row in daily.rows):
        raise ValueError(AdvertisingError.INVENTORY_INCOMPLETE)
    ids = tuple(
        sorted(
            {
                row.campaign_id
                for row in daily.rows
                if campaign_kind(campaigns[row.campaign_id])
                not in (AdvertisingKind.CPC, AdvertisingKind.CPO_SELECTED, AdvertisingKind.CPO_ALL)
            }
        )
    )
    facts: list[AdvertisingFact] = []
    corrections: list[AdvertisingCorrection] = []
    reported_kinds: set[AdvertisingKind] = set()
    for offset in range(0, len(ids), OZON_ADVERTISING.campaigns_per_report):
        batch = ids[offset : offset + OZON_ADVERTISING.campaigns_per_report]
        reports = await client.campaign_reports(period, batch)
        if set(reports) != set(batch):
            raise ValueError(AdvertisingError.REPORT_INCOMPLETE)
        for campaign_id in batch:
            campaign = campaigns[campaign_id]
            kind = campaign_kind(campaign)
            report = reports[campaign_id].report
            reported_kinds.add(kind)
            for row in report.rows:
                facts.append(
                    AdvertisingFact(
                        day=row.day,
                        kind=kind,
                        amount=row.amount,
                        impressions=row.impressions if kind == AdvertisingKind.CPC else None,
                        clicks=row.clicks if kind == AdvertisingKind.CPC else None,
                        raw_type=campaign.adv_object_type,
                        campaign_id=campaign_id,
                        advertised_sku=row.sku or None,
                    )
                )
            if report.totals.corrections:
                corrections.append(
                    AdvertisingCorrection(
                        campaign_id=campaign_id,
                        kind=kind,
                        amount=report.totals.corrections,
                    )
                )
            if not report.rows and any(row.campaign_id == campaign_id and row.amount for row in daily.rows):
                raise ValueError(AdvertisingError.REPORT_MISSING_EXPENSES)
    uncertain = {AdvertisingKind.BLOGGER, AdvertisingKind.VK, AdvertisingKind.OTHER}
    present = {campaign_kind(campaign) for campaign in campaigns.values()}
    return AdvertisingSnapshot(
        store_slug=store,
        source=AdvertisingSource.CAMPAIGNS,
        period=period,
        facts=tuple(facts),
        campaigns=tuple(campaigns.values()),
        corrections=tuple(corrections),
        unavailable_kinds=tuple(sorted((uncertain & present) - reported_kinds)),
        includes_cpc=False,
        synced_at=datetime.now(UTC),
    )


async def cpc_snapshot(
    client: AsyncPerformanceClient, store: str, period: AdvertisingPeriod
) -> AdvertisingSnapshot:
    inventory = {item.id: item for item in await client.campaigns(period)}
    campaigns = {key: item for key, item in inventory.items() if campaign_kind(item) == AdvertisingKind.CPC}
    ids = tuple(sorted(campaigns))
    facts: list[AdvertisingFact] = []
    corrections: list[AdvertisingCorrection] = []
    recent = period.start >= completed_period().end
    saved_traffic = await client.saved_product_traffic(period, ids) if not recent and ids else None
    daily = None if recent or saved_traffic is not None or not ids else await client.daily(period)
    if daily is not None and any(not period.start <= row.day <= period.end for row in daily.rows):
        raise ValueError(AdvertisingError.CAMPAIGN_DATE_OUTSIDE_PERIOD)
    if daily is not None and any(row.campaign_id not in inventory for row in daily.rows):
        raise ValueError(AdvertisingError.INVENTORY_INCOMPLETE)
    historical_ids = {row.campaign_id for row in daily.rows} if daily is not None else set()
    batches = (
        (ids,)
        if ids and (recent or saved_traffic is not None)
        else tuple(
            ids[offset : offset + OZON_ADVERTISING.campaigns_per_report]
            for offset in range(0, len(ids), OZON_ADVERTISING.campaigns_per_report)
        )
    )
    for batch in batches:
        traffic = saved_traffic
        if traffic is None:
            traffic = (
                await client.product_traffic(period, batch)
                if recent
                else await client.saved_product_traffic(period, batch)
            )
        if traffic is not None:
            facts.extend(
                AdvertisingFact(
                    day=row.day,
                    kind=AdvertisingKind.CPC,
                    amount=row.amount,
                    impressions=row.impressions,
                    clicks=row.clicks,
                    raw_type=campaigns[row.campaign_id].adv_object_type,
                    campaign_id=row.campaign_id,
                    advertised_sku=row.sku,
                )
                for row in traffic.rows
            )
            continue
        if not await client.has_campaign_report(period, batch):
            batch = tuple(campaign_id for campaign_id in batch if campaign_id in historical_ids)
            if not batch:
                continue
        reports = await client.campaign_reports(period, batch)
        if set(reports) != set(batch):
            raise ValueError(AdvertisingError.REPORT_INCOMPLETE)
        for campaign_id in batch:
            report = reports[campaign_id].report
            facts.extend(
                AdvertisingFact(
                    day=row.day,
                    kind=AdvertisingKind.CPC,
                    amount=row.amount,
                    impressions=row.impressions,
                    clicks=row.clicks,
                    raw_type=campaigns[campaign_id].adv_object_type,
                    campaign_id=campaign_id,
                    advertised_sku=row.sku or None,
                )
                for row in report.rows
            )
            if report.totals.corrections:
                corrections.append(
                    AdvertisingCorrection(
                        campaign_id=campaign_id, kind=AdvertisingKind.CPC, amount=report.totals.corrections
                    )
                )
            if (
                not report.rows
                and daily is not None
                and any(row.campaign_id == campaign_id and row.amount for row in daily.rows)
            ):
                raise ValueError(AdvertisingError.REPORT_MISSING_EXPENSES)
    return AdvertisingSnapshot(
        store_slug=store,
        source=AdvertisingSource.CPC,
        period=period,
        facts=tuple(facts),
        campaigns=tuple(campaigns.values()),
        corrections=tuple(corrections),
        traffic_collected=True,
        synced_at=datetime.now(UTC),
    )


async def source_snapshot(
    client: AsyncPerformanceClient, store: str, source: AdvertisingSource, period: AdvertisingPeriod
) -> AdvertisingSnapshot:
    if source == AdvertisingSource.CPC:
        return await cpc_snapshot(client, store, period)
    if source == AdvertisingSource.CAMPAIGNS:
        return await campaign_snapshot(client, store, period)
    over_expenses = Decimal(0)
    if source == AdvertisingSource.SELECTED:
        report = await client.selected_orders(period)
        facts = selected_facts(report)
        over_expenses = report.over_expenses
    else:
        facts = all_product_facts(await client.all_product_orders(period))
    return AdvertisingSnapshot(
        store_slug=store,
        source=source,
        period=period,
        facts=facts,
        synced_at=datetime.now(UTC),
        over_expenses=over_expenses,
        unavailable_kinds=(AdvertisingKind.CPO_SELECTED, AdvertisingKind.CPO_COMBO) if over_expenses else (),
    )


def safe_error(error: Exception) -> str:
    if isinstance(error, PerformanceApiFailure):
        return str(error)
    if isinstance(error, (ValueError, ValidationError)):
        return AdvertisingError.INVALID_RESPONSE
    return AdvertisingError.SYNC_FAILED


async def sync_store(
    store: str, resources: AsyncResources, period: AdvertisingPeriod, *, intraday: bool = False
) -> bool:
    async with async_locks.hold(OZON_ADVERTISING.lock_prefix + store, resources.database):
        yesterday = completed_period().end
        if period.end > yesterday:
            raise ValueError(AdvertisingError.INVALID_PERIOD)
        now = datetime.now(TIME.moscow_timezone)
        today = AdvertisingPeriod(start=now.date(), end=now.date())
        slot = now.replace(minute=0, second=0, microsecond=0).isoformat()
        statuses = await resources.advertising.overlapping(
            (store,), AdvertisingPeriod(start=period.start, end=today.end if intraday else period.end)
        )
        imports = import_plan(period, statuses, yesterday)
        if intraday:
            imports = tuple(
                item
                for item in imports
                if item.period.end != yesterday
                or now.time() >= time(OZON_ADVERTISING.run_hour_msk, OZON_ADVERTISING.run_minute_msk)
            )
            imports = (
                tuple(
                    AdvertisingImport(source=source, period=today)
                    for source in OZON_ADVERTISING.intraday_sources
                    if not any(
                        status.source == source
                        and status.period == today
                        and status.state == AdvertisingState.READY
                        and status.snapshot
                        and status.snapshot.refresh_slot == slot
                        for status in statuses
                    )
                )
                + imports
            )
        if not imports:
            return True
        try:
            client_id, secret = tokens.get_performance_credentials(store)
        except tokens.OzonCredentialsNotFoundError:
            for item in imports:
                await resources.advertising.fail(
                    AdvertisingSourceStatus(
                        store_slug=store,
                        source=item.source,
                        period=item.period,
                        state=AdvertisingState.NO_CREDENTIALS,
                        attempted_at=datetime.now(UTC),
                    )
                )
            return True
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
            await resources.catalog.get_ozon_articles(store),
            AsyncSellerApi(resources.http, tokens.get_order_credentials(store))
            if tokens.has_credentials(store)
            else None,
        )
        success = True
        for item in imports:
            client.refresh_slot = slot if item.period == today else None
            previous = next(
                (
                    status
                    for status in statuses
                    if status.source == item.source and status.period == item.period
                ),
                None,
            )
            previous_slot = previous.refresh_slot if previous else None
            try:
                if (
                    previous_slot
                    and previous_slot != client.refresh_slot
                    and item.source in (AdvertisingSource.SELECTED, AdvertisingSource.ALL_PRODUCTS)
                ):
                    await client.finish_previous(
                        PerformanceReportRequest(
                            method=HTTPMethod.POST
                            if item.source == AdvertisingSource.SELECTED
                            else HTTPMethod.GET,
                            path=OZON_ADVERTISING.selected_path
                            if item.source == AdvertisingSource.SELECTED
                            else OZON_ADVERTISING.all_products_path,
                            period=item.period,
                            refresh_slot=previous_slot,
                        )
                    )
                previous_slot = client.refresh_slot
                await resources.advertising.fail(
                    AdvertisingSourceStatus(
                        store_slug=store,
                        source=item.source,
                        period=item.period,
                        state=AdvertisingState.MISSING,
                        refresh_slot=client.refresh_slot,
                        attempted_at=datetime.now(UTC),
                    )
                )
                snapshot = await source_snapshot(client, store, item.source, item.period)
                snapshot = snapshot.model_copy(
                    update={"finalized": item.period.end < today.start, "refresh_slot": client.refresh_slot}
                )
                await resources.advertising.save(await resolver.attach(snapshot))
                logger.info(
                    "ozon_advertising_source_loaded store=%s source=%s rows=%s",
                    store,
                    item.source.value,
                    len(snapshot.facts),
                )
            except Exception as error:
                optional_missing = (
                    (store, item.source) in OZON_ADVERTISING.optional_campaign_sources
                    and isinstance(error, PerformanceApiFailure)
                    and error.status_code == HTTPStatus.NOT_FOUND
                    and error.code == AdvertisingError.CAMPAIGN_NOT_FOUND
                )
                if not optional_missing:
                    success = False
                code = safe_error(error)
                await resources.advertising.fail(
                    AdvertisingSourceStatus(
                        store_slug=store,
                        source=item.source,
                        period=item.period,
                        state=AdvertisingState.MISSING if optional_missing else AdvertisingState.ERROR,
                        refresh_slot=previous_slot,
                        attempted_at=datetime.now(UTC),
                        error=None if optional_missing else code,
                    )
                )
                if optional_missing:
                    logger.info(
                        "ozon_advertising_campaign_absent store=%s source=%s", store, item.source.value
                    )
                else:
                    logger.warning(
                        "ozon_advertising_source_failed store=%s source=%s code=%s",
                        store,
                        item.source.value,
                        code,
                    )
                if isinstance(error, PerformanceApiFailure) and error.status_code in (
                    HTTPStatus.UNAUTHORIZED,
                    HTTPStatus.FORBIDDEN,
                ):
                    break
        return success


async def sync_all(
    stores: tuple[str, ...] | None = None,
    *,
    resources: AsyncResources,
    period: AdvertisingPeriod | None = None,
) -> AdvertisingSyncResult:
    targets = tuple(dict.fromkeys(STORES if stores is None else stores))
    window = period or completed_period()
    results: dict[str, bool] = {}

    async def run(store: str) -> None:
        try:
            results[store] = await sync_store(store, resources, window, intraday=period is None)
        except Exception as error:
            results[store] = False
            logger.warning("ozon_advertising_store_failed store=%s code=%s", store, safe_error(error))

    async with asyncio.TaskGroup() as group:
        for store in targets:
            group.create_task(run(store))
    failed = tuple(store for store in targets if not results[store])
    return AdvertisingSyncResult(
        stores=targets, failed_stores=failed, error=AdvertisingError.STORES_FAILED if failed else None
    )
