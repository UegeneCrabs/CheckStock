from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from app.core.constants import (
    OZON_ORDERS,
    TIME,
    OrderScheme,
    OrderSyncStage,
)
from app.core.logging_config import tagged_logger
from app.dto.orders import (
    OrderPeriod,
    OrderSaveBatch,
    OrderSyncIssue,
    OzonArticleIndex,
    OzonCredentials,
    OzonOrderDayState,
    OzonPendingPosting,
    SalesOrderLine,
    StoreOrderSyncResult,
)
from app.infrastructure.async_http import store_context
from app.infrastructure.async_resources import AsyncResources
from app.jobs import async_locks
from app.jobs.locks import SyncJobBusyError
from app.orders.normalizers import ozon
from app.ozon import tokens as ozon_tokens
from app.ozon.async_api import AsyncSellerApi

logger = tagged_logger(__name__, "orders", "ozon", "ozon_async")


async def record_failure(batch: OrderSaveBatch, resources: AsyncResources) -> StoreOrderSyncResult:
    errors = batch.errors
    try:
        states = {(item.scheme, item.day): item for item in await resources.orders.days((batch.store_slug,))}
        affected = {(item.scheme, item.day) for item in await resources.orders.pending(batch.store_slug)}
        day = batch.period.start
        while day < batch.period.end:
            for scheme in OZON_ORDERS.schemes:
                state = states.get((scheme, day))
                if state is None or not state.finalized:
                    affected.add((scheme, day))
            day += timedelta(days=1)
        days = tuple(
            states.get(
                (scheme, day),
                OzonOrderDayState(
                    store_slug=batch.store_slug,
                    scheme=scheme,
                    day=day,
                    attempted_at=batch.attempted_at,
                ),
            ).model_copy(update={"ok": False, "attempted_at": batch.attempted_at, "error": batch.error})
            for scheme, day in affected
        )
        batch = batch.model_copy(update={"days": days, "incremental": True})
        await resources.orders.save(batch)
    except Exception as error:
        errors += (
            OrderSyncIssue(
                stage=OrderSyncStage.SAVE,
                error_type=type(error).__name__,
                message=str(error)[: OZON_ORDERS.max_error_length],
            ),
        )
        logger.exception("ozon_orders_error_tracking_failed store=%s", batch.store_slug)
    return StoreOrderSyncResult(store_slug=batch.store_slug, rows=0, errors=errors)


async def sync_configured_store(
    store: str,
    period: OrderPeriod,
    resources: AsyncResources,
    *,
    wait_for_lock: bool = False,
    retry_incomplete_days: bool = False,
) -> StoreOrderSyncResult | None:
    attempted_at = datetime.now(TIME.moscow_timezone).replace(microsecond=0)
    try:
        if not ozon_tokens.has_credentials(store):
            return None
        credentials = ozon_tokens.get_order_credentials(store)
    except Exception as error:
        issue = OrderSyncIssue(
            stage=OrderSyncStage.CREDENTIALS,
            error_type=type(error).__name__,
            message=str(error)[: OZON_ORDERS.max_error_length],
        )
        return await record_failure(
            OrderSaveBatch(
                store_slug=store, lines=(), attempted_at=attempted_at, period=period, errors=(issue,)
            ),
            resources,
        )
    try:
        async with async_locks.hold(OZON_ORDERS.lock_prefix + store, resources.database, wait=wait_for_lock):
            return await sync_store(
                store, credentials, period, resources, retry_incomplete_days=retry_incomplete_days
            )
    except SyncJobBusyError:
        return None


