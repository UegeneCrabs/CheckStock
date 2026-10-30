import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta

from app.application.ports import OzonReportsStore
from app.core.constants import OZON_SCENARIO, TIME, OzonReportGroup
from app.dto.ozon_reports import (
    OzonFilterResult,
    OzonProfitQuery,
    OzonProfitReport,
    OzonReportArticle,
    OzonReportCategories,
    OzonReportFilters,
    OzonReportIdentity,
    OzonReportPagination,
    OzonTargetChange,
    OzonTargetPreview,
    OzonTargetRecord,
    OzonTargetReport,
    OzonTargetSave,
)
from app.dto.ozon_tariffs import OzonProductCategorySelection
from app.economics.ozon_reports import categories, filters, profit_row, selected, totals
from app.economics.ozon_target_prices import target_row

logger = logging.getLogger(__name__)


class OzonReportsService:
    def __init__(
        self,
        repository: OzonReportsStore,
        clock: Callable[[], datetime],
        category_reader: Callable[
            [str, tuple[str, ...]], Awaitable[tuple[OzonProductCategorySelection, ...]]
        ],
    ):
        self.repository = repository
        self.clock = clock
        self.category_reader = category_reader

    async def _categories(self, identities: tuple[OzonReportIdentity, ...]) -> OzonReportCategories:
        subjects: dict[tuple[str, str], str] = {}
        unavailable: set[str] = set()

        async def load(store: str) -> None:
            articles = tuple(item.article for item in identities if item.store_slug == store)
            try:
                products = await self.category_reader(store, articles)
                subjects.update({(store, item.article): item.type_name for item in products})
            except Exception as error:
                logger.warning(
                    "ozon_report_categories_unavailable store=%s error=%s", store, type(error).__name__
                )
                unavailable.add(store)
            if any(not subjects.get((store, article)) for article in articles):
                unavailable.add(store)

        async with asyncio.TaskGroup() as group:
            for store in sorted({item.store_slug for item in identities}):
                group.create_task(load(store))
        return OzonReportCategories(
            identities=tuple(
                item.model_copy(update={"subject": subjects.get((item.store_slug, item.article))})
                for item in identities
            ),
            unavailable_stores=tuple(sorted(unavailable)),
        )

    async def filter_options(self, stores: tuple[str, ...]) -> OzonFilterResult:
        items = await self.repository.catalog(stores) if stores else ()
        category_data = await self._categories(
            tuple(OzonReportIdentity(store_slug=item.store_slug, article=item.article) for item in items)
        )
        return OzonFilterResult(
            category_unavailable_stores=category_data.unavailable_stores,
            filters=OzonReportFilters(
                subjects=tuple(sorted({item.subject or "" for item in category_data.identities})),
                managers=tuple(sorted({item.manager for item in items if item.manager})),
                articles=tuple(
                    OzonReportArticle(
                        article=item.article,
                        name=item.name,
                        store_slug=item.store_slug,
                        store_name=item.store_name,
                        image_url=item.image_url,
                    )
                    for item in items
                ),
            ),
        )

    async def profit(
        self, query: OzonProfitQuery, stores: tuple[str, ...], *, export: bool = False
    ) -> OzonProfitReport:
        if not stores:
            return OzonProfitReport(
                period_from=query.date_from,
                period_to=query.date_to,
                rows=(),
                totals=totals(()),
                filters=OzonReportFilters(),
            )
        evidence = await self.repository.evidence(query, stores)
        category_data = await self._categories(evidence.identities)
        evidence = replace(evidence, identities=category_data.identities)
        today = self.clock().astimezone(TIME.moscow_timezone).date()
        rows = tuple(
            profit_row(product, evidence)
            for product in evidence.products
            if (product.store_slug, product.article) in evidence.stocked
            or (product.economics_7d.turnover or 0) > 0
            or any(
                (day.orders or 0) > 0 or (day.advertising_spend or 0) != 0
                for day in product.economics_7d.daily
            )
            or (query.date_from <= today <= query.date_to and (product.stock.total or 0) > 0)
        )
        options = filters(rows)
        rows = tuple(row for row in rows if selected(row, query))
        rows = tuple(sorted(rows, key=lambda row: row.net_orders_amount or 0, reverse=True))
        grouped = categories(rows)
        view = grouped if query.group_by == OzonReportGroup.SUBJECT else rows
        paged = query.daily_details and query.group_by == OzonReportGroup.PRODUCT and not export
        pages = max((len(view) + query.page_size - 1) // query.page_size, 1) if paged else 1
        page = min(query.page, pages) if paged else 1
        shown = view[(page - 1) * query.page_size : page * query.page_size] if paged else view
        if not query.daily_details and not export:
            shown = tuple(row.model_copy(update={"daily_calculations": ()}) for row in shown)
        return OzonProfitReport(
            period_from=query.date_from,
            period_to=query.date_to,
            rows=rows if export else shown,
            category_rows=grouped if export else (),
            totals=totals(rows),
            filters=options,
            category_unavailable_stores=category_data.unavailable_stores,
            group_by=query.group_by,
            daily_details=query.daily_details or export,
            pagination=OzonReportPagination(
                enabled=paged, page=page, page_size=query.page_size, total_count=len(view), total_pages=pages
            ),
        )

    async def targets(
        self, stores: tuple[str, ...], article: str = "", preview: OzonTargetPreview | None = None
    ) -> OzonTargetReport:
        today = self.clock().astimezone(TIME.moscow_timezone).date()
        end = today - timedelta(days=1)
        query = OzonProfitQuery(date_from=today - timedelta(days=OZON_SCENARIO.advertising_days), date_to=end)
        if not stores:
            return OzonTargetReport(period_from=query.date_from, period_to=end, rows=())
        evidence = await self.repository.evidence(query, stores)
        saved = await self.repository.targets(stores)
        products = tuple(
            product
            for product in evidence.products
            if (not article or product.article == article)
            and (
                (product.stock.total or 0) > 0
                or (product.stock.inbound or 0) > 0
                or (product.economics_7d.turnover or 0) > 0
            )
        )
        rows = tuple(
            target_row(product, saved.get((product.store_slug, product.article), OzonTargetRecord()), preview)
            for product in products
        )
        return OzonTargetReport(
            period_from=query.date_from,
            period_to=end,
            rows=tuple(sorted(rows, key=lambda row: row.orders_amount or 0, reverse=True)),
        )

    async def save_target(
        self, store: str, change: OzonTargetChange, user_id: int, user_name: str
    ) -> OzonTargetRecord:
        if not await self.repository.has_product(store, change.article):
            raise LookupError(change.article)
        return await self.repository.save_target(
            OzonTargetSave(
                store_slug=store, change=change, user_id=user_id, user_name=user_name, saved_at=self.clock()
            )
        )
