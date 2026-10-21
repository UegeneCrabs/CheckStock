"""Publish a validated WB snapshot and its sources in one transaction."""

from app.application.ports import WbStandardCostsUnitOfWorkFactory
from app.dto.wb_standard_costs import PublishSnapshot


class WbStandardCostsService:
    def __init__(self, unit_of_work_factory: WbStandardCostsUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def publish(self, command: PublishSnapshot) -> None:
        with self._unit_of_work_factory() as uow:
            uow.repository.save(command)
            uow.commit()
