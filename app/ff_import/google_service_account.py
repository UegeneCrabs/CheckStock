import json
from functools import lru_cache

from app.config import settings

CREDENTIALS_PATH = settings.google_service_account_path
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class CredentialsUnavailableError(Exception):
    pass


def has_credentials() -> bool:
    return CREDENTIALS_PATH.exists()


@lru_cache(maxsize=1)
def _load_key_data() -> dict:
    with open(CREDENTIALS_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_service_account_email() -> str:

    try:
        return _load_key_data().get("client_email", "?")
    except (OSError, json.JSONDecodeError):
        return "?"


@lru_cache(maxsize=1)
def get_credentials():

    try:
        from google.oauth2 import service_account
    except ImportError as e:
        raise CredentialsUnavailableError(
            "для чтения приватных Google Таблиц на сервере нужны пакеты "
            "google-api-python-client и google-auth — установи их в .venv "
            "(pip install google-api-python-client google-auth) и попробуй снова"
        ) from e

    try:
        return service_account.Credentials.from_service_account_file(str(CREDENTIALS_PATH), scopes=SCOPES)
    except (OSError, ValueError) as e:
        raise CredentialsUnavailableError(
            f"не удалось прочитать ключ сервисного аккаунта ({CREDENTIALS_PATH}): {e}"
        ) from e


def reload_credentials() -> None:

    _load_key_data.cache_clear()
    get_credentials.cache_clear()
