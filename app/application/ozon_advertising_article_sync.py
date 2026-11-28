from datetime import date, timedelta

from app.core.constants import (
    OZON_ADVERTISING,
    AdvertisingDailyLoadState,
    AdvertisingError,
    AdvertisingSource,
    AdvertisingState,
    HTTPMethod,
)
from app.dto.ozon_advertising import (
    AdvertisingImport,
    AdvertisingPeriod,
    AdvertisingSnapshot,
    AdvertisingSourceStatus,
    AllProductsOrdersReport,
    PerformanceReportRequest,
)
from app.infrastructure.async_advertising_repository import AsyncAdvertisingRepository
from app.infrastructure.ozon_article_daily_repository import ArticleDailyRepository
from app.ozon.advertising_article_days import article_days, attach_report_articles
from app.ozon.advertising_normalizer import period_days
from app.ozon.async_performance import request_key


class ArticleDailyImport:
    def __init__(
        self,
        repository: ArticleDailyRepository,
        reports: AsyncAdvertisingRepository,
        store: str,
        today: date,
        pending: dict[AdvertisingSource, set[date]],
    ) -> None:
        self.repository = repository
        self.reports = reports
        self.store = store
        self.today = today
        self.pending = pending
        for days in self.pending.values():
            days.add(today)

    async def older_statuses(self, before: date) -> tuple[AdvertisingSourceStatus, ...]:
        found: list[AdvertisingSourceStatus] = []
        for source, dates in self.pending.items():
            for item in self.windows(source, {day for day in dates if day < before}):
                found.extend(await self.reports.overlapping((self.store,), item.period, source=source))
        return tuple(found)

    async def save(self, snapshot: AdvertisingSnapshot, days: set[date]) -> bool:
        if not days:
            return True
        if snapshot.source == AdvertisingSource.ALL_PRODUCTS and any(
            not fact.article and fact.advertised_sku and fact.advertised_sku == fact.purchased_sku
            for fact in snapshot.facts
        ):
            request = PerformanceReportRequest(
                method=HTTPMethod.GET,
                path=OZON_ADVERTISING.all_products_path,
                period=snapshot.period,
                refresh_slot=snapshot.refresh_slot,
            )
            raw = await self.reports.response(self.store, request_key(request))
            if raw is not None:
                snapshot = attach_report_articles(snapshot, AllProductsOrdersReport.model_validate_json(raw))
        success = True
        try:
            by_day = article_days(snapshot)
        except ValueError:
            for day_key in days:
                await self.repository.record(
                    self.store,
                    snapshot.source,
                    day_key,
                    snapshot.synced_at,
                    error=AdvertisingError.INVALID_RESPONSE,
                )
            return False
        for day in by_day:
            if day.day in days:
                status = await self.repository.record(
                    self.store, snapshot.source, day.day, snapshot.synced_at, day
                )
                success &= status == AdvertisingDailyLoadState.READY
                self.pending[snapshot.source].discard(day.day)
        return success

    def needed(self, source: AdvertisingSource, period: AdvertisingPeriod) -> set[date]:
        return self.pending.get(source, set()).intersection(period_days(period))

    async def restore(
        self,
        statuses: tuple[AdvertisingSourceStatus, ...],
        scheduled: tuple[AdvertisingImport, ...],
        slot: str,
    ) -> bool:
        reserved = {source: set[date]() for source in self.pending}
        for item in scheduled:
            if item.source in reserved:
                reserved[item.source].update(period_days(item.period))
        success = True
        for state in sorted(statuses, key=lambda s: s.attempted_at, reverse=True):
            snapshot = state.snapshot
            if state.source not in reserved or state.state != AdvertisingState.READY or snapshot is None:
                continue
            days = self.needed(state.source, snapshot.period) - reserved[state.source]
            if not snapshot.finalized:
                days &= {self.today} if snapshot.refresh_slot == slot else set()
            else:
                days.discard(self.today)
            if state.source == AdvertisingSource.CPC and not snapshot.traffic_collected:
                continue
            success &= await self.save(snapshot, days)
        return success

    def windows(self, source: AdvertisingSource, days: set[date]) -> tuple[AdvertisingImport, ...]:
        windows = []
        missing = days.copy()
        yesterday = self.today - timedelta(days=1)
        while missing:
            start = end = min(missing)
            missing.remove(start)
            while (
                end + timedelta(days=1) in missing
                and (end - start).days + 1 < OZON_ADVERTISING.period_days
                and end + timedelta(days=1) < yesterday
            ):
                end += timedelta(days=1)
                missing.remove(end)
            windows.append(AdvertisingImport(source=source, period=AdvertisingPeriod(start=start, end=end)))
        return tuple(windows)

    def unfinished_imports(self, scheduled: tuple[AdvertisingImport, ...]) -> tuple[AdvertisingImport, ...]:
        result = []
        for item in scheduled:
            if item.source not in self.pending:
                result.append(item)
            else:
                result.extend(self.windows(item.source, self.needed(item.source, item.period)))
        return tuple(result)

    def extra_imports(self, scheduled: tuple[AdvertisingImport, ...]) -> tuple[AdvertisingImport, ...]:
        extra: list[AdvertisingImport] = []
        for source, pending in self.pending.items():
            missing = pending.copy()
            for item in scheduled:
                if item.source == source:
                    missing.difference_update(period_days(item.period))
            extra.extend(self.windows(source, missing))
        return tuple(extra)
