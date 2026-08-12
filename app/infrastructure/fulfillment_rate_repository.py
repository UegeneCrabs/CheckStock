from types import TracebackType

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.dto.unit_economics import (
    FulfillmentNames,
    PersistedFulfillmentRate,
    PersistedFulfillmentRates,
    SaveFulfillmentRatesCommand,
)
from app.infrastructure.orm import FulfillmentRecord, FulfillmentUnitRateRecord


class SqlAlchemyFulfillmentRateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def fulfillment_names(self) -> FulfillmentNames:
        names = self._session.scalars(select(FulfillmentRecord.name).order_by(FulfillmentRecord.id))
        return FulfillmentNames(tuple(names))

    def rates(self) -> PersistedFulfillmentRates:
        rows = self._session.execute(
            select(FulfillmentRecord.name, FulfillmentUnitRateRecord)
            .outerjoin(
                FulfillmentUnitRateRecord,
                FulfillmentUnitRateRecord.fulfillment == FulfillmentRecord.name,
            )
            .order_by(FulfillmentRecord.id)
        )
        return PersistedFulfillmentRates(
            tuple(
                PersistedFulfillmentRate(
                    name=name,
                    storage_per_m3_day=rate.storage_per_m3_day if rate else None,
                    acceptance_per_unit=rate.acceptance_per_unit if rate else None,
                    fulfillment_per_unit=rate.fulfillment_per_unit if rate else None,
                    updated_at=rate.updated_at if rate else None,
                )
                for name, rate in rows
            )
        )

    def save(self, command: SaveFulfillmentRatesCommand) -> None:
        for item in command.rates.root:
            record = self._session.get(FulfillmentUnitRateRecord, item.name)
            if record is None:
                record = FulfillmentUnitRateRecord(fulfillment=item.name)
                self._session.add(record)
                self._session.flush()
            record.storage_per_m3_day = item.storage
            record.acceptance_per_unit = item.accept
            record.fulfillment_per_unit = item.fulfillment
            record.updated_at = command.updated_at.isoformat()


class SqlAlchemyFulfillmentRateUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self.repository: SqlAlchemyFulfillmentRateRepository

    def __enter__(self) -> "SqlAlchemyFulfillmentRateUnitOfWork":
        self._session = self._session_factory()
        self.repository = SqlAlchemyFulfillmentRateRepository(self._session)
        return self

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("UnitOfWork is not active")
        self._session.commit()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if self._session is None:
            return None
        try:
            if exc_type is not None:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None
        return None
