import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.core.logging_config import tagged_logger
from app.dto.orders import OzonCredentials

logger = tagged_logger(__name__, "ozon", "tokens")

TOKENS_PATH = settings.ozon_tokens_path
_read_path: ContextVar[Path | None] = ContextVar("ozon_tokens_read_path", default=None)


@contextmanager
def read_tokens_from(path: Path) -> Iterator[None]:
    token = _read_path.set(path)
    try:
        yield
    finally:
        _read_path.reset(token)


def _path() -> Path:
    return _read_path.get() or TOKENS_PATH


class OzonCredentialsNotFoundError(Exception):
    pass


@lru_cache(maxsize=8)
def _load_tokens_cached(path: Path, signature: tuple[int, int] | None) -> dict[str, dict[str, object]]:

    if signature is None or not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        if path.stat().st_size == 0:
            logger.warning("Файл доступов Ozon пуст: %s — считаем, что ключей нет", path)
        else:
            logger.error("Файл доступов Ozon испорчен (%s): %s — считаем, что ключей нет", path, e)
        return {}
    except OSError as e:
        logger.error("Файл доступов Ozon не прочитан (%s): %s", path, e)
        return {}

    if not isinstance(data, dict):
        logger.error("Файл доступов Ozon должен содержать объект {магазин: {...}}: %s", path)
        return {}
    return {store: entry if isinstance(entry, dict) else {} for store, entry in data.items()}


def _tokens_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _load_tokens() -> dict[str, dict[str, object]]:
    path = _path()
    return _load_tokens_cached(path, _tokens_signature(path))


def reload_tokens() -> None:

    _load_tokens_cached.cache_clear()


def is_listed(store_slug: str) -> bool:

    return store_slug in _load_tokens()


def get_credentials(store_slug: str) -> tuple[str, str]:

    entry = _load_tokens().get(store_slug) or {}
    client_id = str(entry.get("client_id") or "").strip()
    api_key = str(entry.get("api_key") or "").strip()

    if not client_id or not api_key:
        raise OzonCredentialsNotFoundError(
            f"Нет доступов Ozon для магазина '{store_slug}'. Добавьте client_id и api_key в {_path()}"
        )
    return client_id, api_key


def has_credentials(store_slug: str) -> bool:
    entry = _load_tokens().get(store_slug) or {}
    return bool(entry.get("client_id") and entry.get("api_key"))


def get_order_credentials(store_slug: str) -> OzonCredentials:
    client_id, api_key = get_credentials(store_slug)
    return OzonCredentials(client_id=client_id, api_key=api_key)


def get_performance_credentials(store_slug: str) -> tuple[str, str]:
    entry = _load_tokens().get(store_slug) or {}
    client_id = str(entry.get("performance_client_id") or "").strip()
    client_secret = str(entry.get("performance_client_secret") or "").strip()
    if not client_id or not client_secret:
        raise OzonCredentialsNotFoundError(
            f"Нет ключей Performance API для магазина '{store_slug}'. "
            "Добавьте performance_client_id и performance_client_secret в файл доступов Ozon"
        )
    return client_id, client_secret


def has_performance_credentials(store_slug: str) -> bool:
    entry = _load_tokens().get(store_slug) or {}
    return bool(entry.get("performance_client_id") and entry.get("performance_client_secret"))
