from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Table, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.constants import (
    OZON_ADVERTISING,
    OZON_API_LIMITS,
    AdvertisingError,
    DatabaseDialect,
    HTTPStatus,
    OzonReservationState,
)
from app.dto.ozon_limits import (
    OzonRateFeedback,
    OzonRateScope,
    OzonRateState,
    PerformanceAdmission,
    PerformanceCompletion,
    PerformanceIdentity,
    PerformanceLease,
    PerformanceQuotaRequest,
    PerformanceReservation,
)
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.ozon_advertising_orm import OzonAdvertisingReportRecord, OzonAdvertisingRequestRecord
from app.infrastructure.ozon_limits_orm import (
    OzonApiControlRecord,
    OzonPerformanceBudgetRecord,
    OzonPerformanceLeaseRecord,
)

CONTROL = table_for(OzonApiControlRecord)
BUDGET = table_for(OzonPerformanceBudgetRecord)
LEASES = table_for(OzonPerformanceLeaseRecord)


class OzonLimitsRepository:
    def __init__(self, database: AsyncDatabase, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.database = database
        self.clock = clock

    @staticmethod
    async def _insert_ignore(connection: AsyncConnection, table: Table, values: dict[str, object]) -> None:
        statement = (
            postgres_insert(table)
            if connection.dialect.name == DatabaseDialect.POSTGRESQL
            else sqlite_insert(table)
        )
        await connection.execute(statement.values(values).on_conflict_do_nothing())

    @asynccontextmanager
    async def _locked(self) -> AsyncIterator[AsyncConnection]:
        async with self.database.transaction(write=True) as connection:
            await self._insert_ignore(
                connection, CONTROL, {"scope": OZON_API_LIMITS.coordination_scope, "payload": "{}"}
            )
            await connection.execute(
                select(CONTROL.c.scope)
                .where(CONTROL.c.scope == OZON_API_LIMITS.coordination_scope)
                .with_for_update()
            )
            yield connection

    @staticmethod
    async def _rate(connection: AsyncConnection, scope: OzonRateScope, now: datetime) -> OzonRateState:
        payload = await connection.scalar(select(CONTROL.c.payload).where(CONTROL.c.scope == scope.key))
        if payload is None:
            return OzonRateState(next_at=now, spacing=scope.minimum_spacing)
        saved = OzonRateState.model_validate_json(payload)
        return saved.model_copy(update={"spacing": max(saved.spacing, scope.minimum_spacing)})

    @staticmethod
    async def _save_rate(connection: AsyncConnection, scope: OzonRateScope, state: OzonRateState) -> None:
        await connection.execute(
            upsert(connection, CONTROL, {"scope": scope.key, "payload": state.model_dump_json()}, ("scope",))
        )

    async def reserve_rate(self, scopes: tuple[OzonRateScope, ...], now: datetime) -> float:
        async with self._locked() as connection:
            now = max(now, self.clock())
            states = tuple([await self._rate(connection, scope, now) for scope in scopes])
            delay = max((max(0, (state.next_at - now).total_seconds()) for state in states), default=0)
            if delay:
                return delay
            for scope, state in zip(scopes, states, strict=True):
                await self._save_rate(
                    connection,
                    scope,
                    state.model_copy(update={"next_at": now + timedelta(seconds=state.spacing)}),
                )
        return 0

    async def observe_rate(self, scope: OzonRateScope, feedback: OzonRateFeedback, now: datetime) -> None:
        async with self._locked() as connection:
            now = max(now, self.clock())
            saved = await self._rate(connection, scope, now)
            spacing, successes, next_at = saved.spacing, saved.successes, saved.next_at
            if feedback.status == HTTPStatus.TOO_MANY_REQUESTS:
                spacing = max(
                    scope.minimum_spacing,
                    min(
                        OZON_API_LIMITS.maximum_spacing_seconds,
                        max(spacing, OZON_API_LIMITS.minimum_retry_seconds) * 2,
                    ),
                )
                delay = max(spacing, feedback.retry_seconds or 0)
                next_at = max(next_at, now + timedelta(seconds=delay))
                successes = 0
            elif HTTPStatus.OK <= feedback.status < HTTPStatus.MULTIPLE_CHOICES:
                successes += 1
                if successes >= OZON_API_LIMITS.successes_before_decrease:
                    spacing = max(scope.minimum_spacing, spacing * OZON_API_LIMITS.spacing_decrease_factor)
                    successes = 0
                if feedback.remaining is not None and feedback.remaining <= 0:
                    next_at = max(
                        next_at,
                        now
                        + timedelta(
                            seconds=max(
                                spacing, feedback.retry_seconds or OZON_API_LIMITS.minimum_retry_seconds
                            )
                        ),
                    )
            await self._save_rate(
                connection, scope, OzonRateState(next_at=next_at, spacing=spacing, successes=successes)
            )

    async def _import_legacy(
        self, connection: AsyncConnection, identity: PerformanceIdentity, now: datetime
    ) -> None:
        accounts = identity.accounts or (identity,)
        cutoff = now - timedelta(seconds=OZON_API_LIMITS.window_seconds)
        journal = table_for(OzonAdvertisingRequestRecord)
        for account in accounts:
            if (
                account.organization_scope != identity.organization_scope
                and account.account_scope != identity.account_scope
            ):
                continue
            marker = OZON_API_LIMITS.legacy_scope_prefix + account.account_scope + ":" + account.store_slug
            imported = await connection.scalar(select(CONTROL.c.scope).where(CONTROL.c.scope == marker))
            if imported is None:
                rows = (
                    await connection.execute(
                        select(journal).where(
                            journal.c.store_slug == account.store_slug,
                            journal.c.attempted_at >= cutoff.date().isoformat(),
                        )
                    )
                ).mappings()
                batch: list[dict[str, object]] = []
                for row in rows:
                    observed = datetime.fromisoformat(row["attempted_at"])
                    observed = (
                        observed.replace(tzinfo=UTC) if observed.tzinfo is None else observed.astimezone(UTC)
                    )
                    if observed <= cutoff:
                        continue
                    batch.append(
                        {
                            "id": row["id"],
                            "store_slug": account.store_slug,
                            "account_scope": account.account_scope,
                            "organization_scope": account.organization_scope,
                            "attempted_at": observed,
                            "export_units": row["export_units"],
                            "http_units": int(row["path"] != OZON_ADVERTISING.products_path),
                            "state": OzonReservationState.RESOLVED,
                            "request_key": None,
                        }
                    )
                    if len(batch) >= OZON_API_LIMITS.legacy_batch_size:
                        statement = (
                            postgres_insert(BUDGET)
                            if connection.dialect.name == DatabaseDialect.POSTGRESQL
                            else sqlite_insert(BUDGET)
                        )
                        await connection.execute(statement.on_conflict_do_nothing(), batch)
                        batch = []
                if batch:
                    statement = (
                        postgres_insert(BUDGET)
                        if connection.dialect.name == DatabaseDialect.POSTGRESQL
                        else sqlite_insert(BUDGET)
                    )
                    await connection.execute(statement.on_conflict_do_nothing(), batch)
                await self._insert_ignore(connection, CONTROL, {"scope": marker, "payload": "{}"})
            await connection.execute(
                update(BUDGET)
                .where(
                    BUDGET.c.store_slug == account.store_slug,
                    BUDGET.c.attempted_at > cutoff,
                    BUDGET.c.organization_scope != account.organization_scope,
                )
                .values(organization_scope=account.organization_scope)
            )
        last = await connection.scalar(
            select(CONTROL.c.payload).where(CONTROL.c.scope == OZON_API_LIMITS.budget_cleanup_scope)
        )
        if last != now.date().isoformat():
            await connection.execute(
                delete(BUDGET).where(
                    BUDGET.c.attempted_at < now - timedelta(days=OZON_API_LIMITS.budget_retention_days)
                )
            )
            await connection.execute(
                upsert(
                    connection,
                    CONTROL,
                    {"scope": OZON_API_LIMITS.budget_cleanup_scope, "payload": now.date().isoformat()},
                    ("scope",),
                )
            )

    async def reserve_performance(
        self, request: PerformanceQuotaRequest, now: datetime
    ) -> PerformanceAdmission:
        identity = request.identity
        async with self._locked() as connection:
            now = max(now, self.clock())
            await self._import_legacy(connection, identity, now)
            cutoff = now - timedelta(seconds=OZON_API_LIMITS.window_seconds)
            account_count, account_units = (
                await connection.execute(
                    select(
                        func.coalesce(func.sum(BUDGET.c.http_units), 0),
                        func.coalesce(func.sum(BUDGET.c.export_units), 0),
                    ).where(BUDGET.c.account_scope == identity.account_scope, BUDGET.c.attempted_at > cutoff)
                )
            ).one()
            if (
                request.http_units
                and account_count + request.http_units > OZON_API_LIMITS.performance_requests_per_day
            ):
                return PerformanceAdmission(error=AdvertisingError.DAILY_LIMIT)
            if request.export_units:
                organization_units = await connection.scalar(
                    select(func.coalesce(func.sum(BUDGET.c.export_units), 0)).where(
                        BUDGET.c.organization_scope == identity.organization_scope,
                        BUDGET.c.attempted_at > cutoff,
                    )
                )
                if (
                    account_units + request.export_units > request.account_export_limit
                    or (organization_units or 0) + request.export_units
                    > OZON_API_LIMITS.performance_exports_per_day
                ):
                    return PerformanceAdmission(error=AdvertisingError.DAILY_LIMIT)
            if request.export_units and request.request_key is not None:
                uncertain = await connection.scalar(
                    select(BUDGET.c.id)
                    .where(
                        BUDGET.c.account_scope == identity.account_scope,
                        BUDGET.c.attempted_at > now - timedelta(seconds=OZON_API_LIMITS.lease_seconds),
                        BUDGET.c.request_key.is_not(None),
                        BUDGET.c.export_units > 0,
                        BUDGET.c.state.in_(
                            (
                                OzonReservationState.RESERVED,
                                OzonReservationState.UNCERTAIN,
                                OzonReservationState.ACCEPTED,
                            )
                        ),
                    )
                    .limit(1)
                )
                if uncertain is not None:
                    return PerformanceAdmission(error=AdvertisingError.ACTIVE_LIMIT)
            reservation = PerformanceReservation(id=uuid4().hex, export_units=request.export_units)
            await connection.execute(
                BUDGET.insert().values(
                    id=reservation.id,
                    store_slug=identity.store_slug,
                    account_scope=identity.account_scope,
                    organization_scope=identity.organization_scope,
                    attempted_at=now,
                    export_units=request.export_units,
                    http_units=request.http_units,
                    state=OzonReservationState.RESERVED,
                    request_key=request.request_key,
                )
            )
            return PerformanceAdmission(reservation=reservation)

    async def finish_performance(self, completion: PerformanceCompletion) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                update(BUDGET)
                .where(BUDGET.c.id == completion.reservation.id)
                .values(
                    state=completion.state,
                    export_units=0
                    if completion.state == OzonReservationState.REJECTED
                    else completion.reservation.export_units,
                )
            )

    async def acquire_report(self, identity: PerformanceIdentity, now: datetime) -> PerformanceLease | None:
        async with self._locked() as connection:
            now = max(now, self.clock())
            await connection.execute(delete(LEASES).where(LEASES.c.expires_at <= now))
            if await connection.scalar(
                select(LEASES.c.token).where(LEASES.c.account_scope == identity.account_scope)
            ):
                return None
            active = (
                (
                    await connection.execute(
                        select(LEASES).where(LEASES.c.organization_scope == identity.organization_scope)
                    )
                )
                .mappings()
                .all()
            )
            accounts = identity.accounts or (identity,)
            organizations = tuple(
                account.store_slug
                for account in accounts
                if account.organization_scope == identity.organization_scope
            )
            by_store = {account.store_slug: account.account_scope for account in accounts}
            reports = table_for(OzonAdvertisingReportRecord)
            pending = (
                await connection.execute(
                    select(reports.c.store_slug)
                    .where(reports.c.store_slug.in_(organizations), reports.c.pending == 1)
                    .distinct()
                )
            ).scalars()
            occupied = {row["account_scope"] for row in active} | {
                by_store.get(store, store) for store in pending
            }
            if len(occupied - {identity.account_scope}) >= OZON_API_LIMITS.organization_active_reports:
                return None
            lease = PerformanceLease(token=uuid4().hex)
            await connection.execute(
                LEASES.insert().values(
                    token=lease.token,
                    store_slug=identity.store_slug,
                    account_scope=identity.account_scope,
                    organization_scope=identity.organization_scope,
                    expires_at=now + timedelta(seconds=OZON_API_LIMITS.lease_seconds),
                )
            )
            return lease

    async def renew_report(self, lease: PerformanceLease, now: datetime) -> None:
        async with self.database.transaction(write=True) as connection:
            result = await connection.execute(
                update(LEASES)
                .where(LEASES.c.token == lease.token)
                .values(expires_at=now + timedelta(seconds=OZON_API_LIMITS.lease_seconds))
            )
            if result.rowcount != 1:
                raise RuntimeError("Performance report lease expired")

    async def release_report(self, lease: PerformanceLease) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(delete(LEASES).where(LEASES.c.token == lease.token))

    async def resolve_report(self, identity: PerformanceIdentity, request_key: str) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                update(BUDGET)
                .where(
                    BUDGET.c.account_scope == identity.account_scope,
                    BUDGET.c.request_key == request_key,
                    BUDGET.c.state != OzonReservationState.REJECTED,
                )
                .values(state=OzonReservationState.RESOLVED)
            )
