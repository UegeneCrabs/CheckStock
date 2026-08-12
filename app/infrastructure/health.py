from collections.abc import Callable
from pathlib import Path

from app.dto.system import ReadinessStatus
from app.infrastructure.database import database_for_path


class DatabaseHealthService:
    def __init__(self, database_path: Callable[[], Path]) -> None:
        self._database_path = database_path

    def readiness(self) -> ReadinessStatus:
        database = database_for_path(self._database_path())
        try:
            with database.engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1").scalar_one()
        except Exception:
            return ReadinessStatus(status="unavailable", database="unavailable")
        return ReadinessStatus(status="ok", database="ok")
