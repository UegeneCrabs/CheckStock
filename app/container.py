from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.access.security import Pbkdf2PasswordService
from app.application.identity import IdentityService
from app.application.stock import StockMovementService
from app.config import settings
from app.infrastructure.database import database_for_path
from app.infrastructure.health import DatabaseHealthService
from app.infrastructure.identity_repository import SqlAlchemyIdentityUnitOfWork
from app.infrastructure.stock_repository import SqlAlchemyStockUnitOfWork
from app.repositories import core
from app.stock.inbound_supplies import build_service as build_inbound_service


class ApplicationContainer:
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

    def _identity_unit_of_work(self) -> SqlAlchemyIdentityUnitOfWork:
        database = database_for_path(self._database_path())
        return SqlAlchemyIdentityUnitOfWork(database.session_factory)

    def _stock_unit_of_work(self) -> SqlAlchemyStockUnitOfWork:
        database = database_for_path(self._database_path())
        return SqlAlchemyStockUnitOfWork(database.session_factory)
