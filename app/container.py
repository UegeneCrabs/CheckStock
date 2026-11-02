from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from app.access.security import Pbkdf2PasswordService
from app.application.database_backup import DatabaseBackupService
from app.application.fbo_stock import FboStockService
from app.application.finance import FinanceService
from app.application.identity import IdentityService
from app.application.ozon_analytics import OzonAnalyticsService
from app.application.ozon_cabinet import OzonCabinetService
from app.application.ozon_calendar import OzonCalendarService
from app.application.ozon_google_export import OzonGoogleExport
from app.application.ozon_history import OzonHistoryService
from app.application.ozon_limits import OzonLimitsService
from app.application.ozon_logistics import OzonLogisticsService
from app.application.ozon_margin_service import OzonMarginService
from app.application.ozon_price_changes import OzonPriceChangeService
from app.application.ozon_promotion_entry import OzonPromotionEntryService
from app.application.ozon_promotions import OzonPromotionsService
from app.application.ozon_related import OzonRelatedService
from app.application.ozon_reports import OzonReportsService
from app.application.ozon_storage import OzonStorageService
from app.application.ozon_tariffs import OzonTariffService
from app.application.spp_history import SppHistoryService
from app.application.stock import StockMovementService
from app.application.supply_arrival_stock import SupplyArrivalStockService
from app.application.wb_product_rating import WbProductRatingService
from app.application.wb_standard_costs import WbStandardCostsService
from app.application.yandex_analytics import YandexAnalyticsService
from app.config import settings
from app.core.constants import OZON_PRICES, OZON_PROMOTIONS, TIME, OzonPriceSource, OzonPromotionStatus
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.orders import OrderPeriod
from app.dto.ozon_buyout import OzonBuyoutEvidence
from app.dto.ozon_economics import OzonProductsQuery
from app.dto.ozon_logistics import ReturnsFilter
from app.dto.ozon_price_changes import OzonPriceChangeBusyError, OzonPriceChangeEvidence
from app.dto.ozon_prices import PriceIdentity, PriceValues, WebsiteEvidence
from app.dto.ozon_promotion_entry import OzonEntryBusyError, OzonEntryEvidence, OzonEntryRefreshedPrices
from app.dto.ozon_related import OzonModelProducts
from app.dto.ozon_tariffs import OzonCategorySelection, OzonProductCategorySelection
from app.dto.wb_hourly_prices import WbSellerPrice, WbStorefrontResult
from app.economics.ozon_commissions import snapshot as commission_snapshot
from app.economics.wb.prices import calculate_wallet_price
from app.infrastructure.async_logistics_repository import AsyncLogisticsRepository
from app.infrastructure.async_storage_repository import AsyncStorageRepository
from app.infrastructure.async_turnover_repository import AsyncTurnoverRepository
from app.infrastructure.database import database_for_path
from app.infrastructure.fbo_stock_repository import FboStockRepository
from app.infrastructure.finance_repository import FinanceRepository
from app.infrastructure.health import DatabaseHealthService
from app.infrastructure.identity_repository import SqlAlchemyIdentityUnitOfWork
from app.infrastructure.ozon_analytics_repository import OzonAnalyticsRepository
from app.infrastructure.ozon_cabinet_repository import OzonCabinetRepository
from app.infrastructure.ozon_calendar_repository import OzonCalendarRepository
from app.infrastructure.ozon_history_repository import OzonHistoryRepository
from app.infrastructure.ozon_limits_repository import OzonLimitsRepository
from app.infrastructure.ozon_margin_repository import OzonMarginRepository
from app.infrastructure.ozon_price_changes_repository import OzonPriceChangeRepository
from app.infrastructure.ozon_promotion_entry_repository import OzonPromotionEntryRepository
from app.infrastructure.ozon_reports_repository import OzonReportsRepository
from app.infrastructure.ozon_tariff_catalog import CATALOG
from app.infrastructure.spp_history_repository import SqlAlchemySppHistoryRepository
from app.infrastructure.stock_repository import SqlAlchemyStockUnitOfWork
from app.infrastructure.supply_arrival_stock_repository import SupplyArrivalStockRepository
from app.infrastructure.wb_hourly_price_repository import (
    SqlAlchemyDailyPriceCloser,
    SqlAlchemyHourlyPriceWriter,
)
from app.infrastructure.wb_hourly_price_repository import catalog_items as wb_catalog_items
from app.infrastructure.wb_product_rating_repository import SqlAlchemyWbRatingWriter
from app.infrastructure.wb_standard_costs_repository import SqlAlchemyWbStandardCostsUnitOfWork
from app.infrastructure.yandex_analytics_repository import YandexAnalyticsRepository
from app.jobs.async_locks import hold
from app.jobs.locks import SyncJobBusyError
from app.orders.ozon_async import sync_configured_store
from app.ozon.async_logistics import AsyncLogisticsClient
from app.ozon.async_price_changes import AsyncPriceChangeClient
from app.ozon.async_promotions import AsyncPromotionsClient
from app.ozon.async_storage import AsyncStorageClient
from app.ozon.price_normalizer import fbo_commission_observation, seller_observation, website_observation
from app.ozon.tokens import get_order_credentials
from app.repositories import core
from app.stock.inbound_supplies import build_service as build_inbound_service
from app.stock.supply_arrivals import source_key as supply_arrivals_source_key
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens
from app.wb.hourly_prices import (
    WbHourlyPriceService,
    seller_prices_from_payload,
    storefront_result_from_payload,
)

