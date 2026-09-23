"""Anonymous storefront session, separate from seller API and site user accounts."""

import json
import math
import os
import time
from hashlib import sha256
from pathlib import Path

from app.config import BASE_DIR

SESSION_HELP = "Подготовьте сеанс WB: python -m scripts.parsers.prepare_wb_storefront_session"
CARDS_URL = "https://www.wildberries.ru/__internal/u-card/cards/v4/detail"


class StorefrontSessionError(ValueError):
    pass


def session_path() -> Path:
    configured = os.environ.get("CHECKSTOCK_WB_STOREFRONT_SESSION_PATH")
    return Path(configured) if configured else BASE_DIR / "secrets/wb_storefront_session.json"


def validate_session(data: object) -> dict:
    if not isinstance(data, dict):
        raise StorefrontSessionError("Неверный формат сеанса витрины WB. " + SESSION_HELP)
    for key in ("token", "user_agent", "spa_version", "device_id"):
        value = data.get(key)
        if (
            not isinstance(value, str)
            or not value.strip()
            or any(not 32 <= ord(char) < 127 for char in value)
        ):
            raise StorefrontSessionError("Неполный сеанс витрины WB. " + SESSION_HELP)
    if ";" in data["token"]:
        raise StorefrontSessionError("Неверный токен сеанса витрины WB. " + SESSION_HELP)
    try:
        expires_at = float(data.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    if not math.isfinite(expires_at) or expires_at <= time.time():
        raise StorefrontSessionError("Сеанс витрины WB истёк. " + SESSION_HELP)
    return data


def read_session() -> dict:
    try:
        return validate_session(json.loads(session_path().read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        if isinstance(error, StorefrontSessionError):
            raise
        raise StorefrontSessionError("Не удалось прочитать сеанс витрины WB. " + SESSION_HELP) from None


def fingerprint(data: dict) -> str:
    """Compare session generations without putting cookies in logs or exceptions."""
    return sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def request_headers(data: dict | None = None) -> dict[str, str]:
    data = validate_session(data) if data is not None else read_session()
    return {
        "User-Agent": data["user_agent"],
        "X-Spa-Version": data["spa_version"],
        "X-Requested-With": "XMLHttpRequest",
        "DeviceId": data["device_id"],
        "Cookie": "x_wbaas_token=" + data["token"],
    }


def save_session(data: dict) -> Path:
    validate_session(data)
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as target:
            os.chmod(temporary, 0o600)
            json.dump(data, target, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
