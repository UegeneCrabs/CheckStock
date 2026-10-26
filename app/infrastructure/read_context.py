from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.infrastructure.database import DatabaseConnection

_READ_CONNECTION: ContextVar[Callable[[], DatabaseConnection] | None] = ContextVar(
    "read_connection", default=None
)


def current_read_connection() -> DatabaseConnection | None:
    factory = _READ_CONNECTION.get()
    return factory() if factory else None


@contextmanager
def repository_read_scope(factory: Callable[[], DatabaseConnection]) -> Iterator[None]:
    token = _READ_CONNECTION.set(factory)
    try:
        yield
    finally:
        _READ_CONNECTION.reset(token)
