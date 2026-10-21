from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from app.access.security import Pbkdf2PasswordService
from app.application.finance import FinanceService
from app.application.identity import IdentityService
from app.application.stock import StockMovementService
from app.application.supply_arrival_stock import SupplyArrivalStockService
from app.application.wb_standard_costs import WbStandardCostsService
from app.config import settings
from app.dto.wb_hourly_prices import WbSellerPrice, WbStorefrontResult
from app.economics.wb.prices import calculate_wallet_price
from app.infrastructure.database import database_for_path
from app.infrastructure.finance_repository import FinanceRepository
from app.infrastructure.health import DatabaseHealthService
from app.infrastructure.identity_repository import SqlAlchemyIdentityUnitOfWork
from app.infrastructure.stock_repository import SqlAlchemyStockUnitOfWork
from app.infrastructure.supply_arrival_stock_repository import SupplyArrivalStockRepository
from app.infrastructure.wb_hourly_price_repository import (
    SqlAlchemyDailyPriceCloser,
    SqlAlchemyHourlyPriceWriter,
)
from app.infrastructure.wb_hourly_price_repository import catalog_items as wb_catalog_items
from app.infrastructure.wb_standard_costs_repository import SqlAlchemyWbStandardCostsUnitOfWork
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
    from app.infrastructure.async_resources import AsyncResources


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


class ApplicationContainer:
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
