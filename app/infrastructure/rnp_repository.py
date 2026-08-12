from datetime import datetime
from types import TracebackType

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.dto.rnp import (
    AddRnpActionCommand,
    RnpAction,
    RnpArticleExists,
    RnpArticleQuery,
    RnpStrategy,
    SaveRnpStrategyCommand,
)
from app.infrastructure.orm import RnpActionRecord, RnpStrategyRecord, StockItemRecord


class SqlAlchemyRnpRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def article_exists(self, query: RnpArticleQuery) -> RnpArticleExists:
        article_id = self._session.scalar(
            select(StockItemRecord.id).where(
                StockItemRecord.store_slug == query.store_slug,
                StockItemRecord.marketplace == query.marketplace.value,
                StockItemRecord.article == query.article,
                StockItemRecord.is_service == 0,
            )
        )
        return RnpArticleExists(article_id is not None)

    def save_strategy(self, command: SaveRnpStrategyCommand) -> RnpStrategy:
        request = command.request
        key = (request.store.lower(), request.marketplace.value, request.article)
        record = self._session.get(RnpStrategyRecord, key)
        if record is None:
            record = RnpStrategyRecord(
                store_slug=key[0],
                marketplace=key[1],
                article=key[2],
                strategy=request.strategy,
                date_from=request.date_from.isoformat(),
                date_to=request.date_to.isoformat(),
                updated_by=command.updated_by,
                updated_at=command.updated_at.isoformat(),
            )
            self._session.add(record)
        else:
            record.strategy = request.strategy
            record.date_from = request.date_from.isoformat()
            record.date_to = request.date_to.isoformat()
            record.updated_by = command.updated_by
            record.updated_at = command.updated_at.isoformat()
        self._session.flush()
        return RnpStrategy(
            store_slug=record.store_slug,
            marketplace=record.marketplace,
            article=record.article,
            strategy=record.strategy,
            date_from=record.date_from,
            date_to=record.date_to,
            updated_by=record.updated_by,
            updated_at=datetime.fromisoformat(record.updated_at),
        )

    def add_action(self, command: AddRnpActionCommand) -> RnpAction:
        request = command.request
        record = RnpActionRecord(
            store_slug=request.store.lower(),
            marketplace=request.marketplace.value,
            article=request.article,
            action_date=request.action_date.isoformat(),
            note=request.note,
            user_id=command.user_id,
            user_name=command.user_name,
            created_at=command.created_at.isoformat(),
        )
        self._session.add(record)
        self._session.flush()
        return RnpAction(
            id=record.id,
            article=record.article,
            action_date=record.action_date,
            note=record.note,
            user_name=record.user_name,
            created_at=datetime.fromisoformat(record.created_at),
        )


class SqlAlchemyRnpUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self.repository: SqlAlchemyRnpRepository

    def __enter__(self) -> "SqlAlchemyRnpUnitOfWork":
        self._session = self._session_factory()
        self.repository = SqlAlchemyRnpRepository(self._session)
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
