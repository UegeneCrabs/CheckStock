from collections import Counter
from collections.abc import Callable
from datetime import datetime

from app.application.ports import SupplyStockUnitOfWork
from app.dto.identity import User
from app.dto.supply_arrivals import (
    SupplyArrivalId,
    SupplyArrivalRow,
    SupplyArrivalsReport,
    SupplyArrivalsStockReport,
    SupplyStockChange,
    SupplyStockCommand,
    SupplyStockState,
)
from app.stock.supply_arrival_identity import arrival_id


class SupplyArrivalStockService:
    def __init__(
        self, repository: SupplyStockUnitOfWork, source_key: str, clock: Callable[[], datetime]
    ) -> None:
        self._repository = repository
        self._source_key = source_key
        self._clock = clock

    async def report(self, report: SupplyArrivalsReport, user: User) -> SupplyArrivalsStockReport:
        states = {state.supply_id: state for state in await self._repository.states(self._source_key)}
        identities = Counter(arrival_id(self._source_key, row) for row in report.rows)
        rows = []
        for row in report.rows:
            identity = arrival_id(self._source_key, row)
            rows.append(
                SupplyArrivalRow(
                    **row.model_dump(),
                    supply_id=identity,
                    stock=states.get(identity, SupplyStockState(supply_id=identity)),
                    stock_editable=user.is_active and identities[identity] == 1,
                )
            )
        return SupplyArrivalsStockReport(
            **report.model_dump(exclude={"rows"}), rows=tuple(rows), can_edit_stock=user.is_active
        )

    async def change(
        self, supply_id: SupplyArrivalId, change: SupplyStockChange, user: User
    ) -> SupplyStockState:
        if not user.is_active:
            raise PermissionError
        return await self._repository.save(
            SupplyStockCommand(
                source_key=self._source_key,
                supply_id=supply_id,
                change=change,
                user_id=user.id,
                user_name=user.full_name,
                occurred_at=self._clock(),
            )
        )
