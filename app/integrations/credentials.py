from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from app.ozon import tokens as ozon_tokens
from app.wb import tokens as wb_tokens
from app.yandex import tokens as yandex_tokens

MARKETPLACE_CODES = {"wb": "WB", "ozon": "OZON", "yandex": "YANDEX MARKET"}
_LOCK = threading.Lock()


class CredentialStorageError(RuntimeError):
    pass


def _read(path: Path) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CredentialStorageError(
            f"Файл {path.name} не удалось прочитать. Сохранение отменено, чтобы не потерять данные."
        ) from error
    if not isinstance(data, dict):
        raise CredentialStorageError(f"Файл {path.name} должен содержать JSON-объект.")
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(data, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        try:
            os.chmod(temporary_name, 0o600)
        except OSError:
            pass
        os.replace(temporary_name, path)
    except OSError as error:
        raise CredentialStorageError(f"Не удалось сохранить {path.name}.") from error
    finally:
        if temporary_name and os.path.exists(temporary_name):
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def credential_status(store_slug: str, code: str) -> dict[str, object]:
    if code == "wb":
        data = _read(wb_tokens.TOKENS_PATH)
        token = str(data.get(store_slug) or "").strip()
        configured = bool(token)
        expiry = None
        if configured:
            parsed = wb_tokens.get_token_expiry(token)
            expiry = parsed.isoformat() if parsed else None
        return {"configured": configured, "expires_at": expiry}
    if code == "ozon":
        data = _read(ozon_tokens.TOKENS_PATH)
        entry = data.get(store_slug) if isinstance(data.get(store_slug), dict) else {}
        client_id = str(entry.get("client_id") or "").strip()
        return {
            "configured": bool(client_id and str(entry.get("api_key") or "").strip()),
            "client_id_hint": f"••••{client_id[-4:]}" if client_id else "",
        }
    if code == "yandex":
        data = _read(yandex_tokens.SECRETS_PATH)
        entry = data.get(store_slug) if isinstance(data.get(store_slug), dict) else {}
        return {"configured": bool(str(entry.get("api_key") or "").strip())}
    raise ValueError("Неизвестный маркетплейс")


def save_credential(
    store_slug: str,
    code: str,
    *,
    api_key: str,
    client_id: str = "",
) -> None:
    api_key = api_key.strip()
    client_id = client_id.strip()
    if not api_key:
        raise ValueError("Введите новый API-ключ")
    if len(api_key) > 16_384 or len(client_id) > 1_024:
        raise ValueError("Значение ключа слишком длинное")

    with _LOCK:
        if code == "wb":
            data = _read(wb_tokens.TOKENS_PATH)
            data[store_slug] = api_key
            _write(wb_tokens.TOKENS_PATH, data)
            wb_tokens.reload_tokens()
            return
        if code == "ozon":
            data = _read(ozon_tokens.TOKENS_PATH)
            current = data.get(store_slug) if isinstance(data.get(store_slug), dict) else {}
            effective_client_id = client_id or str(current.get("client_id") or "").strip()
            if not effective_client_id:
                raise ValueError("Для нового подключения Ozon укажите Client ID")
            data[store_slug] = {**current, "client_id": effective_client_id, "api_key": api_key}
            _write(ozon_tokens.TOKENS_PATH, data)
            ozon_tokens.reload_tokens()
            return
        if code == "yandex":
            data = _read(yandex_tokens.SECRETS_PATH)
            current = data.get(store_slug) if isinstance(data.get(store_slug), dict) else {}
            data[store_slug] = {**current, "api_key": api_key}
            _write(yandex_tokens.SECRETS_PATH, data)
            return
    raise ValueError("Неизвестный маркетплейс")


def delete_credential(store_slug: str, code: str) -> None:
    with _LOCK:
        if code == "wb":
            data = _read(wb_tokens.TOKENS_PATH)
            data.pop(store_slug, None)
            _write(wb_tokens.TOKENS_PATH, data)
            wb_tokens.reload_tokens()
            return
        if code == "ozon":
            data = _read(ozon_tokens.TOKENS_PATH)
            current = data.get(store_slug) if isinstance(data.get(store_slug), dict) else {}
            if current:
                current = {**current}
                current.pop("api_key", None)
                if current:
                    data[store_slug] = current
                else:
                    data.pop(store_slug, None)
            _write(ozon_tokens.TOKENS_PATH, data)
            ozon_tokens.reload_tokens()
            return
        if code == "yandex":
            data = _read(yandex_tokens.SECRETS_PATH)
            current = data.get(store_slug) if isinstance(data.get(store_slug), dict) else {}
            if current:
                current = {**current}
                current.pop("api_key", None)
                if current:
                    data[store_slug] = current
                else:
                    data.pop(store_slug, None)
            _write(yandex_tokens.SECRETS_PATH, data)
            return
    raise ValueError("Неизвестный маркетплейс")
