from collections.abc import Callable
from datetime import datetime

from app.application.ports import RnpUnitOfWorkFactory
from app.dto.identity import User
from app.dto.rnp import (
    AddRnpActionCommand,
    RnpAction,
    RnpActionRequest,
    RnpArticleQuery,
    RnpStrategy,
    RnpStrategyRequest,
    SaveRnpStrategyCommand,
)


class RnpCommandService:
    def __init__(
        self,
        unit_of_work_factory: RnpUnitOfWorkFactory,
        clock: Callable[[], datetime],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    def save_strategy(self, request: RnpStrategyRequest, user: User) -> RnpStrategy:
        query = RnpArticleQuery(
            store_slug=request.store.lower(),
            marketplace=request.marketplace,
            article=request.article,
        )
        with self._unit_of_work_factory() as unit_of_work:
            if not unit_of_work.repository.article_exists(query).root:
                raise ValueError("Товар не найден")
            strategy = unit_of_work.repository.save_strategy(
                SaveRnpStrategyCommand(
                    request=request,
                    updated_by=user.full_name or "Пользователь",
                    updated_at=self._clock(),
                )
            )
            unit_of_work.commit()
            return strategy

    def add_action(self, request: RnpActionRequest, user: User) -> RnpAction:
        if request.action_date > self._clock().date():
            raise ValueError("Нельзя записать действие будущей датой")
        query = RnpArticleQuery(
            store_slug=request.store.lower(),
            marketplace=request.marketplace,
            article=request.article,
        )
        with self._unit_of_work_factory() as unit_of_work:
            if not unit_of_work.repository.article_exists(query).root:
                raise ValueError("Товар не найден")
            action = unit_of_work.repository.add_action(
                AddRnpActionCommand(
                    request=request,
                    user_id=user.id,
                    user_name=user.full_name or "Пользователь",
                    created_at=self._clock(),
                )
            )
            unit_of_work.commit()
            return action