if TYPE_CHECKING:
    from app.infrastructure.async_database import AsyncDatabase
    from app.infrastructure.async_resources import AsyncResources


def ozon_limits_service(database: "AsyncDatabase") -> OzonLimitsService:
    return OzonLimitsService(OzonLimitsRepository(database))


def ozon_analytics_service(database: "AsyncDatabase") -> OzonAnalyticsService:
    return OzonAnalyticsService(OzonAnalyticsRepository(database))


def ozon_reports_service(resources: "AsyncResources") -> OzonReportsService:
    async def categories(store: str, articles: tuple[str, ...]) -> tuple[OzonProductCategorySelection, ...]:
        return await resources.ozon_categories.products(get_order_credentials(store), articles)

    return OzonReportsService(
        OzonReportsRepository(resources.database), lambda: datetime.now(UTC), categories
    )


def ozon_margin_service(resources: "AsyncResources") -> OzonMarginService:
    return OzonMarginService(OzonMarginRepository(resources.database))


def fbo_stock_service(resources: "AsyncResources") -> FboStockService:
    return FboStockService(FboStockRepository(resources.database))


def _wb_storefront_products(nm_ids: tuple[str, ...]) -> WbStorefrontResult:
    return storefront_result_from_payload(wb_api.get_storefront_products(nm_ids))


def _wb_seller_prices(store_slug: str, nm_ids: tuple[str, ...]) -> tuple[WbSellerPrice, ...]:
    if not nm_ids:
        return ()
    token = wb_tokens.get_token(store_slug)
    goods: list[dict] = []
    for start in range(0, len(nm_ids), wb_api.PRICES_PAGE_LIMIT):
        batch = [int(nm_id) for nm_id in nm_ids[start : start + wb_api.PRICES_PAGE_LIMIT]]
        goods.extend(wb_api.get_goods_prices_by_nm_ids(token, batch))
    return seller_prices_from_payload(goods)


def wb_product_rating_service(database_path: Callable[[], Path] | None = None) -> WbProductRatingService:
    return WbProductRatingService(SqlAlchemyWbRatingWriter(database_path or (lambda: core.DB_PATH)))


