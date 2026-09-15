"""Private, fixed per-store HTTP proxy configuration for the storefront browser."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from app.config import BASE_DIR
from app.core.stores import STORES


class ProxyConfigError(ValueError):
    """Messages must not contain proxy addresses or credentials."""


def load() -> dict[str, dict[str, str]] | None:
    configured_path = os.environ.get("CHECKSTOCK_YANDEX_STOREFRONT_PROXIES_PATH")
    path = Path(configured_path or BASE_DIR / "secrets/yandex_storefront_proxies.json")
    if not path.exists() and not configured_path:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ProxyConfigError("Не удалось прочитать JSON с прокси витрины ЯМ") from None
    if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
        raise ProxyConfigError("В настройках прокси поле enabled должно быть true или false")
    if not data["enabled"]:
        return None
    stores = data.get("stores")
    if not isinstance(stores, dict) or not stores or any(slug not in STORES for slug in stores):
        raise ProxyConfigError("Проверьте список магазинов в настройках прокси витрины ЯМ")
    proxies = {}
    for slug, entry in stores.items():
        if not isinstance(entry, dict):
            raise ProxyConfigError(f"{slug}: неверный формат прокси")
        server = entry.get("server")
        username, password = entry.get("username"), entry.get("password")
        if not all(isinstance(value, str) and value for value in (server, username, password)):
            raise ProxyConfigError(f"{slug}: заполните server, username и password")
        try:
            parsed = urlsplit(server)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.port
                and not parsed.username
                and not parsed.password
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
                and not any(character.isspace() for character in server)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ProxyConfigError(
                f"{slug}: нужен HTTP(S)-прокси вида http://адрес:порт; логин и пароль задаются отдельно"
            )
        proxies[slug] = {"server": server.rstrip("/"), "username": username, "password": password}
    return proxies