async def sync_store(
    store: str,
    credentials: OzonCredentials,
    period: OrderPeriod,
    resources: AsyncResources,
    *,
    retry_incomplete_days: bool = False,
) -> StoreOrderSyncResult:
    context = store_context.set(store)
    attempted_at = datetime.now(TIME.moscow_timezone).replace(microsecond=0)
    start, end = period.start, period.end
    logger.info("ozon_orders_started store=%s start=%s end=%s", store, start, end)
    errors: list[OrderSyncIssue] = []
    try:
        api = AsyncSellerApi(resources.http, credentials)
        lines: list[SalesOrderLine] = []
        catalog: OzonArticleIndex | None = None
        today = attempted_at.date()
        states = {(item.scheme, item.day): item for item in await resources.orders.days((store,))}
        pending = await resources.orders.pending(store)
        loaded: set[tuple[OrderScheme, date]] = set()

        async def refresh(
            scheme: OrderScheme, window: OrderPeriod, targets: tuple[OzonPendingPosting, ...] = ()
        ) -> None:
            nonlocal catalog
            filter_days = tuple(item.filter_day or item.day for item in targets)
            requested = (
                OrderPeriod(start=min(filter_days), end=max(filter_days) + timedelta(days=1))
                if targets and scheme == OrderScheme.FBO
                else window
            )
            since = (
                datetime.combine(requested.start, time.min, tzinfo=TIME.moscow_timezone)
                .astimezone(UTC)
                .isoformat()
            )
            to = (
                datetime.combine(requested.end, time.min, tzinfo=TIME.moscow_timezone).astimezone(UTC)
                - timedelta(microseconds=1)
            ).isoformat()
            current = states.get(
                (scheme, window.start),
                OzonOrderDayState(
                    store_slug=store,
                    scheme=scheme,
                    day=window.start,
                    attempted_at=attempted_at,
                ),
            )
            current = current.model_copy(update={"ok": False, "attempted_at": attempted_at})
            await resources.orders.save(
                OrderSaveBatch(
                    store_slug=store,
                    lines=(),
                    attempted_at=attempted_at,
                    period=period,
                    days=(current,),
                    incremental=True,
                    report_status=False,
                )
            )
            new_lines: tuple[SalesOrderLine, ...] = ()
            issue: OrderSyncIssue | None = None
            try:
                postings = await api.postings(
                    scheme,
                    since,
                    to,
                    posting_numbers=tuple(item.posting_number for item in targets)
                    if targets and scheme == OrderScheme.FBO
                    else None,
                    order_numbers=tuple(dict.fromkeys(item.order_number for item in targets))
                    if targets and scheme == OrderScheme.FBS
                    else None,
                )
                if targets:
                    expected = {item.posting_number for item in targets}
                    postings = tuple(posting for posting in postings if posting.posting_number in expected)
                    if expected != {posting.posting_number for posting in postings}:
                        raise ValueError("Requested postings missing from response")
                if catalog is None and any(
                    not product.offer_id.strip()
                    for posting in postings
                    for product in posting.products or posting.items or ()
                ):
                    catalog = await resources.catalog.get_ozon_articles(store)
                prepared = tuple(
                    posting.prepare(scheme, path=f"postings.{index}", catalog=catalog)
                    for index, posting in enumerate(postings)
                )
                new_lines = ozon.normalize_postings(store, prepared, scheme)
                if targets and any(
                    not window.start.isoformat() <= line.ordered_at[:10] < window.end.isoformat()
                    for line in new_lines
                ):
                    raise ValueError("Order date outside requested period")
                if not targets and any(
                    not requested.start
                    <= posting.filter_day(scheme, date.fromisoformat(item.ordered_at[:10]))
                    < requested.end
                    for posting, item in zip(postings, prepared, strict=True)
                ):
                    raise ValueError("Posting filter date outside requested period")
                current = current.model_copy(
                    update={
                        "listed": current.listed or not targets,
                        "finalized": current.finalized or (not targets and window.start < today),
                        "ok": current.listed or not targets,
                        "error": None,
                    }
                )
                lines.extend(new_lines)
            except Exception as error:
                new_lines = ()
                issue = OrderSyncIssue(
                    stage=OrderSyncStage.LOAD,
                    scheme=scheme,
                    period=window,
                    error_type=type(error).__name__,
                    message=str(error)[: OZON_ORDERS.max_error_length],
                )
                errors.append(issue)
                current = current.model_copy(update={"ok": False, "error": issue.description})
            states[scheme, window.start] = current
            await resources.orders.save(
                OrderSaveBatch(
                    store_slug=store,
                    lines=new_lines,
                    attempted_at=attempted_at,
                    period=period,
                    days=(current.model_copy(update={"ok": False}) if targets else current,),
                    errors=(issue,) if issue else (),
                    incremental=True,
                    report_status=False,
                )
            )

        cursor = start
        while cursor < end:
            for scheme in OZON_ORDERS.schemes:
                state = states.get((scheme, cursor))
                retryable = cursor >= today - timedelta(days=OZON_ORDERS.retry_days)
                if state and state.finalized and state.ok:
                    continue
                if state and not retryable and not retry_incomplete_days:
                    continue
                if (
                    state
                    and cursor == today
                    and state.ok
                    and state.attempted_at.replace(minute=0, second=0, microsecond=0)
                    == attempted_at.replace(minute=0, second=0, microsecond=0)
                ):
                    continue
                loaded.add((scheme, cursor))
                await refresh(scheme, OrderPeriod(start=cursor, end=cursor + timedelta(days=1)))
            cursor += timedelta(days=1)
        grouped: dict[tuple[OrderScheme, date], list[OzonPendingPosting]] = defaultdict(list)
        for posting in pending:
            state = states.get((posting.scheme, posting.day))
            crosses_day = posting.scheme == OrderScheme.FBO and posting.filter_day not in (None, posting.day)
            requires_refresh = (posting.scheme, posting.day) in loaded and crosses_day
            if (
                state
                and state.ok
                and not requires_refresh
                and state.attempted_at.replace(minute=0, second=0, microsecond=0)
                == attempted_at.replace(minute=0, second=0, microsecond=0)
            ):
                continue
            if (posting.scheme, posting.day) not in loaded or requires_refresh:
                grouped[posting.scheme, posting.day].append(posting)
        for targets in grouped.values():
            for offset in range(0, len(targets), OZON_ORDERS.pending_batch_size):
                batch = tuple(targets[offset : offset + OZON_ORDERS.pending_batch_size])
                await refresh(
                    batch[0].scheme,
                    OrderPeriod(start=batch[0].day, end=batch[0].day + timedelta(days=1)),
                    batch,
                )
            key = targets[0].scheme, targets[0].day
            day_errors = tuple(
                issue.description
                for issue in errors
                if issue.scheme == key[0] and issue.period is not None and issue.period.start == key[1]
            )
            if day_errors:
                states[key] = states[key].model_copy(
                    update={
                        "ok": False,
                        "error": "; ".join(day_errors)[: OZON_ORDERS.max_error_length],
                    }
                )
        await resources.orders.save(
            OrderSaveBatch(
                store_slug=store,
                lines=(),
                days=tuple(states.values()),
                rows_received=len(lines),
                incremental=True,
                attempted_at=attempted_at,
                period=period,
                errors=tuple(errors),
            )
        )
        logger.info("ozon_orders_finished store=%s rows=%s ok=%s", store, len(lines), not errors)
        return StoreOrderSyncResult(store_slug=store, rows=len(lines), errors=tuple(errors))
    except Exception as error:
        issue = OrderSyncIssue(
            stage=OrderSyncStage.SAVE,
            error_type=type(error).__name__,
            message=str(error)[: OZON_ORDERS.max_error_length],
        )
        logger.warning("ozon_orders_failed store=%s error=%s", store, issue.description)
        return await record_failure(
            OrderSaveBatch(
                store_slug=store,
                lines=(),
                attempted_at=attempted_at,
                period=period,
                errors=(*errors, issue),
            ),
            resources,
        )
    finally:
        store_context.reset(context)
