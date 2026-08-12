import threading

from app.config import settings
from app.infrastructure.database import DatabaseConnection, database_for_path

DB_PATH = settings.database_path
WRITE_LOCK = threading.Lock()


def get_connection() -> DatabaseConnection:
    return database_for_path(DB_PATH).connect()
