from datetime import datetime
from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from app.dto.decision import DecisionAction, SetDecisionStatusCommand
from app.infrastructure.orm import DecisionActionRecord


class SqlAlchemyDecisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def set_status(self, command: SetDecisionStatusCommand) -> DecisionAction:
        request = command.request
        record = self._session.get(DecisionActionRecord, request.fingerprint)
        if record is None:
            record = DecisionActionRecord(
                fingerprint=request.fingerprint,
                status=request.status.value,
                user_id=command.user_id,
                user_name=command.user_name,
                updated_at=command.updated_at.isoformat(),
            )
            self._session.add(record)
        else:
            record.status = request.status.value
            record.user_id = command.user_id
            record.user_name = command.user_name
            record.updated_at = command.updated_at.isoformat()
        self._session.flush()
        return DecisionAction(
            fingerprint=record.fingerprint,
            status=record.status,
            user_id=record.user_id,
            user_name=record.user_name,
            updated_at=datetime.fromisoformat(record.updated_at),
        )


class SqlAlchemyDecisionUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self.repository: SqlAlchemyDecisionRepository

    def __enter__(self) -> "SqlAlchemyDecisionUnitOfWork":
        self._session = self._session_factory()
        self.repository = SqlAlchemyDecisionRepository(self._session)
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
