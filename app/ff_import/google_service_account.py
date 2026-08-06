"""
Учётные данные сервисного аккаунта Google — нужны только для чтения
ПРИВАТНЫХ Google Таблиц (не расшаренных по ссылке "всем, у кого есть
ссылка") через Google Sheets API.

Один сервисный аккаунт общий на все 7 магазинов. Ключ лежит локально в
secrets/google_service_account.json и не должен попадать в git (см.
.gitignore) — по аналогии с secrets/wb_tokens.json.

Как настроить:
1. Создать проект в Google Cloud, включить в нём Google Sheets API.
2. Создать сервисный аккаунт, скачать JSON-ключ, положить его сюда как
   secrets/google_service_account.json (пример формата —
   secrets/google_service_account.example.json).
3. В каждой нужной приватной таблице нажать "Настройки доступа" и дать
   доступ "Читатель" на e-mail сервисного аккаунта — это client_email
   из JSON-ключа, его можно посмотреть через get_service_account_email().

Если файла нет вообще — ничего не ломается: публичные таблицы (расшаренные
по ссылке) по-прежнему читаются как раньше, простым CSV-скачиванием без
всякой авторизации. Приватные таблицы в этом случае просто не читаются, с
понятной ошибкой на этот счёт.
"""

import json
from functools import lru_cache
from pathlib import Path

# app/ff_import/google_service_account.py живёт на два уровня глубже корня
# проекта — secrets/ остаётся в корне.
CREDENTIALS_PATH = Path(__file__).resolve().parent.parent.parent / "secrets" / "google_service_account.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class CredentialsUnavailableError(Exception):
    """Библиотеки google-api-python-client/google-auth не установлены, либо
    ключ повреждён/нечитаем."""


def has_credentials() -> bool:
    return CREDENTIALS_PATH.exists()


@lru_cache(maxsize=1)
def _load_key_data() -> dict:
    with open(CREDENTIALS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_service_account_email() -> str:
    """E-mail сервисного аккаунта — именно на него нужно расшарить приватную
    таблицу с правами "Читатель", чтобы sync смог её прочитать."""
    try:
        return _load_key_data().get("client_email", "?")
    except (OSError, json.JSONDecodeError):
        return "?"


@lru_cache(maxsize=1)
def get_credentials():
    """Ленивый импорт google-auth — эта зависимость нужна только если реально
    используется чтение приватных таблиц (публичные работают без неё)."""
    try:
        from google.oauth2 import service_account
    except ImportError as e:
        raise CredentialsUnavailableError(
            "для чтения приватных Google Таблиц на сервере нужны пакеты "
            "google-api-python-client и google-auth — установи их в .venv "
            "(pip install google-api-python-client google-auth) и попробуй снова"
        ) from e

    try:
        return service_account.Credentials.from_service_account_file(
            str(CREDENTIALS_PATH), scopes=SCOPES
        )
    except (OSError, ValueError) as e:
        raise CredentialsUnavailableError(
            f"не удалось прочитать ключ сервисного аккаунта ({CREDENTIALS_PATH}): {e}"
        ) from e


def reload_credentials() -> None:
    """Сбросить кэш — вызывать, если ключ обновили без перезапуска сервера."""
    _load_key_data.cache_clear()
    get_credentials.cache_clear()
