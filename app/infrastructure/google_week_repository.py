import asyncio
import json
from datetime import date, datetime

from sqlalchemy import JSON, cast, func, select, text, type_coerce
from sqlalchemy.engine import ScalarResult

from app.core.constants import (
    GOOGLE_WEEK,
    OZON_ORDERS,
    TIME,
    DatabaseDialect,
    Marketplace,
    WeekAction,
    WeekIssue,
)
from app.dto.google_week import (
    DailyFact,
    OzonWeekOrder,
    Product,
    SheetOption,
    StoreFacts,
    WeekActionState,
    WeekCatalog,
    WeekProblem,
    WeekProfile,
    WeekReport,
    WeekSettings,
    document_id,
)
from app.dto.orders import OzonOrderDayState
from app.infrastructure.async_database import AsyncDatabase, upsert
from app.infrastructure.google_week_orm import google_week_marketplaces
from app.infrastructure.orm import SalesOrderLineRecord


class GoogleWeekRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def get(self, marketplace: Marketplace) -> WeekProfile:
        async with self.database.transaction() as connection:
            payload = (
                await connection.execute(
                    select(google_week_marketplaces.c.payload).where(
                        google_week_marketplaces.c.marketplace == marketplace
                    )
                )
            ).scalar_one_or_none()
            if payload:
                return WeekProfile.model_validate_json(payload)
            profile = WeekProfile(
                marketplace=marketplace,
                settings=WeekSettings(
                    stock_sheet_name=GOOGLE_WEEK.default_stock_sheets[list(Marketplace).index(marketplace)]
                ),
            )
            if marketplace != Marketplace.WB:
                return profile
            original = (
                (await connection.execute(text("SELECT * FROM google_week_update_settings WHERE id=1")))
                .mappings()
                .first()
            )
            if original is None:
                return profile
            profile.saved_at = datetime.fromisoformat(original["updated_at"])
            profile.settings = WeekSettings(
                spreadsheet_url=original["spreadsheet_url"],
                sheet_name=original["sheet_name"],
                cells=json.loads(original["cells"]),
                weekday=original["weekday"],
                run_time=original["run_time"],
            )
            tables = {
                WeekAction.NUMBER: "google_week_update_settings",
                WeekAction.SEARCH: "google_week_search_state",
                WeekAction.SALES: "google_week_sales_state",
                WeekAction.STOCK: "google_week_stock_state",
                WeekAction.FREEZE: "google_week_freeze_state",
            }
            enabled = set()
            for action, table in tables.items():
                row = (await connection.execute(text(f"SELECT * FROM {table} WHERE id=1"))).mappings().first()
                if row is None:
                    continue
                if row["enabled"]:
                    enabled.add(action)
                report = (
                    WeekReport.model_validate_json(row["result_json"]) if row.get("result_json") else None
                )
                if action == WeekAction.NUMBER and row["last_value"]:
                    report = WeekReport(value=row["last_value"], processed_at=row["last_success_at"])
                profile.states[action] = WeekActionState(
                    activated_at=profile.saved_at,
                    last_attempt_at=row["last_attempt_at"],
                    last_success_at=row["last_success_at"],
                    last_success_slot=row["last_success_slot"],
                    last_error=WeekProblem(code=WeekIssue.LEGACY, detail=row["last_error"])
                    if row["last_error"]
                    else None,
                    result=report,
                )
                if action == WeekAction.STOCK:
                    profile.settings.stock_sheet_name = row["sheet_name"]
                if action == WeekAction.FREEZE and row["spreadsheet_id"] == document_id(
                    profile.settings.spreadsheet_url
                ):
                    profile.settings.freeze_sheet_ids = tuple(sorted(json.loads(row["sheet_ids"])))
                    profile.settings.selection_initialized = bool(row["selection_initialized"])
            profile.settings.enabled_actions = frozenset(enabled)
            catalog = (
                (
                    await connection.execute(
                        text("SELECT * FROM google_sheet_catalog WHERE spreadsheet_id=:id"),
                        {"id": document_id(profile.settings.spreadsheet_url)},
                    )
                )
                .mappings()
                .first()
            )
            if catalog:
                profile.catalog = WeekCatalog(
                    sheets=[SheetOption.model_validate(item) for item in json.loads(catalog["sheets_json"])],
                    updated_at=catalog["updated_at"],
                    last_attempt_at=catalog["last_attempt_at"],
                    last_error=WeekProblem(code=WeekIssue.LEGACY, detail=catalog["last_error"])
                    if catalog["last_error"]
                    else None,
                )
            return profile

    async def save(self, profile: WeekProfile) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                upsert(
                    connection,
                    google_week_marketplaces,
                    {"marketplace": profile.marketplace.value, "payload": profile.model_dump_json()},
                    ("marketplace",),
                )
            )

    async def products(self, marketplace: Marketplace) -> list[Product]:
        async with self.database.transaction() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT store_slug, article FROM stock_items WHERE marketplace=:marketplace AND is_service=0"
                    ),
                    {"marketplace": marketplace.value},
                )
            ).mappings()
            products = {(row["store_slug"], row["article"]): Product.model_validate(row) for row in rows}
            aliases = (
                await connection.execute(
                    text(
                        "SELECT store_slug, article, target_article FROM catalog_article_aliases WHERE marketplace=:marketplace"
                    ),
                    {"marketplace": marketplace.value},
                )
            ).mappings()
            for alias in aliases:
                product = products.get((alias["store_slug"], alias["target_article"]))
                if product:
                    product.aliases.append(alias["article"])
            return list(products.values())

    async def facts(
        self, marketplace: Marketplace, stores: set[str], start: date, end: date, *, stock: bool
    ) -> dict[str, StoreFacts]:
        results: dict[str, StoreFacts] = {}

        async def load(store: str) -> None:
            try:
                results[store] = await self._store_facts(marketplace, store, start, end, stock=stock)
            except Exception as error:
                results[store] = StoreFacts(
                    error=WeekProblem(code=WeekIssue.SOURCE, detail=type(error).__name__)
                )

        async with asyncio.TaskGroup() as group:
            for store in sorted(stores):
                group.create_task(load(store))
        return results

    async def _store_facts(
        self, marketplace: Marketplace, store: str, start: date, end: date, *, stock: bool
    ) -> StoreFacts:
        if marketplace == Marketplace.OZON and not stock:
            return await self._ozon_sales_facts(store, start, end)
        params: dict[str, str | int] = {
            "marketplace": marketplace.value,
            "store": store,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        if stock:
            query = """SELECT store_slug, article, day, quantity, captured_at, 0 AS confirmed
                FROM marketplace_stock_daily_history WHERE marketplace=:marketplace AND store_slug=:store
                AND scheme='fbo' AND day BETWEEN :start AND :end"""
        elif marketplace == Marketplace.WB:
            params.update(raw=GOOGLE_WEEK.funnel_raw_version, complete=GOOGLE_WEEK.funnel_complete_version)
            query = """SELECT store_slug, article, day,
                CASE WHEN source_version>=:raw THEN orders_count-cancel_count ELSE NULL END AS quantity,
                NULL AS captured_at, CASE WHEN source_version>=:complete THEN 1 ELSE 0 END AS confirmed
                FROM wb_funnel_daily_orders WHERE store_slug=:store AND day BETWEEN :start AND :end
                UNION ALL SELECT store_slug, NULL, day, 0, NULL, 1 FROM economics_source_days
                WHERE marketplace=:marketplace AND store_slug=:store AND source='orders' AND day BETWEEN :start AND :end"""
        else:
            query = """SELECT store_slug, article, substr(ordered_at,1,10) AS day,
                SUM(quantity-cancelled_quantity) AS quantity, NULL AS captured_at, 0 AS confirmed
                FROM sales_order_lines WHERE marketplace=:marketplace AND store_slug=:store
                AND substr(ordered_at,1,10) BETWEEN :start AND :end
                GROUP BY store_slug, article, substr(ordered_at,1,10)
                UNION ALL SELECT store_slug, NULL, day, 0, NULL, 1 FROM unit_economics_yandex_loaded_days
                WHERE store_slug=:store AND source='orders' AND day BETWEEN :start AND :end"""
        result = StoreFacts()
        async with self.database.transaction() as connection:
            rows = (await connection.execute(text(query), params)).mappings()
            for row in rows:
                fact = DailyFact.model_validate(row)
                if row["confirmed"]:
                    result.confirmed.add(fact.day)
                if fact.article is not None:
                    result.facts.append(fact)
        return result

    async def _ozon_sales_facts(self, store: str, start: date, end: date) -> StoreFacts:
        table = SalesOrderLineRecord.__table__
        payload = (
            cast(table.c.raw_json, JSON)
            if self.database.dialect == DatabaseDialect.POSTGRESQL
            else type_coerce(table.c.raw_json, JSON)
        )
        query = select(
            table.c.article,
            table.c.quantity,
            table.c.ordered_at,
            payload["in_process_at"].as_string().label("accepted_at"),
        ).where(
            table.c.marketplace == Marketplace.OZON,
            table.c.store_slug == store,
            table.c.scheme.in_(OZON_ORDERS.schemes),
            func.lower(func.trim(func.coalesce(table.c.status, ""))) != GOOGLE_WEEK.ozon_cancelled_status,
        )
        quantities: dict[tuple[str, date], int] = {}
        source_days: dict[date, set[date]] = {}
        params = {"marketplace": Marketplace.OZON.value, "store": store}
        async with self.database.transaction() as connection:
            confirmed_rows: ScalarResult[str] = (
                await connection.execute(
                    text("""SELECT day FROM economics_source_days
                        WHERE marketplace=:marketplace AND store_slug=:store AND source='orders'"""),
                    params,
                )
            ).scalars()
            confirmed = {date.fromisoformat(value) for value in confirmed_rows}
            day_payloads: ScalarResult[str] = (
                await connection.execute(
                    text("SELECT payload FROM ozon_order_days WHERE store_slug=:store"), params
                )
            ).scalars()
            days: dict[date, dict[str, OzonOrderDayState]] = {}
            for day_payload in day_payloads:
                state = OzonOrderDayState.model_validate_json(day_payload)
                days.setdefault(state.day, {})[state.scheme] = state
            for day, states in days.items():
                if all(
                    scheme in states
                    and states[scheme].listed
                    and states[scheme].finalized
                    and states[scheme].ok
                    for scheme in OZON_ORDERS.schemes
                ):
                    confirmed.add(day)
                else:
                    confirmed.discard(day)
            async with connection.stream(query) as rows:
                async for row in rows.mappings():
                    order = OzonWeekOrder.model_validate(row)
                    day = order.accepted_at.astimezone(TIME.moscow_timezone).date()
                    if start <= day <= end:
                        key = (order.article, day)
                        quantities[key] = quantities.get(key, 0) + order.quantity
                        source_days.setdefault(day, set()).add(
                            order.ordered_at.astimezone(TIME.moscow_timezone).date()
                        )
        return StoreFacts(
            facts=[
                DailyFact(store_slug=store, article=article, day=day, quantity=quantity)
                for (article, day), quantity in sorted(quantities.items())
            ],
            confirmed={
                day for day in confirmed if start <= day <= end and source_days.get(day, set()) <= confirmed
            },
        )
