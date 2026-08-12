from collections.abc import Callable
from datetime import datetime

from app.application.ports import DecisionUnitOfWorkFactory
from app.dto.decision import DecisionAction, DecisionStatusRequest, SetDecisionStatusCommand
from app.dto.identity import User


class DecisionCommandService:
    def __init__(
        self,
        unit_of_work_factory: DecisionUnitOfWorkFactory,
        clock: Callable[[], datetime],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    def set_status(self, request: DecisionStatusRequest, user: User) -> DecisionAction:
        with self._unit_of_work_factory() as unit_of_work:
            result = unit_of_work.repository.set_status(
                SetDecisionStatusCommand(
                    request=request,
                    user_id=user.id,
                    user_name=user.full_name,
                    updated_at=self._clock(),
                )
            )
            unit_of_work.commit()
            return result