class ApplicationContainer:
    @property
    def spp_history(self) -> SppHistoryService:
        return SppHistoryService(
            SqlAlchemySppHistoryRepository(database_for_path(self._database_path()).session_factory)
        )

    @property
    def finance(self) -> FinanceService:
        return FinanceService(FinanceRepository(database_for_path(self._database_path()).session_factory))

    def __init__(self, database_path: Callable[[], Path] | None = None) -> None:
        self._database_path = database_path or (lambda: core.DB_PATH)
        self.inbound_supplies = build_inbound_service(self._database_path)
        self.passwords = Pbkdf2PasswordService()
        self.health = DatabaseHealthService(self._database_path)
        self.identity = IdentityService(
            unit_of_work_factory=self._identity_unit_of_work,
            password_service=self.passwords,
            session_ttl=timedelta(days=settings.session_ttl_days),
        )
        self.stock = StockMovementService(
            unit_of_work_factory=self._stock_unit_of_work,
            clock=lambda: datetime.now(UTC),
        )
        self.wb_standard_costs = WbStandardCostsService(self._wb_standard_costs_unit_of_work)
        self.wb_hourly_prices = WbHourlyPriceService(
            catalog_items=wb_catalog_items,
            storefront_products=_wb_storefront_products,
            seller_prices=_wb_seller_prices,
            wallet_discount=wb_api.get_default_wallet_discount_percent,
            wallet_price=calculate_wallet_price,
            writer=SqlAlchemyHourlyPriceWriter(self._database_path),
        )
        self.wb_daily_price_closer = SqlAlchemyDailyPriceCloser(self._database_path)
        self.wb_product_rating = wb_product_rating_service(self._database_path)
        self.yandex_analytics = YandexAnalyticsService(
            YandexAnalyticsRepository(), lambda: datetime.now(MOSCOW_TIMEZONE)
        )

    def _identity_unit_of_work(self) -> SqlAlchemyIdentityUnitOfWork:
        database = database_for_path(self._database_path())
        return SqlAlchemyIdentityUnitOfWork(database.session_factory)

    def _stock_unit_of_work(self) -> SqlAlchemyStockUnitOfWork:
        database = database_for_path(self._database_path())
        return SqlAlchemyStockUnitOfWork(database.session_factory)

    def _wb_standard_costs_unit_of_work(self) -> SqlAlchemyWbStandardCostsUnitOfWork:
        database = database_for_path(self._database_path())
        return SqlAlchemyWbStandardCostsUnitOfWork(database.session_factory)


def supply_arrival_stock_service(resources: "AsyncResources") -> SupplyArrivalStockService:
    return SupplyArrivalStockService(
        SupplyArrivalStockRepository(resources.database),
        supply_arrivals_source_key(),
        lambda: datetime.now(UTC),
    )


def ozon_logistics_service(resources: "AsyncResources", store: str) -> OzonLogisticsService:
    return OzonLogisticsService(
        store,
        AsyncLogisticsRepository(resources.database),
        AsyncLogisticsClient(resources.http, get_order_credentials(store)).evidence,
        lambda: datetime.now(UTC),
    )


def ozon_storage_service(resources: "AsyncResources", store: str) -> OzonStorageService:
    return OzonStorageService(
        store,
        AsyncStorageRepository(resources.database),
        AsyncStorageClient(resources.http, get_order_credentials(store)),
        lambda: datetime.now(UTC),
    )


def ozon_history_service(resources: "AsyncResources") -> OzonHistoryService:
    return OzonHistoryService(OzonHistoryRepository(resources.database))


def ozon_calendar_service(resources: "AsyncResources") -> OzonCalendarService:
    return OzonCalendarService(OzonCalendarRepository(resources.database), lambda: datetime.now(UTC))


def ozon_related_service(resources: "AsyncResources", store: str) -> OzonRelatedService:
    async def models(articles: tuple[str, ...]) -> OzonModelProducts:
        return await resources.ozon_related.load(get_order_credentials(store), articles)

    return OzonRelatedService(lambda: resources.catalog.ozon_products(store), models)


def ozon_promotions_service(resources: "AsyncResources", store: str) -> OzonPromotionsService:
    credentials = get_order_credentials(store)
    scope = sha256(f"{store}:{credentials.client_id}:{credentials.api_key}".encode()).hexdigest()
    return OzonPromotionsService(
        AsyncPromotionsClient(resources.http, credentials),
        lambda: resources.catalog.ozon_products(store),
        resources.ozon_promotions_cache,
        scope,
        lambda: datetime.now(UTC),
    )


