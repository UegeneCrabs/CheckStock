"""
Хранение доступов к Ozon Seller API.

В отличие от WB, где один JWT-токен на магазин, Ozon авторизует пару
Client-Id + Api-Key, которые уходят обычными заголовками. Срока действия
внутри ключа нет — он живёт, пока его не отозвали в личном кабинете,
поэтому предупреждать «скоро истечёт», как для WB, здесь нечего.

Доступы лежат в secrets/ozon_tokens.json (файл в .gitignore), формат:

{
    "rimili": {"client_id": "123456", "api_key": "xxxxxxxx-xxxx-..."},
    "tris":   {"client_id": "654321", "api_key": "yyyyyyyy-yyyy-..."}
}

Магазин без доступов просто пропускается — как и в WB, весь процесс из-за
этого не падает.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("checkstock.ozon_tokens")

TOKENS_PATH = Path(__file__).resolve().parent.parent.parent / "secrets" / "ozon_tokens.json"


class OzonCredentialsNotFoundError(Exception):
    pass


@lru_cache(maxsize=8)
def _load_tokens_cached(signature: tuple[int, int] | None) -> dict:
    """Доступы из файла. Нечитаемый файл — это «доступов нет», а не авария.

    Пустой файл (0 байт) — обычное состояние: его создают заранее, а ключи
    вписывают потом. json.load на нём падает, и раньше это роняло не только
    синхронизацию, но и саму страницу магазина: has_credentials вызывается
    при каждой отрисовке вкладок. Магазин без ключа Ozon должен показывать
    заглушку, а не 500.
    """
    if signature is None or not TOKENS_PATH.exists():
        return {}
    try:
        with open(TOKENS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        if TOKENS_PATH.stat().st_size == 0:
            logger.warning("Файл доступов Ozon пуст: %s — считаем, что ключей нет",
                           TOKENS_PATH)
        else:
            logger.error("Файл доступов Ozon испорчен (%s): %s — считаем, что ключей нет",
                         TOKENS_PATH, e)
        return {}
    except OSError as e:
        logger.error("Файл доступов Ozon не прочитан (%s): %s", TOKENS_PATH, e)
        return {}

    if not isinstance(data, dict):
        logger.error("Файл доступов Ozon должен содержать объект {магазин: {...}}: %s",
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
    """Сбросить кэш — если доступы обновили без перезапуска сервера."""
    _load_tokens_cached.cache_clear()


def is_listed(store_slug: str) -> bool:
    """Есть ли магазин в файле доступов вообще — см. wb/tokens.is_listed."""
    return store_slug in _load_tokens()


def get_credentials(store_slug: str) -> tuple[str, str]:
    """Возвращает (client_id, api_key) для магазина."""
    entry = _load_tokens().get(store_slug) or {}
    client_id = str(entry.get("client_id") or "").strip()
    api_key = str(entry.get("api_key") or "").strip()

    if not client_id or not api_key:
        raise OzonCredentialsNotFoundError(
            f"Нет доступов Ozon для магазина '{store_slug}'. "
            f"Добавьте client_id и api_key в {TOKENS_PATH}"
        )
    return client_id, api_key


def has_credentials(store_slug: str) -> bool:
    entry = _load_tokens().get(store_slug) or {}
    return bool(entry.get("client_id") and entry.get("api_key"))
