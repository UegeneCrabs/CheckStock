import json
import logging
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)

TOKENS_PATH = settings.ozon_tokens_path


class OzonCredentialsNotFoundError(Exception):
    pass


@lru_cache(maxsize=8)
def _load_tokens_cached(signature: tuple[int, int] | None) -> dict:

    if signature is None or not TOKENS_PATH.exists():
        return {}
    try:
        with open(TOKENS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        if TOKENS_PATH.stat().st_size == 0:
            logger.warning("Файл доступов Ozon пуст: %s — считаем, что ключей нет", TOKENS_PATH)
        else:
            logger.error("Файл доступов Ozon испорчен (%s): %s — считаем, что ключей нет", TOKENS_PATH, e)
        return {}
    except OSError as e:
        logger.error("Файл доступов Ozon не прочитан (%s): %s", TOKENS_PATH, e)
        return {}

    if not isinstance(data, dict):
        logger.error("Файл доступов Ozon должен содержать объект {магазин: {...}}: %s", TOKENS_PATH)
        return {}
    return data


def _tokens_signature() -> tuple[int, int] | None:
    try:
        stat = TOKENS_PATH.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _load_tokens() -> dict:
    return _load_tokens_cached(_tokens_signature())


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
            f"Нет доступов Ozon для магазина '{store_slug}'. Добавьте client_id и api_key в {TOKENS_PATH}"
        )
    return client_id, api_key


def has_credentials(store_slug: str) -> bool:
    entry = _load_tokens().get(store_slug) or {}
    return bool(entry.get("client_id") and entry.get("api_key"))