def ozon_promotion_entry_service(resources: "AsyncResources", store: str) -> OzonPromotionEntryService:
    @asynccontextmanager
    async def lock(article: str) -> AsyncIterator[None]:
        try:
            async with hold(OZON_PROMOTIONS.entry_lock_prefix + store + ":" + article, resources.database):
                yield
        except SyncJobBusyError:
            raise OzonEntryBusyError() from None

    async def refresh(evidence: OzonEntryEvidence) -> OzonEntryRefreshedPrices:
        now = datetime.now(UTC)
        seller = seller_observation(evidence.prices, now)
        websites = tuple(
            WebsiteEvidence(
                product_id=evidence.identity.id,
                action_id=item.action.id,
                source=OzonPriceSource.PRODUCTS
                if item.status == OzonPromotionStatus.PARTICIPATING
                else OzonPriceSource.CANDIDATES,
                received_at=now,
                prices=item.product.website_prices.prices_by_schema[scheme.value.upper()],
                scheme=scheme,
            )
            for item in evidence.promotions.items
            if item.product and item.product.website_prices
            for scheme in OZON_PRICES.website_schemes
            if scheme.value.upper() in item.product.website_prices.prices_by_schema
        )
        website = website_observation(
            PriceIdentity(product_id=evidence.identity.id, offer_id=evidence.identity.offer_id), websites, now
        )
        commission = fbo_commission_observation(evidence.prices, now)
        await resources.prices.save(store, (seller, website))
        await resources.commissions.save(store, (commission,))
        values = PriceValues.model_validate(
            {change.field.value: change.value for change in (*seller.changes, *website.changes)}
        )
        return OzonEntryRefreshedPrices(
            prices=values, commission=commission_snapshot(commission.value, values.seller_price, now)
        )

    return OzonPromotionEntryService(
        OzonPromotionEntryRepository(resources.database),
        AsyncPromotionsClient(resources.http, get_order_credentials(store)),
        ozon_promotions_service(resources, store),
        OzonCabinetRepository(resources.database).boost_settings,
        ozon_tariff_service(resources, store).calculate,
        lock,
        refresh,
        lambda: datetime.now(UTC),
    )


def ozon_price_change_service(resources: "AsyncResources", store: str) -> OzonPriceChangeService:
    from app.economics.ozon_price_changes import observations, price_values

    @asynccontextmanager
    async def lock(article: str) -> AsyncIterator[None]:
        try:
            async with hold(OZON_PROMOTIONS.entry_lock_prefix + store + ":" + article, resources.database):
                yield
        except SyncJobBusyError:
            raise OzonPriceChangeBusyError() from None

    async def refresh(evidence: OzonPriceChangeEvidence) -> PriceValues:
        async with hold(OZON_PRICES.lock_prefix + store, resources.database, wait=True):
            await resources.prices.close_days(store, evidence.received_at)
            await resources.prices.save(store, observations(evidence))
            await resources.commissions.save(
                store, (fbo_commission_observation(evidence.prices, evidence.received_at),)
            )
        return price_values(evidence)

    return OzonPriceChangeService(
        OzonPriceChangeRepository(resources.database),
        AsyncPriceChangeClient(resources.http, get_order_credentials(store)),
        ozon_promotions_service(resources, store),
        lock,
        refresh,
        lambda: datetime.now(UTC),
    )


def ozon_tariff_service(resources: "AsyncResources", store: str = "") -> OzonTariffService:
    async def category(article: str) -> OzonCategorySelection:
        return await resources.ozon_categories.product(get_order_credentials(store), article)

    return OzonTariffService(CATALOG, category, lambda: datetime.now(TIME.moscow_timezone).date())


def ozon_google_export_service(resources: "AsyncResources") -> OzonGoogleExport:
    return OzonGoogleExport(resources, OzonAnalyticsRepository(resources.database))


def database_backup_service(resources: "AsyncResources") -> DatabaseBackupService:
    from app.infrastructure.database_backup_store import DatabaseBackupStore
    from app.integrations.database_backup_drive import DatabaseBackupDrive

    return DatabaseBackupService(
        DatabaseBackupStore(settings), DatabaseBackupDrive(resources.client, settings)
    )


def ozon_cabinet_service(resources: "AsyncResources") -> OzonCabinetService:
    async def load_orders(query: OzonProductsQuery) -> OzonBuyoutEvidence:
        result = await sync_configured_store(
            query.stores[0],
            OrderPeriod(
                start=query.period_from,
                end=query.period_to + timedelta(days=1),
            ),
            resources,
            wait_for_lock=True,
            retry_incomplete_days=True,
        )
        if result is None or result.errors:
            raise ValueError("Order refresh unavailable")
        credentials = get_order_credentials(query.stores[0])
        lines = await OzonCabinetRepository(resources.database).buyout_lines(query)
        returns = await AsyncLogisticsClient(resources.http, credentials).returns(
            tuple(dict.fromkeys(line.order_key for line in lines)), ReturnsFilter(return_schema=None)
        )
        return OzonBuyoutEvidence(
            turnover=await AsyncTurnoverRepository(resources.database).load(query),
            lines=lines,
            returns=returns,
        )

    return OzonCabinetService(
        OzonCabinetRepository(resources.database),
        load_orders,
        AsyncLogisticsRepository(resources.database).articles,
        lambda: datetime.now(UTC),
    )
