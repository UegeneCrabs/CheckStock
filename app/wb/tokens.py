import base64
import json
import logging
from datetime import UTC, datetime
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)

TOKENS_PATH = settings.wb_tokens_path


class TokenNotFoundError(Exception):
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
            logger.warning("Файл токенов WB пуст: %s — считаем, что токенов нет", TOKENS_PATH)
        else:
            logger.error("Файл токенов WB испорчен (%s): %s — считаем, что токенов нет", TOKENS_PATH, e)
        return {}
    except OSError as e:
        logger.error("Файл токенов WB не прочитан (%s): %s", TOKENS_PATH, e)
        return {}

    if not isinstance(data, dict):
        logger.error("Файл токенов WB должен содержать объект {магазин: ...}: %s", TOKENS_PATH)
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


def get_token(store_slug: str) -> str:
    token = _load_tokens().get(store_slug)
    if not token:
        raise TokenNotFoundError(f"Нет WB-токена для магазина '{store_slug}'. Добавьте его в {TOKENS_PATH}")
    return token


def is_listed(store_slug: str) -> bool:

    return store_slug in _load_tokens()


def has_token(store_slug: str) -> bool:
    return bool(_load_tokens().get(store_slug))


def decode_token_claims(token: str) -> dict:

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def get_token_expiry(token: str) -> datetime | None:

    exp = decode_token_claims(token).get("exp")
    if not isinstance(exp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(exp, UTC)
    except (OSError, OverflowError, ValueError):
        return None
