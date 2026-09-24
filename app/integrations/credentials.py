from __future__ import annotations

import json
import os
import tempfile
import threading
from hashlib import sha256
from pathlib import Path

from app.ozon import tokens as ozon_tokens
from app.wb import tokens as wb_tokens
from app.yandex import api as yandex_api
from app.yandex import tokens as yandex_tokens

MARKETPLACE_CODES = {"wb": "WB", "ozon": "OZON", "yandex": "YANDEX MARKET"}
_LOCK = threading.Lock()


class CredentialStorageError(RuntimeError):
    pass


def _key_fingerprint(value: object) -> str:
    key = str(value or "").strip()
    return sha256(key.encode()).hexdigest()[:12] if key else ""


def check_yandex_business(store_slug: str, business_id: int) -> dict[str, str]:
    """Verify cabinet access on demand without blocking credential updates."""

    try:
        api_key = yandex_tokens.get_api_key_for_business(store_slug, business_id)
        campaigns = yandex_api.get_campaigns(api_key)
    except (KeyError, yandex_api.YandexApiError) as error:
        message = error.friendly if isinstance(error, yandex_api.YandexApiError) else str(error)
        return {"state": "error", "message": message}
    available_business_ids = {
        int(yandex_api.normalize_campaign(campaign)["business_id"] or 0) for campaign in campaigns
    }
    if business_id not in available_business_ids:
        return {
            "state": "error",
            "message": "Ключ не предоставляет доступ к этому Business ID.",
        }
    return {"state": "works", "message": "Доступ подтверждён"}


def save_yandex_account(
    store_slug: str, business_id: int, api_key: str, campaign_ids: list[int] | None = None
) -> None:
    api_key = api_key.strip()
    if business_id < 1:
        raise ValueError("Business ID должен быть положительным числом")
    if len(api_key) > 16_384:
        raise ValueError("Значение ключа слишком длинное")
    with _LOCK:
        data = _read(yandex_tokens.SECRETS_PATH)
        current = data.get(store_slug) if isinstance(data.get(store_slug), dict) else {}
        existing = next(
            (
                account
                for account in yandex_tokens.get_accounts(store_slug)
                if account["business_id"] == business_id
            ),
            None,
        )
        # The older generic integrations form stored a Yandex key at the
        # store level before a Business ID was supplied. Reuse that key when
        # the account is first bound, rather than making the user enter it
        # again.
        effective_key = api_key or str((existing or {}).get("api_key") or current.get("api_key") or "")
        if not effective_key:
            raise ValueError("Введите API-ключ для этого Business ID")
        effective_campaign_ids: list[int] = []
        source_campaign_ids = (
            campaign_ids if campaign_ids is not None else (existing or {}).get("campaign_ids", [])
        )
        for value in source_campaign_ids:
            try:
                campaign_id = int(value)
            except (TypeError, ValueError) as error:
                raise ValueError("Campaign ID должен быть положительным числом") from error
            if campaign_id < 1:
                raise ValueError("Campaign ID должен быть положительным числом")
            if campaign_id not in effective_campaign_ids:
                effective_campaign_ids.append(campaign_id)
        accounts = [
            account
            for account in yandex_tokens.get_accounts(store_slug)
            if account["business_id"] != business_id
        ]
        accounts.append(
            {
                "business_id": business_id,
                "api_key": effective_key,
                "campaign_ids": effective_campaign_ids,
            }
        )
        accounts.sort(key=lambda account: account["business_id"])
        data[store_slug] = {
            **current,
            "accounts": accounts,
            "api_key": accounts[0]["api_key"],
            "business_id": accounts[0]["business_id"],
            "business_ids": [account["business_id"] for account in accounts],
        }
        _write(yandex_tokens.SECRETS_PATH, data)


def delete_yandex_account(store_slug: str, business_id: int) -> None:
    with _LOCK:
        data = _read(yandex_tokens.SECRETS_PATH)
        current = data.get(store_slug) if isinstance(data.get(store_slug), dict) else {}
        accounts = [
            account
            for account in yandex_tokens.get_accounts(store_slug)
            if account["business_id"] != business_id
        ]
        if not accounts:
            data.pop(store_slug, None)
        else:
            data[store_slug] = {
                **current,
                "accounts": accounts,
                "api_key": accounts[0]["api_key"],
                "business_id": accounts[0]["business_id"],
                "business_ids": [account["business_id"] for account in accounts],
            }
        _write(yandex_tokens.SECRETS_PATH, data)


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
        return {
            "configured": configured,
            "expires_at": expiry,
            "key_fingerprint": _key_fingerprint(token),
        }
    if code == "ozon":
        data = _read(ozon_tokens.TOKENS_PATH)
        entry = data.get(store_slug) if isinstance(data.get(store_slug), dict) else {}
        client_id = str(entry.get("client_id") or "").strip()
        return {
            "configured": bool(client_id and str(entry.get("api_key") or "").strip()),
            "client_id_hint": f"••••{client_id[-4:]}" if client_id else "",
            "key_fingerprint": _key_fingerprint(entry.get("api_key")),
        }
    if code == "yandex":
        accounts = yandex_tokens.get_accounts(store_slug)
        return {
            "configured": bool(accounts),
            "business_ids": [account["business_id"] for account in accounts],
            "accounts": [
                {
                    "business_id": account["business_id"],
                    "key_fingerprint": _key_fingerprint(account["api_key"]),
                }
                for account in accounts
            ],
        }
    raise ValueError("Неизвестный маркетплейс")


def save_credential(
    store_slug: str,
    code: str,
    *,
    api_key: str,
    client_id: str = "",
    business_ids: list[int] | None = None,
) -> None:
    api_key = api_key.strip()
    client_id = client_id.strip()
    if len(api_key) > 16_384 or len(client_id) > 1_024:
        raise ValueError("Значение ключа слишком длинное")

    with _LOCK:
        if code == "wb":
            data = _read(wb_tokens.TOKENS_PATH)
            effective_api_key = api_key or str(data.get(store_slug) or "").strip()
            if not effective_api_key:
                raise ValueError("Введите API-ключ")
            data[store_slug] = effective_api_key
            _write(wb_tokens.TOKENS_PATH, data)
            wb_tokens.reload_tokens()
            return
        if code == "ozon":
            data = _read(ozon_tokens.TOKENS_PATH)
            current = data.get(store_slug) if isinstance(data.get(store_slug), dict) else {}
            effective_client_id = client_id or str(current.get("client_id") or "").strip()
            effective_api_key = api_key or str(current.get("api_key") or "").strip()
            if not effective_client_id:
                raise ValueError("Для нового подключения Ozon укажите Client ID")
            if not effective_api_key:
                raise ValueError("Введите API-ключ")
            data[store_slug] = {**current, "client_id": effective_client_id, "api_key": effective_api_key}
            _write(ozon_tokens.TOKENS_PATH, data)
            ozon_tokens.reload_tokens()
            return
        if code == "yandex":
            if not api_key:
                raise ValueError("Введите API-ключ")
            data = _read(yandex_tokens.SECRETS_PATH)
            current = data.get(store_slug) if isinstance(data.get(store_slug), dict) else {}
            accounts = current.get("accounts")
            if isinstance(accounts, list):
                current = {
                    **current,
                    "accounts": [
                        {**account, "api_key": api_key}
                        for account in accounts
                        if isinstance(account, dict)
                    ],
                }
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
