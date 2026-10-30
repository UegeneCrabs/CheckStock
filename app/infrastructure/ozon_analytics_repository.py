from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import TypeVar
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Table,
    case,
    cast,
    delete,
    func,
    insert,
    or_,
    select,
    type_coerce,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import Insert as PostgresInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import Insert as SqliteInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from app.core.constants import (
    OZON_ANALYTICS,
    OZON_ORDERS,
    TIME,
    DatabaseDialect,
    HTTPStatus,
    Marketplace,
    OzonAnalyticsCommentKind,
    OzonAnalyticsReason,
    OzonAnalyticsRunState,
    StockScheme,
)
from app.dto.common import DtoModel
from app.dto.ozon_advertising import AdvertisingPeriod
from app.dto.ozon_analytics import (
    OzonAnalyticsApiError,
    OzonAnalyticsAuthor,
    OzonAnalyticsCatalog,
    OzonAnalyticsComment,
    OzonAnalyticsCommentChange,
    OzonAnalyticsCommentKey,
    OzonAnalyticsCommentRevision,
    OzonAnalyticsConflict,
    OzonAnalyticsForecastSettings,
    OzonAnalyticsFunnelDay,
    OzonAnalyticsFunnelPage,
    OzonAnalyticsFunnelRun,
    OzonAnalyticsFunnelValue,
    OzonAnalyticsIssue,
    OzonAnalyticsNote,
    OzonAnalyticsNoteCreate,
    OzonAnalyticsNoteRevision,
    OzonAnalyticsNoteUpdate,
    OzonAnalyticsPrice,
    OzonAnalyticsReference,
    OzonAnalyticsRequestError,
    OzonAnalyticsReservation,
    OzonAnalyticsSalesSources,
    OzonAnalyticsSourceBundle,
    OzonAnalyticsSourceStatus,
    OzonAnalyticsStock,
    OzonAnalyticsStockSnapshot,
    OzonAnalyticsTransit,
)
from app.dto.ozon_cabinet import OzonBuyoutSnapshot, OzonCabinetForecast, OzonCabinetSave, OzonCabinetUpdate
from app.dto.ozon_economics import OzonConfirmedDay, OzonProductsQuery, OzonTurnoverData
from app.infrastructure.async_advertising_repository import AsyncAdvertisingRepository
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.async_prices_repository import stored_price_values
from app.infrastructure.async_turnover_repository import AsyncTurnoverRepository
from app.infrastructure.economics_coverage_orm import economics_source_days, ozon_order_days
from app.infrastructure.orm import (
    FulfillmentStockRecord,
    MarketplaceStockRecord,
    RnpActionRecord,
    RnpActionRevisionRecord,
    SalesOrderLineRecord,
    StockItemRecord,
    UnitEconomics1CSourceValueRecord,
)
from app.infrastructure.ozon_analytics_orm import (
    OzonAnalyticsAccountRecord,
    OzonAnalyticsAttemptRecord,
    OzonAnalyticsCommentRecord,
    OzonAnalyticsCommentRevisionRecord,
    OzonAnalyticsFunnelDailyRecord,
    OzonAnalyticsFunnelRunRecord,
    OzonAnalyticsFunnelStagingRecord,
    OzonAnalyticsReferenceDailyRecord,
    OzonAnalyticsSourceStatusRecord,
    OzonAnalyticsStockSnapshotRecord,
    OzonAnalyticsTransitRecord,
)
from app.infrastructure.ozon_cabinet_orm import OzonBuyoutDailyRecord
from app.infrastructure.ozon_cabinet_repository import OzonCabinetConflict, OzonCabinetRepository
from app.infrastructure.ozon_economics_repository import OzonEconomicsRepository
from app.infrastructure.ozon_margin_repository import OzonMarginRepository
from app.infrastructure.ozon_prices_orm import OzonPriceDailyRecord
from app.ozon import tokens

Payload = TypeVar("Payload", bound=DtoModel)


