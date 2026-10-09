from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.access.security import Pbkdf2PasswordService
from app.application.finance import FinanceService
from app.application.identity import IdentityService
from app.application.stock import StockMovementService
from app.config import settings
from app.economics.wb.prices import calculate_wallet_price
from app.infrastructure.database import database_for_path
from app.infrastructure.finance_repository import FinanceRepository
from app.infrastructure.health import DatabaseHealthService
from app.infrastructure.identity_repository import SqlAlchemyIdentityUnitOfWork
from app.infrastructure.stock_repository import SqlAlchemyStockUnitOfWork
from app.infrastructure.wb_hourly_price_repository import SqlAlchemyHourlyPriceWriter
from app.infrastructure.wb_hourly_price_repository import catalog_items as wb_catalog_items
from app.repositories import core
from app.stock.inbound_supplies import build_service as build_inbound_service
from app.wb import api as wb_api
from app.wb.hourly_prices import WbHourlyPriceService


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
        self.wb_hourly_prices = WbHourlyPriceService(
            catalog_items=wb_catalog_items,
            storefront_products=wb_api.get_storefront_products,
            wallet_discount=wb_api.get_default_wallet_discount_percent,
            wallet_price=calculate_wallet_price,
            writer=SqlAlchemyHourlyPriceWriter(self._database_path),
        )

    def _identity_unit_of_work(self) -> SqlAlchemyIdentityUnitOfWork:
        database = database_for_path(self._database_path())
        return SqlAlchemyIdentityUnitOfWork(database.session_factory)

    def _stock_unit_of_work(self) -> SqlAlchemyStockUnitOfWork:
        database = database_for_path(self._database_path())
        return SqlAlchemyStockUnitOfWork(database.session_factory)
