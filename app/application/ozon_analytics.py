import asyncio
import calendar
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.application.ports import OzonAnalyticsClient, OzonAnalyticsStore
from app.core.constants import (
    OZON_ANALYTICS,
    TIME,
    HTTPStatus,
    OzonAnalyticsCommentKind,
    OzonAnalyticsReason,
    OzonAnalyticsRunState,
)
from app.dto.orders import OzonArticleIndex
from app.dto.ozon_analytics import (
    OzonAnalyticsAnalyzer,
    OzonAnalyticsApiError,
    OzonAnalyticsAuthor,
    OzonAnalyticsComment,
    OzonAnalyticsCommentChange,
    OzonAnalyticsCommentKey,
    OzonAnalyticsCommentRevision,
    OzonAnalyticsEphemerides,
    OzonAnalyticsForecastSettings,
    OzonAnalyticsFunnelRun,
    OzonAnalyticsHistory,
    OzonAnalyticsNote,
    OzonAnalyticsNoteCreate,
    OzonAnalyticsNoteRevision,
    OzonAnalyticsNoteUpdate,
    OzonAnalyticsReference,
    OzonAnalyticsRequestError,
    OzonAnalyticsSales,
    OzonAnalyticsSalesQuery,
    OzonAnalyticsSourceStatus,
    OzonAnalyticsStockSnapshot,
    OzonAnalyticsSyncResult,
    OzonAnalyticsWeekQuery,
)
from app.ozon.analytics_normalizer import OzonAnalyticsCalculator, dates, history, monday, sales


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonAnalyticsAccountStore:
    store: str
    client: OzonAnalyticsClient
    catalog: OzonArticleIndex


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonAnalyticsDaySelection:
    day: date
    queue: str
    priority: int
    attempted_at: datetime


def source_window(today: date) -> tuple[date, ...]:
    month = today.year * OZON_ANALYTICS.months_per_year + today.month - 1 - OZON_ANALYTICS.backfill_months
    year, index = divmod(month, OZON_ANALYTICS.months_per_year)
    start = date(year, index + 1, min(today.day, calendar.monthrange(year, index + 1)[1]))
    return tuple(reversed(dates(start, today)))


def next_source_day(
    runs: tuple[OzonAnalyticsFunnelRun, ...], now: datetime, last_queue: str | None
) -> OzonAnalyticsDaySelection | None:
    window = source_window(now.astimezone(UTC).date())
    by_day = {item.source_day: item for item in runs}
    initial = tuple(day for day in window[: OZON_ANALYTICS.traffic_days] if day not in by_day)
    unfinished = tuple(
        item
        for item in runs
        if item.source_day in window[: OZON_ANALYTICS.traffic_days]
        and item.state != OzonAnalyticsRunState.COMPLETE
        and (item.next_retry_at is None or item.next_retry_at <= now)
    )
    if unfinished:
        item = min(unfinished, key=lambda value: value.started_at)
        return OzonAnalyticsDaySelection(
            day=item.source_day, queue=OZON_ANALYTICS.recent_queue, priority=0, attempted_at=item.started_at
        )
    if initial:
        return OzonAnalyticsDaySelection(
            day=initial[0], queue=OZON_ANALYTICS.recent_queue, priority=0, attempted_at=now
        )
    recent: list[OzonAnalyticsDaySelection] = []
    backfill: list[OzonAnalyticsDaySelection] = []
    for day in window:
        run = by_day.get(day)
        if run is not None and run.next_retry_at is not None and run.next_retry_at > now:
            continue
        queue = (
            OZON_ANALYTICS.recent_queue
            if day in window[: OZON_ANALYTICS.traffic_days]
            else OZON_ANALYTICS.backfill_queue
        )
        target = recent if queue == OZON_ANALYTICS.recent_queue else backfill
        if run is None or run.state != OzonAnalyticsRunState.COMPLETE:
            target.append(
                OzonAnalyticsDaySelection(
                    day=day,
                    queue=queue,
                    priority=0 if run is not None else 1,
                    attempted_at=run.started_at if run is not None else now,
                )
            )
        elif (now - run.updated_at).total_seconds() >= OZON_ANALYTICS.refresh_seconds:
            target.append(
                OzonAnalyticsDaySelection(day=day, queue=queue, priority=2, attempted_at=run.updated_at)
            )
    first = backfill if last_queue == OZON_ANALYTICS.recent_queue else recent
    second = recent if first is backfill else backfill
    candidates = first or second
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item.priority, item.attempted_at, -item.day.toordinal()))


