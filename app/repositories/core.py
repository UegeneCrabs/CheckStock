import threading

from app.config import settings
from app.infrastructure.database import DatabaseConnection, database_for_path
from app.infrastructure.read_context import current_read_connection

DB_PATH = settings.database_path
WRITE_LOCK = threading.Lock()


def get_connection() -> DatabaseConnection:
    if connection := current_read_connection():
        return connection
    return database_for_path(DB_PATH).connect()
