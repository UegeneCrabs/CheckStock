"""
Хранение API-токенов Wildberries.

Токены НЕ хранятся в базе данных и не хардкодятся в коде — они лежат в
локальном JSON-файле secrets/wb_tokens.json, который не должен попадать
в git (см. .gitignore). В репозитории остаётся только пример-шаблон
secrets/wb_tokens.example.json с пустыми значениями.

Формат файла:
{
    "rimili": "токен_магазина_rimili",
    "tris": "токен_магазина_tris",
    ...
}

Один токен категории "Маркетплейс" + "Аналитика"/"Статистика" на магазин
достаточно для запросов остатков FBS и FBO (см. wb_api.py).
"""

import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from functools import lru_cache

# app/wb/tokens.py живёт на два уровня глубже корня проекта — secrets/ остаётся в корне.
logger = logging.getLogger("checkstock.wb_tokens")

TOKENS_PATH = Path(__file__).resolve().parent.parent.parent / "secrets" / "wb_tokens.json"


class TokenNotFoundError(Exception):
    pass


@lru_cache(maxsize=8)
def _load_tokens_cached(signature: tuple[int, int] | None) -> dict:
    """См. ozon/tokens._load_tokens: пустой или битый файл — это «токенов нет»."""
    if signature is None or not TOKENS_PATH.exists():
        return {}
    try:
        with open(TOKENS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        if TOKENS_PATH.stat().st_size == 0:
            logger.warning("Файл токенов WB пуст: %s — считаем, что токенов нет",
                           TOKENS_PATH)
        else:
            logger.error("Файл токенов WB испорчен (%s): %s — считаем, что токенов нет",
                         TOKENS_PATH, e)
        return {}
    except OSError as e:
        logger.error("Файл токенов WB не прочитан (%s): %s", TOKENS_PATH, e)
        return {}

    if not isinstance(data, dict):
        logger.error("Файл токенов WB должен содержать объект {магазин: ...}: %s",
                     TOKENS_PATH)
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
    """Сбросить кэш — вызывать, если файл с токенами обновили без перезапуска сервера."""
    _load_tokens_cached.cache_clear()


def get_token(store_slug: str) -> str:
    token = _load_tokens().get(store_slug)
    if not token:
        raise TokenNotFoundError(
            f"Нет WB-токена для магазина '{store_slug}'. "
            f"Добавьте его в {TOKENS_PATH}"
        )
    return token


def is_listed(store_slug: str) -> bool:
    """Есть ли магазин в файле ключей вообще.

    Отличать «ключа нет» от «магазин на площадке не работает» важно: в первом
    случае это недосмотр, во втором — норма, и предупреждать не о чем.
    """
    return store_slug in _load_tokens()


def has_token(store_slug: str) -> bool:
    return bool(_load_tokens().get(store_slug))


# ---------------------------------------------------------------------------
# Срок действия ключа
# ---------------------------------------------------------------------------
#
# Токен WB — это JWT, и дата окончания действия лежит прямо в нём (поле exp).
# Значит, узнать её можно локально, не тратя запрос к API и не завися от его
# доступности. Подпись мы не проверяем — она нужна серверу WB, а нам достаточно
# прочитать полезную нагрузку.


def decode_token_claims(token: str) -> dict:
    """Разбирает полезную нагрузку JWT. Возвращает {} на любом мусоре —
    токен мог быть введён с опечаткой, и падать из-за этого не стоит."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # base64url без выравнивания
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def get_token_expiry(token: str) -> datetime | None:
    """Когда ключ перестанет работать. None — если срок вычитать не удалось."""
    exp = decode_token_claims(token).get("exp")
    if not isinstance(exp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(exp, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