class OzonAnalyticsRepository:
    def __init__(self, database: AsyncDatabase, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.database = database
        self.clock = clock

    async def forecast_settings(self, store: str) -> OzonAnalyticsForecastSettings:
        record = (await OzonCabinetRepository(self.database).settings((store,)))[0]
        return OzonAnalyticsForecastSettings(**record.values.forecast.model_dump(), revision=record.revision)

    async def save_forecast(
        self, store: str, change: OzonAnalyticsForecastSettings, author: OzonAnalyticsAuthor, now: datetime
    ) -> OzonAnalyticsForecastSettings:
        try:
            record = await OzonCabinetRepository(self.database).save(
                OzonCabinetSave(
                    store_slug=store,
                    change=OzonCabinetUpdate(
                        revision=change.revision,
                        forecast=OzonCabinetForecast(hourly_d_coefficients=change.hourly_d_coefficients),
                    ),
                    user_name=author.name,
                    saved_at=now,
                )
            )
        except OzonCabinetConflict as error:
            raise OzonAnalyticsConflict() from error
        return OzonAnalyticsForecastSettings(**record.values.forecast.model_dump(), revision=record.revision)

    @staticmethod
    async def _require_product(connection: AsyncConnection, store: str, article: str) -> None:
        table = table_for(StockItemRecord)
        identity = await connection.scalar(
            select(table.c.id)
            .where(
                table.c.store_slug == store,
                table.c.article == article,
                table.c.marketplace == Marketplace.OZON,
                table.c.is_service == 0,
            )
            .with_for_update()
        )
        if identity is None:
            raise OzonAnalyticsRequestError(HTTPStatus.NOT_FOUND, OzonAnalyticsReason.PRODUCT_NOT_FOUND)

    async def catalog(self, stores: tuple[str, ...]) -> tuple[OzonAnalyticsCatalog, ...]:
        stock = table_for(StockItemRecord)
        source = table_for(UnitEconomics1CSourceValueRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(
                    stock.c.store_slug,
                    stock.c.article,
                    stock.c.name,
                    stock.c.barcode,
                    func.coalesce(stock.c.image_url, "").label("image"),
                    stock.c.mp_sku,
                    stock.c.mp_product_id,
                    stock.c.mp_updated_at.label("stock_updated_at"),
                    func.coalesce(source.c.manager, "").label("manager"),
                    func.coalesce(source.c.abc_code, "").label("code"),
                    source.c.goal_week,
                    source.c.goal_day,
                    source.c.stock_status,
                    source.c.stock_end_week,
                    source.c.synced_at.label("source_synced_at"),
                )
                .select_from(stock.outerjoin(source, stock.c.id == source.c.stock_item_id))
                .where(
                    stock.c.store_slug.in_(stores),
                    stock.c.marketplace == Marketplace.OZON,
                    stock.c.is_service == 0,
                )
            )
            return tuple(OzonAnalyticsCatalog.model_validate(row) for row in rows.mappings())

    async def product_exists(self, store: str, article: str) -> bool:
        stock = table_for(StockItemRecord)
        async with self.database.transaction() as connection:
            return (
                await connection.scalar(
                    select(stock.c.id).where(
                        stock.c.store_slug == store,
                        stock.c.marketplace == Marketplace.OZON,
                        stock.c.article == article,
                        stock.c.is_service == 0,
                    )
                )
                is not None
            )

    async def _payloads(
        self,
        table: Table,
        model: type[Payload],
        stores: tuple[str, ...],
        start: date | None = None,
        end: date | None = None,
        date_field: str = "day",
    ) -> tuple[Payload, ...]:
        statement = select(table.c.payload).where(table.c.store_slug.in_(stores))
        if start is not None and end is not None:
            statement = statement.where(table.c[date_field].between(start.isoformat(), end.isoformat()))
        async with self.database.transaction() as connection:
            return tuple(
                model.model_validate_json(payload) for payload in await connection.scalars(statement)
            )

    async def load(
        self, stores: tuple[str, ...], start: date, end: date, now: datetime, *, include_notes: bool = True
    ) -> OzonAnalyticsSourceBundle:
        today = now.astimezone(TIME.moscow_timezone).date()
        stock_start = today - timedelta(days=today.weekday() + OZON_ANALYTICS.week_days)
        expanded_start = start - timedelta(days=OZON_ANALYTICS.week_days)
        expanded_end = min(today, end)
        current = await OzonEconomicsRepository(self.database).products(
            OzonProductsQuery(stores=stores, period_from=today, period_to=today),
            today - timedelta(days=1),
            True,
        )
        prices = table_for(OzonPriceDailyRecord)
        async with self.database.transaction() as connection:
            price_days = tuple(
                OzonAnalyticsPrice(
                    store_slug=row.store_slug,
                    article=row.offer_id,
                    product_id=str(row.product_id),
                    day=row.day,
                    values=stored_price_values(row.last_payload),
                )
                for row in await connection.execute(
                    select(prices)
                    .where(
                        prices.c.store_slug.in_(stores),
                        prices.c.day.between(expanded_start.isoformat(), expanded_end.isoformat()),
                    )
                    .order_by(prices.c.day)
                )
            )
        return OzonAnalyticsSourceBundle(
            catalog=await self.catalog(stores),
            current=current.products,
            turnover=await self._turnover_windows(
                OzonProductsQuery(stores=stores, period_from=expanded_start, period_to=expanded_end),
                OzonProductsQuery(stores=stores, period_from=stock_start, period_to=today),
            ),
            advertising=await AsyncAdvertisingRepository(self.database).overlapping(
                stores, AdvertisingPeriod(start=expanded_start, end=expanded_end)
            ),
            advertising_configured=frozenset(
                store for store in stores if tokens.has_performance_credentials(store)
            ),
            cabinets=await OzonCabinetRepository(self.database).settings(stores),
            buyouts=await self._payloads(
                table_for(OzonBuyoutDailyRecord), OzonBuyoutSnapshot, stores, expanded_start, expanded_end
            ),
            margins=await OzonMarginRepository(self.database).history(stores, expanded_start, expanded_end),
            prices=price_days,
            references=await self._payloads(
                table_for(OzonAnalyticsReferenceDailyRecord),
                OzonAnalyticsReference,
                stores,
                expanded_start,
                expanded_end,
            ),
            notes=await self.notes(stores, start, min(end, today), today) if include_notes else (),
            comments=await self.comments(stores, start - timedelta(weeks=OZON_ANALYTICS.history_weeks), end),
            stocks=await self._payloads(
                table_for(OzonAnalyticsStockSnapshotRecord),
                OzonAnalyticsStockSnapshot,
                stores,
                start,
                end,
                "week",
            ),
            funnel=await self._payloads(
                table_for(OzonAnalyticsFunnelDailyRecord),
                OzonAnalyticsFunnelDay,
                stores,
                expanded_start,
                expanded_end,
                "source_day",
            ),
            statuses=await self._payloads(
                table_for(OzonAnalyticsSourceStatusRecord), OzonAnalyticsSourceStatus, stores
            ),
        )

    def _complete_order_days(self, stores: tuple[str, ...], today: date) -> Select[tuple[str, str]]:
        scheme_days: list[Select[tuple[str, str]]] = []
        for scheme in OZON_ORDERS.schemes:
            native = ozon_order_days.alias()
            payload = (
                cast(native.c.payload, JSON)
                if self.database.dialect == DatabaseDialect.POSTGRESQL
                else type_coerce(native.c.payload, JSON)
            )
            native_days = select(native.c.store_slug, native.c.day).where(
                native.c.store_slug.in_(stores),
                native.c.scheme == scheme,
                native.c.day.between(OZON_ANALYTICS.first_sales_day.isoformat(), today.isoformat()),
                payload["listed"].as_boolean().is_(True),
                payload["ok"].as_boolean().is_(True),
                or_(payload["finalized"].as_boolean().is_(True), native.c.day == today.isoformat()),
            )
            legacy_days = select(economics_source_days.c.store_slug, economics_source_days.c.day).where(
                economics_source_days.c.marketplace == Marketplace.OZON,
                economics_source_days.c.store_slug.in_(stores),
                economics_source_days.c.source == OZON_ORDERS.coverage_source,
                economics_source_days.c.day.between(
                    OZON_ANALYTICS.first_sales_day.isoformat(), today.isoformat()
                ),
                ~select(native.c.day)
                .where(
                    native.c.store_slug == economics_source_days.c.store_slug,
                    native.c.day == economics_source_days.c.day,
                    native.c.scheme == scheme,
                )
                .exists(),
            )
            scheme_days.extend((native_days, legacy_days))
        candidates = union_all(*scheme_days).subquery()
        return (
            select(candidates.c.store_slug, candidates.c.day)
            .group_by(candidates.c.store_slug, candidates.c.day)
            .having(func.count() == len(OZON_ORDERS.schemes))
        )

    async def sales_sources(
        self, stores: tuple[str, ...], start: date, end: date, today: date
    ) -> OzonAnalyticsSalesSources:
        lines = table_for(SalesOrderLineRecord)
        order_day = func.substr(lines.c.ordered_at, 1, 10)
        saved_days = select(order_day.label("day")).where(
            lines.c.marketplace == Marketplace.OZON,
            lines.c.store_slug.in_(stores),
            order_day.between(OZON_ANALYTICS.first_sales_day.isoformat(), today.isoformat()),
        )
        complete_days = self._complete_order_days(stores, today).subquery()
        available = union_all(saved_days, select(complete_days.c.day)).subquery()
        async with self.database.transaction() as connection:
            bounds = (
                await connection.execute(
                    select(
                        func.min(available.c.day).label("first"),
                        func.max(available.c.day).label("last"),
                    )
                )
            ).first()
        return OzonAnalyticsSalesSources(
            catalog=await self.catalog(stores),
            turnover=await self._turnover_windows(
                OzonProductsQuery(stores=stores, period_from=start, period_to=end),
                OzonProductsQuery(stores=stores, period_from=today, period_to=today),
            ),
            first_day=date.fromisoformat(bounds.first) if bounds and bounds.first else None,
            last_day=date.fromisoformat(bounds.last) if bounds and bounds.last else None,
        )

    async def _turnover_windows(
        self, selected: OzonProductsQuery, current: OzonProductsQuery
    ) -> OzonTurnoverData:
        repository = AsyncTurnoverRepository(self.database)
        first = await repository.load(selected)
        second = (
            first
            if selected.period_from <= current.period_from and selected.period_to >= current.period_to
            else await repository.load(current)
        )
        keys = set(first.products) | set(second.products)
        stores = selected.stores
        complete_days = self._complete_order_days(stores, current.period_to).subquery()
        async with self.database.transaction() as connection:
            known = tuple(
                OzonConfirmedDay.model_validate(row)
                for row in (
                    await connection.execute(
                        select(complete_days).where(
                            or_(
                                complete_days.c.day.between(
                                    selected.period_from.isoformat(), selected.period_to.isoformat()
                                ),
                                complete_days.c.day.between(
                                    current.period_from.isoformat(), current.period_to.isoformat()
                                ),
                            )
                        )
                    )
                ).mappings()
            )
        eligible = {
            store: frozenset(item.day for item in known if item.store_slug == store) for store in stores
        }
        return OzonTurnoverData(
            products={
                key: tuple(
                    {
                        item.day: item
                        for item in (*first.products.get(key, ()), *second.products.get(key, ()))
                    }.values()
                )
                for key in keys
            },
            confirmed_days={
                store: (
                    first.confirmed_days.get(store, frozenset())
                    | second.confirmed_days.get(store, frozenset())
                )
                & eligible[store]
                for store in stores
            },
            eligible_days=eligible,
            stale_stores=first.stale_stores | second.stale_stores,
            updated_at={**first.updated_at, **second.updated_at},
        )

    async def notes(
        self, stores: tuple[str, ...], start: date, end: date, today: date
    ) -> tuple[OzonAnalyticsNote, ...]:
        table = table_for(RnpActionRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(*(column for column in table.c if column.name != "marketplace"))
                .where(
                    table.c.marketplace == Marketplace.OZON,
                    table.c.store_slug.in_(stores),
                    table.c.action_date.between(start.isoformat(), end.isoformat()),
                )
                .order_by(table.c.created_at, table.c.id)
            )
            notes = tuple(OzonAnalyticsNote.model_validate(row) for row in rows.mappings())
            revisions = table_for(RnpActionRevisionRecord)
            history = tuple(
                OzonAnalyticsNoteRevision.model_validate(row)
                for row in (
                    await connection.execute(
                        select(revisions)
                        .where(revisions.c.action_id.in_(tuple(item.id for item in notes)))
                        .order_by(revisions.c.edited_at, revisions.c.id)
                    )
                ).mappings()
            )
            return tuple(
                value.model_copy(
                    update={
                        "can_edit": value.action_date == today,
                        "revisions": (
                            entries := tuple(item for item in history if item.action_id == value.id)
                        ),
                        "updated_at": entries[-1].edited_at if entries else None,
                        "updated_by": entries[-1].user_name if entries else None,
                    }
                )
                for value in notes
            )

    async def note(self, note_id: int) -> OzonAnalyticsNote | None:
        table = table_for(RnpActionRecord)
        async with self.database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(*(column for column in table.c if column.name != "marketplace")).where(
                            table.c.id == note_id, table.c.marketplace == Marketplace.OZON
                        )
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        value = OzonAnalyticsNote.model_validate(row)
        revisions = await self.note_revisions(note_id)
        return value.model_copy(
            update={
                "revisions": revisions,
                "updated_at": revisions[-1].edited_at if revisions else None,
                "updated_by": revisions[-1].user_name if revisions else None,
            }
        )

    async def create_note(
        self, payload: OzonAnalyticsNoteCreate, author: OzonAnalyticsAuthor, now: datetime
    ) -> OzonAnalyticsNote:
        table = table_for(RnpActionRecord)
        async with self.database.transaction(write=True) as connection:
            await self._require_product(connection, payload.store, payload.article)
            if payload.day != self.clock().astimezone(TIME.moscow_timezone).date():
                raise OzonAnalyticsRequestError(
                    HTTPStatus.UNPROCESSABLE_ENTITY, OzonAnalyticsReason.CURRENT_DAY_ONLY
                )
            note_id = await connection.scalar(
                insert(table)
                .values(
                    store_slug=payload.store,
                    marketplace=Marketplace.OZON,
                    article=payload.article,
                    action_date=payload.day.isoformat(),
                    note=payload.note,
                    user_id=author.id,
                    user_name=author.name,
                    created_at=now.isoformat(),
                )
                .returning(table.c.id)
            )
            if note_id is None:
                raise RuntimeError(OzonAnalyticsReason.WRITE_FAILED)
        return OzonAnalyticsNote(
            id=note_id,
            store_slug=payload.store,
            article=payload.article,
            action_date=payload.day,
            note=payload.note,
            user_id=author.id,
            user_name=author.name,
            created_at=now,
            can_edit=True,
        )

    async def update_note(
        self,
        original: OzonAnalyticsNote,
        change: OzonAnalyticsNoteUpdate,
        author: OzonAnalyticsAuthor,
        now: datetime,
    ) -> OzonAnalyticsNote:
        table = table_for(RnpActionRecord)
        revision = OzonAnalyticsNoteRevision(
            id=uuid4().hex,
            action_id=original.id,
            previous_note=change.previous_note,
            note=change.note,
            user_id=author.id,
            user_name=author.name,
            edited_at=now,
        )
        async with self.database.transaction(write=True) as connection:
            await self._require_product(connection, original.store_slug, original.article)
            if original.action_date != self.clock().astimezone(TIME.moscow_timezone).date():
                raise OzonAnalyticsRequestError(
                    HTTPStatus.UNPROCESSABLE_ENTITY, OzonAnalyticsReason.CURRENT_DAY_ONLY
                )
            value = await connection.scalar(
                update(table)
                .where(
                    table.c.id == original.id,
                    table.c.marketplace == Marketplace.OZON,
                    table.c.note == change.previous_note,
                )
                .values(note=change.note)
                .returning(table.c.id)
            )
            if value is None:
                raise OzonAnalyticsConflict()
            await connection.execute(
                insert(table_for(RnpActionRevisionRecord)).values(**revision.model_dump(mode="json"))
            )
        return original.model_copy(
            update={
                "note": change.note,
                "can_edit": True,
                "revisions": original.revisions + (revision,),
                "updated_at": now,
                "updated_by": author.name,
            }
        )

    async def note_revisions(self, note_id: int) -> tuple[OzonAnalyticsNoteRevision, ...]:
        table = table_for(RnpActionRevisionRecord)
        async with self.database.transaction() as connection:
            return tuple(
                OzonAnalyticsNoteRevision.model_validate(row)
                for row in (
                    await connection.execute(
                        select(table)
                        .where(table.c.action_id == note_id)
                        .order_by(table.c.edited_at, table.c.id)
                    )
                ).mappings()
            )

    async def comments(
        self, stores: tuple[str, ...], start: date, end: date
    ) -> tuple[OzonAnalyticsComment, ...]:
        table = table_for(OzonAnalyticsCommentRecord)
        async with self.database.transaction() as connection:
            return tuple(
                OzonAnalyticsComment.model_validate(row)
                for row in (
                    await connection.execute(
                        select(table).where(
                            table.c.store_slug.in_(stores),
                            table.c.week.between(start.isoformat(), end.isoformat()),
                        )
                    )
                ).mappings()
            )

    @staticmethod
    def _comment_key(table: Table, key: OzonAnalyticsCommentKey) -> tuple[ColumnElement[bool], ...]:
        return (
            table.c.store_slug == key.store,
            table.c.article == key.article,
            table.c.week == key.week.isoformat(),
            table.c.kind == key.kind,
        )

    async def save_comment(
        self, change: OzonAnalyticsCommentChange, author: OzonAnalyticsAuthor, now: datetime
    ) -> OzonAnalyticsComment:
        table = table_for(OzonAnalyticsCommentRecord)
        values = {
            "store_slug": change.store,
            "article": change.article,
            "week": change.week.isoformat(),
            "kind": change.kind,
            "text": change.text,
            "version": change.version + 1,
            "author_id": author.id,
            "author": author.name,
            "updated_at": now.isoformat(),
        }
        async with self.database.transaction(write=True) as connection:
            await self._require_product(connection, change.store, change.article)
            local = self.clock().astimezone(TIME.moscow_timezone).date()
            if change.kind == OzonAnalyticsCommentKind.DECISION and change.week != local - timedelta(
                days=local.weekday()
            ):
                raise OzonAnalyticsRequestError(HTTPStatus.FORBIDDEN, OzonAnalyticsReason.CURRENT_WEEK_ONLY)
            if change.version == 0:
                inserted = await connection.scalar(
                    self._insert_ignore(
                        connection, table, values, ("store_slug", "article", "week", "kind")
                    ).returning(table.c.version)
                )
                previous = None
                if inserted is None:
                    raise OzonAnalyticsConflict()
            else:
                previous = await connection.scalar(
                    select(table.c.text)
                    .where(*self._comment_key(table, change), table.c.version == change.version)
                    .with_for_update()
                )
                if previous is None:
                    raise OzonAnalyticsConflict()
                changed = await connection.scalar(
                    update(table)
                    .where(*self._comment_key(table, change), table.c.version == change.version)
                    .values(**values)
                    .returning(table.c.version)
                )
                if changed is None:
                    raise OzonAnalyticsConflict()
            await connection.execute(
                insert(table_for(OzonAnalyticsCommentRevisionRecord)).values(**values, previous_text=previous)
            )
        return OzonAnalyticsComment.model_validate(values)

    async def comment_revisions(
        self, key: OzonAnalyticsCommentKey
    ) -> tuple[OzonAnalyticsCommentRevision, ...]:
        table = table_for(OzonAnalyticsCommentRevisionRecord)
        async with self.database.transaction() as connection:
            return tuple(
                OzonAnalyticsCommentRevision.model_validate(row)
                for row in (
                    await connection.execute(
                        select(table).where(*self._comment_key(table, key)).order_by(table.c.version.desc())
                    )
                ).mappings()
            )

    @staticmethod
    def _insert_ignore(
        connection: AsyncConnection, table: Table, values: dict[str, object], keys: tuple[str, ...]
    ) -> PostgresInsert | SqliteInsert:
        statement = (
            pg_insert(table)
            if connection.dialect.name == DatabaseDialect.POSTGRESQL
            else sqlite_insert(table)
        )
        return statement.values(**values).on_conflict_do_nothing(index_elements=list(keys))

    async def save_references(self, values: tuple[OzonAnalyticsReference, ...], now: datetime) -> None:
        today = now.astimezone(TIME.moscow_timezone).date()
        if any(
            value.day != today or value.captured_at.astimezone(TIME.moscow_timezone).date() != today
            for value in values
        ):
            raise ValueError(OzonAnalyticsReason.INVALID_PERIOD)
        table = table_for(OzonAnalyticsReferenceDailyRecord)
        async with self.database.transaction(write=True) as connection:
            for value in values:
                await connection.execute(
                    upsert(
                        connection,
                        table,
                        {
                            "store_slug": value.store_slug,
                            "article": value.article,
                            "day": value.day.isoformat(),
                            "payload": value.model_dump_json(),
                        },
                        ("store_slug", "article", "day"),
                    )
                )

    async def save_stock(self, snapshot: OzonAnalyticsStockSnapshot) -> None:
        table = table_for(OzonAnalyticsStockSnapshotRecord)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                self._insert_ignore(
                    connection,
                    table,
                    {
                        "store_slug": snapshot.store_slug,
                        "week": snapshot.week.isoformat(),
                        "payload": snapshot.model_dump_json(),
                    },
                    ("store_slug", "week"),
                )
            )

    async def capture_stocks(self, store: str, now: datetime) -> tuple[OzonAnalyticsStock, ...]:
        catalog = await self.catalog((store,))
        mp = table_for(MarketplaceStockRecord)
        ff = table_for(FulfillmentStockRecord)
        async with self.database.transaction() as connection:
            market = {
                row.article: OzonAnalyticsStock(article=row.article, fbo=row.fbo, fbs=row.fbs)
                for row in await connection.execute(
                    select(
                        mp.c.article,
                        func.sum(case((mp.c.scheme == StockScheme.FBO, mp.c.quantity))).label("fbo"),
                        func.sum(
                            case((mp.c.scheme.in_((StockScheme.FBS, StockScheme.RFBS)), mp.c.quantity))
                        ).label("fbs"),
                    )
                    .where(mp.c.store_slug == store, mp.c.marketplace == Marketplace.OZON)
                    .group_by(mp.c.article)
                )
            }
            fulfillment = {
                row.article: row.quantity
                for row in await connection.execute(
                    select(ff.c.article, func.sum(ff.c.quantity).label("quantity"))
                    .where(ff.c.store_slug == store, ff.c.marketplace == Marketplace.OZON)
                    .group_by(ff.c.article)
                )
            }
        transits = await self._payloads(table_for(OzonAnalyticsTransitRecord), OzonAnalyticsTransit, (store,))
        transit = {item.article: item.from_customer for value in transits for item in value.products}
        statuses = await self._payloads(
            table_for(OzonAnalyticsSourceStatusRecord), OzonAnalyticsSourceStatus, (store,)
        )
        transit_status = next(
            (item for item in statuses if item.source == OZON_ANALYTICS.transit_source), None
        )
        stale_transit = (
            any(
                (now - item.observed_at).total_seconds() > OZON_ANALYTICS.interval_seconds
                for item in transits
            )
            or transit_status is not None
            and transit_status.reason is not None
        )
        return tuple(
            market.get(item.article, OzonAnalyticsStock(article=item.article)).model_copy(
                update={
                    "ff": fulfillment.get(item.article),
                    "from_customer": transit.get(item.article),
                    "field_issues": (
                        OzonAnalyticsIssue(field="fromCustomer", reason=OzonAnalyticsReason.STALE),
                    )
                    if stale_transit and transit.get(item.article) is not None
                    else (),
                }
            )
            for item in catalog
        )

    async def save_transit(self, snapshot: OzonAnalyticsTransit) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                upsert(
                    connection,
                    table_for(OzonAnalyticsTransitRecord),
                    {"store_slug": snapshot.store_slug, "payload": snapshot.model_dump_json()},
                    ("store_slug",),
                )
            )
            await self._save_status(
                connection,
                OzonAnalyticsSourceStatus(
                    store_slug=snapshot.store_slug,
                    source=OZON_ANALYTICS.transit_source,
                    attempted_at=snapshot.observed_at,
                ),
            )

    @staticmethod
    async def _save_status(connection: AsyncConnection, status: OzonAnalyticsSourceStatus) -> None:
        await connection.execute(
            upsert(
                connection,
                table_for(OzonAnalyticsSourceStatusRecord),
                {
                    "store_slug": status.store_slug,
                    "source": status.source,
                    "source_day": status.source_day.isoformat() if status.source_day else "",
                    "payload": status.model_dump_json(),
                },
                ("store_slug", "source", "source_day"),
            )
        )

    async def save_status(self, status: OzonAnalyticsSourceStatus) -> None:
        async with self.database.transaction(write=True) as connection:
            await self._save_status(connection, status)

    async def _lock_account(self, connection: AsyncConnection, account: str) -> None:
        table = table_for(OzonAnalyticsAccountRecord)
        await connection.execute(
            self._insert_ignore(connection, table, {"account": account, "sequence": 0}, ("account",))
        )
        await connection.execute(
            update(table).where(table.c.account == account).values(sequence=table.c.sequence + 1)
        )

    @staticmethod
    async def _require_lease(connection: AsyncConnection, run: OzonAnalyticsFunnelRun, now: datetime) -> None:
        table = table_for(OzonAnalyticsAccountRecord)
        valid = await connection.scalar(
            select(table.c.account).where(
                table.c.account == run.account,
                table.c.lease_token == run.lease_token,
                table.c.lease_until > now.isoformat(),
            )
        )
        if valid is None:
            raise OzonAnalyticsConflict()

    async def reserve_attempt(self, run: OzonAnalyticsFunnelRun, now: datetime) -> OzonAnalyticsReservation:
        account = run.account
        table = table_for(OzonAnalyticsAttemptRecord)
        accounts = table_for(OzonAnalyticsAccountRecord)
        async with self.database.transaction(write=True) as connection:
            await self._lock_account(connection, account)
            await self._require_lease(connection, run, now)
            state = (await connection.execute(select(accounts).where(accounts.c.account == account))).first()
            if state and state.next_retry_at and datetime.fromisoformat(state.next_retry_at) > now:
                return OzonAnalyticsReservation(
                    allowed=False,
                    next_retry_at=datetime.fromisoformat(state.next_retry_at),
                    reason=OzonAnalyticsReason(state.reason) if state.reason else None,
                )
            attempts = tuple(
                await connection.scalars(
                    select(table.c.attempted_at)
                    .where(
                        table.c.account == account,
                        table.c.attempted_at
                        > (now - timedelta(seconds=OZON_ANALYTICS.budget_window_seconds)).isoformat(),
                    )
                    .order_by(table.c.attempted_at)
                )
            )
            if len(attempts) >= OZON_ANALYTICS.daily_attempt_budget:
                return OzonAnalyticsReservation(
                    allowed=False,
                    reason=OzonAnalyticsReason.QUOTA_DEFERRED,
                    next_retry_at=datetime.fromisoformat(attempts[0])
                    + timedelta(seconds=OZON_ANALYTICS.budget_window_seconds),
                )
            runs = table_for(OzonAnalyticsFunnelRunRecord)
            count = await connection.scalar(
                update(runs)
                .where(
                    runs.c.store_slug == run.store_slug,
                    runs.c.source_day == run.source_day.isoformat(),
                    runs.c.generation == run.generation,
                    runs.c.offset == run.offset,
                    runs.c.lease_token == run.lease_token,
                    runs.c.page_attempts < OZON_ANALYTICS.page_attempts,
                )
                .values(page_attempts=runs.c.page_attempts + 1)
                .returning(runs.c.page_attempts)
            )
            if count is None:
                raise OzonAnalyticsConflict()
            await connection.execute(
                insert(table).values(id=uuid4().hex, account=account, attempted_at=now.isoformat())
            )
        return OzonAnalyticsReservation(allowed=True, page_attempts=count)

    async def acquire_account(self, account: str, token: str, now: datetime) -> bool:
        table = table_for(OzonAnalyticsAccountRecord)
        async with self.database.transaction(write=True) as connection:
            await self._lock_account(connection, account)
            changed = await connection.scalar(
                update(table)
                .where(
                    table.c.account == account,
                    or_(table.c.lease_until.is_(None), table.c.lease_until <= now.isoformat()),
                )
                .values(
                    lease_token=token,
                    lease_until=(
                        now
                        + timedelta(
                            seconds=OZON_ANALYTICS.run_timeout_seconds + OZON_ANALYTICS.lease_grace_seconds
                        )
                    ).isoformat(),
                )
                .returning(table.c.account)
            )
        return changed is not None

    async def release_account(self, account: str, token: str) -> None:
        table = table_for(OzonAnalyticsAccountRecord)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                update(table)
                .where(table.c.account == account, table.c.lease_token == token)
                .values(lease_token=None, lease_until=None)
            )

    async def runs(self, store: str) -> tuple[OzonAnalyticsFunnelRun, ...]:
        table = table_for(OzonAnalyticsFunnelRunRecord)
        async with self.database.transaction() as connection:
            return tuple(
                OzonAnalyticsFunnelRun.model_validate(row)
                for row in (
                    await connection.execute(select(table).where(table.c.store_slug == store))
                ).mappings()
            )

    async def last_queue(self, account: str) -> str | None:
        table = table_for(OzonAnalyticsAccountRecord)
        async with self.database.transaction() as connection:
            value: str | None = await connection.scalar(
                select(table.c.last_queue).where(table.c.account == account)
            )
            return value

    async def start_run(self, value: OzonAnalyticsFunnelRun) -> None:
        table = table_for(OzonAnalyticsFunnelRunRecord)
        async with self.database.transaction(write=True) as connection:
            await self._lock_account(connection, value.account)
            await self._require_lease(connection, value, value.updated_at)
            previous = await connection.scalar(
                select(table.c.generation).where(
                    table.c.store_slug == value.store_slug, table.c.source_day == value.source_day.isoformat()
                )
            )
            if previous and previous != value.generation:
                staging = table_for(OzonAnalyticsFunnelStagingRecord)
                await connection.execute(delete(staging).where(staging.c.generation == previous))
            await connection.execute(
                upsert(connection, table, value.model_dump(mode="json"), ("store_slug", "source_day"))
            )
            accounts = table_for(OzonAnalyticsAccountRecord)
            await connection.execute(
                update(accounts).where(accounts.c.account == value.account).values(last_queue=value.queue)
            )

    async def checkpoint(
        self, run: OzonAnalyticsFunnelRun, page: OzonAnalyticsFunnelPage, now: datetime
    ) -> OzonAnalyticsFunnelRun:
        runs = table_for(OzonAnalyticsFunnelRunRecord)
        staging = table_for(OzonAnalyticsFunnelStagingRecord)
        complete = page.rows < OZON_ANALYTICS.page_size
        result = run.model_copy(
            update={
                "offset": run.offset + page.rows,
                "page_attempts": 0,
                "state": OzonAnalyticsRunState.COMPLETE if complete else OzonAnalyticsRunState.RUNNING,
                "updated_at": now,
                "reason": None,
                "next_retry_at": None,
            }
        )
        async with self.database.transaction(write=True) as connection:
            await self._lock_account(connection, run.account)
            await self._require_lease(connection, run, now)
            stored_row = (
                (
                    await connection.execute(
                        select(runs).where(
                            runs.c.store_slug == run.store_slug,
                            runs.c.source_day == run.source_day.isoformat(),
                            runs.c.generation == run.generation,
                            runs.c.lease_token == run.lease_token,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if stored_row is None:
                raise OzonAnalyticsConflict()
            stored = OzonAnalyticsFunnelRun.model_validate(stored_row)
            if stored.state == OzonAnalyticsRunState.COMPLETE or stored.offset != run.offset:
                if stored.offset != run.offset + page.rows:
                    raise OzonAnalyticsConflict()
                if stored.state == OzonAnalyticsRunState.COMPLETE:
                    daily = table_for(OzonAnalyticsFunnelDailyRecord)
                    payload = await connection.scalar(
                        select(daily.c.payload).where(
                            daily.c.store_slug == run.store_slug,
                            daily.c.source_day == run.source_day.isoformat(),
                        )
                    )
                    committed = (
                        OzonAnalyticsFunnelDay.model_validate_json(payload).products if payload else ()
                    )
                else:
                    committed = tuple(
                        OzonAnalyticsFunnelValue.model_validate(row)
                        for row in (
                            await connection.execute(
                                select(staging.c.sku, staging.c.article, staging.c.carts).where(
                                    staging.c.generation == run.generation
                                )
                            )
                        ).mappings()
                    )
                by_sku = {item.sku: item for item in committed}
                if any(by_sku.get(item.sku) != item for item in page.items):
                    raise OzonAnalyticsConflict()
                return stored
            changed = await connection.scalar(
                update(runs)
                .where(
                    runs.c.store_slug == run.store_slug,
                    runs.c.source_day == run.source_day.isoformat(),
                    runs.c.generation == run.generation,
                    runs.c.offset == run.offset,
                    runs.c.lease_token == run.lease_token,
                )
                .values(**result.model_dump(mode="json"))
                .returning(runs.c.offset)
            )
            if changed is None:
                raise OzonAnalyticsConflict()
            existing = frozenset(
                await connection.scalars(select(staging.c.sku).where(staging.c.generation == run.generation))
            )
            if any(item.sku in existing for item in page.items):
                raise OzonAnalyticsApiError(OzonAnalyticsReason.REPEATED_PAGE)
            if run.offset + page.rows > OZON_ANALYTICS.max_source_rows:
                raise OzonAnalyticsApiError(OzonAnalyticsReason.MALFORMED_SOURCE)
            if page.items:
                await connection.execute(
                    insert(staging),
                    [{"generation": run.generation, **item.model_dump()} for item in page.items],
                )
            await self._save_status(
                connection,
                OzonAnalyticsSourceStatus(
                    store_slug=run.store_slug,
                    source=OZON_ANALYTICS.funnel_source,
                    source_day=run.source_day,
                    attempted_at=now,
                ),
            )
            await self._save_status(
                connection,
                OzonAnalyticsSourceStatus(
                    store_slug=run.store_slug, source=OZON_ANALYTICS.funnel_source, attempted_at=now
                ),
            )
            if complete:
                products = tuple(
                    OzonAnalyticsFunnelValue.model_validate(row)
                    for row in (
                        await connection.execute(
                            select(staging.c.sku, staging.c.article, staging.c.carts).where(
                                staging.c.generation == run.generation
                            )
                        )
                    ).mappings()
                )
                snapshot = OzonAnalyticsFunnelDay(
                    store_slug=run.store_slug,
                    source_day=run.source_day,
                    observed_at=now,
                    products=products,
                    source_timestamp=page.source_timestamp,
                )
                await connection.execute(
                    upsert(
                        connection,
                        table_for(OzonAnalyticsFunnelDailyRecord),
                        {
                            "store_slug": run.store_slug,
                            "source_day": run.source_day.isoformat(),
                            "payload": snapshot.model_dump_json(),
                        },
                        ("store_slug", "source_day"),
                    )
                )
                await self._save_status(
                    connection,
                    OzonAnalyticsSourceStatus(
                        store_slug=run.store_slug, source=OZON_ANALYTICS.funnel_source, attempted_at=now
                    ),
                )
                accounts = table_for(OzonAnalyticsAccountRecord)
                await connection.execute(
                    update(accounts)
                    .where(accounts.c.account == run.account)
                    .values(next_retry_at=None, reason=None)
                )
                await connection.execute(delete(staging).where(staging.c.generation == run.generation))
        return result

    async def defer_run(
        self,
        run: OzonAnalyticsFunnelRun,
        reason: OzonAnalyticsReason,
        retry_at: datetime,
        now: datetime,
        *,
        failed: bool = False,
        account_wide: bool = False,
        raw_payload: str | None = None,
    ) -> None:
        table = table_for(OzonAnalyticsFunnelRunRecord)
        async with self.database.transaction(write=True) as connection:
            await self._lock_account(connection, run.account)
            await self._require_lease(connection, run, now)
            changed = await connection.scalar(
                update(table)
                .where(
                    table.c.store_slug == run.store_slug,
                    table.c.source_day == run.source_day.isoformat(),
                    table.c.generation == run.generation,
                    table.c.lease_token == run.lease_token,
                )
                .values(
                    state=OzonAnalyticsRunState.FAILED if failed else OzonAnalyticsRunState.DEFERRED,
                    reason=reason,
                    next_retry_at=retry_at.isoformat(),
                    updated_at=now.isoformat(),
                    page_attempts=run.page_attempts,
                    raw_payload=raw_payload or run.raw_payload,
                )
                .returning(table.c.generation)
            )
            if changed is None:
                raise OzonAnalyticsConflict()
            await self._save_status(
                connection,
                OzonAnalyticsSourceStatus(
                    store_slug=run.store_slug,
                    source=OZON_ANALYTICS.funnel_source,
                    source_day=run.source_day,
                    attempted_at=now,
                    reason=reason,
                    next_retry_at=retry_at,
                ),
            )
            if account_wide:
                await self._save_status(
                    connection,
                    OzonAnalyticsSourceStatus(
                        store_slug=run.store_slug,
                        source=OZON_ANALYTICS.funnel_source,
                        attempted_at=now,
                        reason=reason,
                        next_retry_at=retry_at,
                    ),
                )
                accounts = table_for(OzonAnalyticsAccountRecord)
                await connection.execute(
                    update(accounts)
                    .where(accounts.c.account == run.account)
                    .values(next_retry_at=retry_at.isoformat(), reason=reason)
                )