class OzonAnalyticsService:
    def __init__(
        self,
        repository: OzonAnalyticsStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.repository = repository
        self.clock = clock
        self.sleep = sleep

    async def forecast_settings(self, stores: tuple[str, ...], store: str) -> OzonAnalyticsForecastSettings:
        if store not in stores:
            raise OzonAnalyticsRequestError(HTTPStatus.NOT_FOUND, OzonAnalyticsReason.PRODUCT_NOT_FOUND)
        return await self.repository.forecast_settings(store)

    async def save_forecast(
        self,
        stores: tuple[str, ...],
        store: str,
        change: OzonAnalyticsForecastSettings,
        author: OzonAnalyticsAuthor,
    ) -> OzonAnalyticsForecastSettings:
        if store not in stores:
            raise OzonAnalyticsRequestError(HTTPStatus.NOT_FOUND, OzonAnalyticsReason.PRODUCT_NOT_FOUND)
        return await self.repository.save_forecast(store, change, author, self.clock())

    def _week(self, query: OzonAnalyticsWeekQuery) -> date:
        try:
            return query.start(self.clock().astimezone(TIME.moscow_timezone).date())
        except ValueError as error:
            raise OzonAnalyticsRequestError(
                HTTPStatus.UNPROCESSABLE_ENTITY, OzonAnalyticsReason.INVALID_PERIOD
            ) from error

    async def analyzer(self, stores: tuple[str, ...], query: OzonAnalyticsWeekQuery) -> OzonAnalyticsAnalyzer:
        start = self._week(query)
        now = self.clock()
        sources = await self.repository.load(
            stores, start, start + timedelta(days=OZON_ANALYTICS.week_days - 1), now
        )
        return OzonAnalyticsCalculator(sources, now).analyzer(start)

    async def ephemerides(
        self, stores: tuple[str, ...], query: OzonAnalyticsWeekQuery
    ) -> OzonAnalyticsEphemerides:
        start = self._week(query)
        now = self.clock()
        sources = await self.repository.load(
            stores,
            start - timedelta(days=OZON_ANALYTICS.week_days),
            start + timedelta(days=OZON_ANALYTICS.week_days - 1),
            now,
            include_notes=False,
        )
        return OzonAnalyticsCalculator(sources, now).ephemerides(start)

    async def history(self, stores: tuple[str, ...], query: OzonAnalyticsWeekQuery) -> OzonAnalyticsHistory:
        start = self._week(query)
        catalog = await self.repository.catalog(stores)
        comments = await self.repository.comments(
            stores, start - timedelta(weeks=OZON_ANALYTICS.history_weeks - 1), start
        )
        return history(catalog, comments, start)

    async def sales(self, stores: tuple[str, ...], query: OzonAnalyticsSalesQuery) -> OzonAnalyticsSales:
        now = self.clock()
        today = now.astimezone(TIME.moscow_timezone).date()
        end = query.date_to or today
        start = query.date_from or end - timedelta(days=OZON_ANALYTICS.week_days - 1)
        sources = await self.repository.sales_sources(stores, start, end, today)
        if query.date_from is None:
            end = min(today, sources.last_day) if sources.last_day is not None else today
            start = max(OZON_ANALYTICS.first_sales_day, end - timedelta(days=OZON_ANALYTICS.week_days - 1))
            if start != (query.date_from or today - timedelta(days=OZON_ANALYTICS.week_days - 1)):
                sources = await self.repository.sales_sources(stores, start, end, today)
        return sales(sources, start, end, now)

    async def require_product(self, stores: tuple[str, ...], store: str, article: str) -> None:
        if store not in stores or not await self.repository.product_exists(store, article):
            raise OzonAnalyticsRequestError(HTTPStatus.NOT_FOUND, OzonAnalyticsReason.PRODUCT_NOT_FOUND)

    async def create_note(
        self, stores: tuple[str, ...], payload: OzonAnalyticsNoteCreate, author: OzonAnalyticsAuthor
    ) -> OzonAnalyticsNote:
        await self.require_product(stores, payload.store, payload.article)
        now = self.clock()
        if payload.day != now.astimezone(TIME.moscow_timezone).date():
            raise OzonAnalyticsRequestError(
                HTTPStatus.UNPROCESSABLE_ENTITY, OzonAnalyticsReason.CURRENT_DAY_ONLY
            )
        return await self.repository.create_note(payload, author, now)

    async def require_note(self, stores: tuple[str, ...], note_id: int) -> OzonAnalyticsNote:
        note = await self.repository.note(note_id)
        if note is None:
            raise OzonAnalyticsRequestError(HTTPStatus.NOT_FOUND, OzonAnalyticsReason.NOTE_NOT_FOUND)
        await self.require_product(stores, note.store_slug, note.article)
        return note

    async def update_note(
        self,
        stores: tuple[str, ...],
        note_id: int,
        change: OzonAnalyticsNoteUpdate,
        author: OzonAnalyticsAuthor,
    ) -> OzonAnalyticsNote:
        original = await self.require_note(stores, note_id)
        now = self.clock()
        if original.action_date != now.astimezone(TIME.moscow_timezone).date():
            raise OzonAnalyticsRequestError(
                HTTPStatus.UNPROCESSABLE_ENTITY, OzonAnalyticsReason.CURRENT_DAY_ONLY
            )
        return await self.repository.update_note(original, change, author, now)

    async def note_revisions(
        self, stores: tuple[str, ...], note_id: int
    ) -> tuple[OzonAnalyticsNoteRevision, ...]:
        await self.require_note(stores, note_id)
        return await self.repository.note_revisions(note_id)

    async def check_comment(self, stores: tuple[str, ...], key: OzonAnalyticsCommentKey) -> None:
        self._week(OzonAnalyticsWeekQuery(week=key.week))
        await self.require_product(stores, key.store, key.article)

    async def save_comment(
        self, stores: tuple[str, ...], change: OzonAnalyticsCommentChange, author: OzonAnalyticsAuthor
    ) -> OzonAnalyticsComment:
        await self.check_comment(stores, change)
        now = self.clock()
        if change.kind == OzonAnalyticsCommentKind.DECISION and change.week != monday(
            now.astimezone(TIME.moscow_timezone).date()
        ):
            raise OzonAnalyticsRequestError(HTTPStatus.FORBIDDEN, OzonAnalyticsReason.CURRENT_WEEK_ONLY)
        return await self.repository.save_comment(change, author, now)

    async def comment_revisions(
        self, stores: tuple[str, ...], key: OzonAnalyticsCommentKey
    ) -> tuple[OzonAnalyticsCommentRevision, ...]:
        await self.check_comment(stores, key)
        return await self.repository.comment_revisions(key)

    async def capture_reference(self, store: str) -> int:
        now = self.clock()
        catalog = await self.repository.catalog((store,))
        values = tuple(
            OzonAnalyticsReference(
                store_slug=store,
                article=item.article,
                day=now.astimezone(TIME.moscow_timezone).date(),
                goal_week=item.goal_week,
                goal_day=item.goal_day,
                source_synced_at=item.source_synced_at,
                captured_at=now,
            )
            for item in catalog
        )
        await self.repository.save_references(values, now)
        return len(values)

    async def capture_stock(self, store: str) -> int:
        now = self.clock()
        local = now.astimezone(TIME.moscow_timezone)
        if local.weekday() != OZON_ANALYTICS.stock_weekday or (local.hour, local.minute) < (
            OZON_ANALYTICS.stock_hour,
            OZON_ANALYTICS.stock_minute,
        ):
            return 0
        products = await self.repository.capture_stocks(store, now)
        await self.repository.save_stock(
            OzonAnalyticsStockSnapshot(
                store_slug=store, week=local.date(), captured_at=now, products=products
            )
        )
        return len(products)

    async def transit(self, store: str, client: OzonAnalyticsClient) -> int:
        try:
            snapshot = await client.transit(store, self.clock(), include_to_customer=True)
        except OzonAnalyticsApiError as error:
            attempted_at = self.clock()
            await self.repository.save_status(
                OzonAnalyticsSourceStatus(
                    store_slug=store,
                    source=OZON_ANALYTICS.transit_source,
                    attempted_at=attempted_at,
                    reason=error.reason,
                    next_retry_at=attempted_at + timedelta(seconds=error.retry_seconds)
                    if error.retry_seconds is not None
                    else None,
                )
            )
            raise
        await self.repository.save_transit(snapshot)
        return len(snapshot.products)

    async def sync_funnel_account(
        self, account: str, stores: tuple[OzonAnalyticsAccountStore, ...]
    ) -> OzonAnalyticsSyncResult:
        token = uuid4().hex
        if not await self.repository.acquire_account(account, token, self.clock()):
            return OzonAnalyticsSyncResult(deferred=1)
        attempts = 0
        completed = 0
        pending: OzonAnalyticsFunnelRun | None = None
        try:
            async with asyncio.timeout(OZON_ANALYTICS.run_timeout_seconds):
                while attempts < OZON_ANALYTICS.run_attempt_budget:
                    progress = False
                    for target in stores:
                        if attempts >= OZON_ANALYTICS.run_attempt_budget:
                            break
                        now = self.clock()
                        runs = await self.repository.runs(target.store)
                        selected = next_source_day(runs, now, await self.repository.last_queue(account))
                        if selected is None:
                            continue
                        progress = True
                        day, queue = selected.day, selected.queue
                        previous = next((item for item in runs if item.source_day == day), None)
                        resumable = (
                            previous is not None
                            and previous.state
                            in (OzonAnalyticsRunState.RUNNING, OzonAnalyticsRunState.DEFERRED)
                            and (now - previous.started_at).total_seconds()
                            <= OZON_ANALYTICS.generation_max_age_seconds
                        )
                        pending = None
                        candidate = (
                            previous.model_copy(
                                update={
                                    "state": OzonAnalyticsRunState.RUNNING,
                                    "updated_at": now,
                                    "lease_token": token,
                                    "lease_until": now
                                    + timedelta(
                                        seconds=OZON_ANALYTICS.run_timeout_seconds
                                        + OZON_ANALYTICS.lease_grace_seconds
                                    ),
                                }
                            )
                            if previous and resumable
                            else OzonAnalyticsFunnelRun(
                                account=account,
                                store_slug=target.store,
                                source_day=day,
                                generation=uuid4().hex,
                                queue=queue,
                                state=OzonAnalyticsRunState.RUNNING,
                                updated_at=now,
                                started_at=now,
                                lease_token=token,
                                lease_until=now
                                + timedelta(
                                    seconds=OZON_ANALYTICS.run_timeout_seconds
                                    + OZON_ANALYTICS.lease_grace_seconds
                                ),
                            )
                        )
                        await self.repository.start_run(candidate)
                        pending = candidate
                        while attempts < OZON_ANALYTICS.run_attempt_budget and pending is not None:
                            if pending.page_attempts >= OZON_ANALYTICS.page_attempts:
                                await self.repository.defer_run(
                                    pending,
                                    OzonAnalyticsReason.TRANSPORT_ERROR,
                                    self.clock() + timedelta(seconds=OZON_ANALYTICS.refresh_seconds),
                                    self.clock(),
                                    failed=True,
                                )
                                break
                            reservation = await self.repository.reserve_attempt(pending, self.clock())
                            if not reservation.allowed:
                                await self.repository.defer_run(
                                    pending,
                                    reservation.reason or OzonAnalyticsReason.QUOTA_DEFERRED,
                                    reservation.next_retry_at
                                    or self.clock() + timedelta(seconds=OZON_ANALYTICS.budget_window_seconds),
                                    self.clock(),
                                    account_wide=True,
                                )
                                return OzonAnalyticsSyncResult(
                                    attempts=attempts, products=completed, deferred=1
                                )
                            attempts += 1
                            pending = pending.model_copy(update={"page_attempts": reservation.page_attempts})
                            attempted_run = pending
                            try:
                                page = await target.client.funnel(
                                    pending.source_day, pending.offset, target.catalog
                                )
                                pending = await self.repository.checkpoint(pending, page, self.clock())
                                if pending.state == OzonAnalyticsRunState.COMPLETE:
                                    completed += len(page.items)
                                    pending = None
                                    break
                            except OzonAnalyticsApiError as error:
                                now = self.clock()
                                account_wide = error.reason in (
                                    OzonAnalyticsReason.SUBSCRIPTION_REQUIRED,
                                    OzonAnalyticsReason.QUOTA_DEFERRED,
                                )
                                retriable = error.reason in (
                                    OzonAnalyticsReason.RATE_LIMITED,
                                    OzonAnalyticsReason.TRANSPORT_ERROR,
                                )
                                pause = max(
                                    error.retry_seconds or 0,
                                    OZON_ANALYTICS.budget_window_seconds
                                    if account_wide
                                    else OZON_ANALYTICS.refresh_seconds
                                    if not retriable
                                    or attempted_run.page_attempts >= OZON_ANALYTICS.page_attempts
                                    else 0,
                                )
                                retry_at = now + timedelta(seconds=pause)
                                if (
                                    account_wide
                                    or not retriable
                                    or attempted_run.page_attempts >= OZON_ANALYTICS.page_attempts
                                    or pause >= OZON_ANALYTICS.run_timeout_seconds
                                ):
                                    await self.repository.defer_run(
                                        attempted_run,
                                        error.reason,
                                        retry_at,
                                        now,
                                        failed=not account_wide
                                        and (
                                            not retriable
                                            or attempted_run.page_attempts >= OZON_ANALYTICS.page_attempts
                                        ),
                                        account_wide=account_wide,
                                        raw_payload=error.raw_payload,
                                    )
                                    if account_wide:
                                        return OzonAnalyticsSyncResult(
                                            attempts=attempts, products=completed, deferred=1
                                        )
                                    break
                                if pause:
                                    await self.sleep(pause)
                        if (
                            pending is not None
                            and pending.state == OzonAnalyticsRunState.RUNNING
                            and attempts >= OZON_ANALYTICS.run_attempt_budget
                        ):
                            await self.repository.defer_run(
                                pending,
                                OzonAnalyticsReason.RUN_DEFERRED,
                                self.clock() + timedelta(seconds=OZON_ANALYTICS.interval_seconds),
                                self.clock(),
                            )
                    if not progress:
                        break
        except TimeoutError:
            if pending is not None:
                await self.repository.defer_run(
                    pending,
                    OzonAnalyticsReason.RUN_DEFERRED,
                    self.clock() + timedelta(seconds=OZON_ANALYTICS.interval_seconds),
                    self.clock(),
                )
            return OzonAnalyticsSyncResult(attempts=attempts, products=completed, deferred=1)
        finally:
            cleanup = asyncio.create_task(self.repository.release_account(account, token))
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup.result()
            if cancelled:
                raise asyncio.CancelledError
        return OzonAnalyticsSyncResult(
            attempts=attempts, products=completed, deferred=int(attempts >= OZON_ANALYTICS.run_attempt_budget)
        )
