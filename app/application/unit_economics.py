from collections.abc import Callable
from datetime import datetime

from app.application.ports import FulfillmentRateUnitOfWorkFactory
from app.dto.stores import StoreCollection
from app.dto.unit_economics import (
    FulfillmentRate,
    FulfillmentRatesRequest,
    FulfillmentRatesResult,
    SaveFulfillmentRatesCommand,
    UnitEconomicsConfiguration,
)
from app.errors import UnitEconomicsValidationError
from app.wb.unit_economics_config import build_configuration


class UnitEconomicsConfigurationService:
    def __init__(
        self,
        unit_of_work_factory: FulfillmentRateUnitOfWorkFactory,
        clock: Callable[[], datetime],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    def configuration(self, stores: StoreCollection) -> UnitEconomicsConfiguration:
        with self._unit_of_work_factory() as unit_of_work:
            rates = unit_of_work.repository.rates()
        return build_configuration(stores, rates)

    def save_rates(self, request: FulfillmentRatesRequest) -> FulfillmentRatesResult:
        with self._unit_of_work_factory() as unit_of_work:
            known_names = unit_of_work.repository.fulfillment_names().root
            submitted_names = tuple(rate.name for rate in request.rates.root)
            if len(set(submitted_names)) != len(submitted_names):
                raise UnitEconomicsValidationError("Фулфилмент передан дважды")
            unknown = set(submitted_names) - set(known_names)
            if unknown:
                raise UnitEconomicsValidationError("Неизвестный фулфилмент: " + ", ".join(sorted(unknown)))
            if set(submitted_names) != set(known_names):
                raise UnitEconomicsValidationError("В таблице должны присутствовать все фулфилменты")
            updated_at = self._clock()
            unit_of_work.repository.save(
                SaveFulfillmentRatesCommand(rates=request.rates, updated_at=updated_at)
            )
            unit_of_work.commit()
            return FulfillmentRatesResult(
                rates=tuple(
                    FulfillmentRate(
                        name=rate.name,
                        storage=rate.storage,
                        accept=rate.accept,
                        fulfillment=rate.fulfillment,
                    )
                    for rate in request.rates.root
                ),
                updated_at=updated_at,
            )
