"""Persist snapshot JSON using the caller's SQLAlchemy transaction."""

import json

from sqlalchemy.orm import Session, sessionmaker

from app.dto.wb_standard_costs import PublishSnapshot
from app.infrastructure.database import DatabaseConnection, UnitOfWork


class SqlAlchemyWbStandardCostsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, command: PublishSnapshot) -> None:
        connection = self._session.connection()
        conn = DatabaseConnection(connection, connection.dialect.name)
        conn.execute(
            "INSERT INTO wb_standard_costs_snapshots (store_slug,payload,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(store_slug) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
            (
                command.store_slug,
                json.dumps(
                    command.snapshot.model_dump(mode="json", exclude_unset=True),
                    ensure_ascii=False,
                    allow_nan=False,
                ),
                command.snapshot.updated_at,
            ),
        )
        if command.raw is not None:
            conn.execute(
                "INSERT INTO wb_standard_costs_sources (store_slug,payload,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(store_slug) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (
                    command.store_slug,
                    json.dumps(command.raw.model_dump(), ensure_ascii=False, allow_nan=False),
                    command.snapshot.updated_at,
                ),
            )


class SqlAlchemyWbStandardCostsUnitOfWork(UnitOfWork):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)
        self.repository: SqlAlchemyWbStandardCostsRepository

    def __enter__(self) -> "SqlAlchemyWbStandardCostsUnitOfWork":
        super().__enter__()
        if self.session is None:
            raise RuntimeError("UnitOfWork is not active")
        self.repository = SqlAlchemyWbStandardCostsRepository(self.session)
        return self
