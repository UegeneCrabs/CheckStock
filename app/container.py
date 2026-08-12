from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.application.decision_commands import DecisionCommandService
from app.application.identity import IdentityService
from app.application.rnp_commands import RnpCommandService
from app.application.stock import StockMovementService
from app.application.unit_economics import UnitEconomicsConfigurationService
from app.config import settings
from app.domain import MOSCOW_TIMEZONE
from app.infrastructure.database import database_for_path
from app.infrastructure.decision_repository import SqlAlchemyDecisionUnitOfWork
from app.infrastructure.fulfillment_rate_repository import (
    SqlAlchemyFulfillmentRateUnitOfWork,
)
from app.infrastructure.health import DatabaseHealthService
from app.infrastructure.identity_repository import SqlAlchemyIdentityUnitOfWork
from app.infrastructure.rnp_repository import SqlAlchemyRnpUnitOfWork
from app.infrastructure.stock_repository import SqlAlchemyStockUnitOfWork
from app.repositories import core
from app.security import Pbkdf2PasswordService


class ApplicationContainer:
    def __init__(self, database_path: Callable[[], Path] | None = None) -> None:
        self._database_path = database_path or (lambda: core.DB_PATH)
        self.passwords = Pbkdf2PasswordService()
        self.health = DatabaseHealthService(self._database_path)
        self.identity = IdentityService(
            unit_of_work_factory=self._identity_unit_of_work,
            password_service=self.passwords,
            session_ttl=timedelta(days=settings.session_ttl_days),
        )
        self.rnp_commands = RnpCommandService(
            unit_of_work_factory=self._rnp_unit_of_work,
            clock=lambda: datetime.now(MOSCOW_TIMEZONE),
        )
        self.decision_commands = DecisionCommandService(
            unit_of_work_factory=self._decision_unit_of_work,
            clock=lambda: datetime.now(UTC),
        )
        self.stock = StockMovementService(
            unit_of_work_factory=self._stock_unit_of_work,
            clock=lambda: datetime.now(UTC),
        )
        self.unit_economics = UnitEconomicsConfigurationService(
            unit_of_work_factory=self._fulfillment_rate_unit_of_work,
            clock=lambda: datetime.now(UTC),
        )

    def _identity_unit_of_work(self) -> SqlAlchemyIdentityUnitOfWork:
        database = database_for_path(self._database_path())
        return SqlAlchemyIdentityUnitOfWork(database.session_factory)

    def _rnp_unit_of_work(self) -> SqlAlchemyRnpUnitOfWork:
        database = database_for_path(self._database_path())
        return SqlAlchemyRnpUnitOfWork(database.session_factory)

    def _decision_unit_of_work(self) -> SqlAlchemyDecisionUnitOfWork:
        database = database_for_path(self._database_path())
        return SqlAlchemyDecisionUnitOfWork(database.session_factory)

    def _stock_unit_of_work(self) -> SqlAlchemyStockUnitOfWork:
        database = database_for_path(self._database_path())
        return SqlAlchemyStockUnitOfWork(database.session_factory)

    def _fulfillment_rate_unit_of_work(self) -> SqlAlchemyFulfillmentRateUnitOfWork:
        database = database_for_path(self._database_path())
        return SqlAlchemyFulfillmentRateUnitOfWork(database.session_factory)
