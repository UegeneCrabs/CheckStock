"""Resolve a store's account without choosing another account visible to the key."""

import hashlib
import threading
import time

from app.yandex import api, tokens

_DISCOVERED: dict[tuple[str, str], tuple[float, int]] = {}
_DISCOVERY_LOCK = threading.Lock()


def _accessible_business(store_slug: str, api_key: str, business_ids: set[int]) -> int:
    """The campaigns list may include other businesses; verify actual catalog access."""
    cache_key = (store_slug, hashlib.sha256(api_key.encode()).hexdigest())
    with _DISCOVERY_LOCK:
        cached = _DISCOVERED.get(cache_key)
        if cached and cached[0] > time.monotonic() and cached[1] in business_ids:
            return cached[1]
        accessible = []
        for business_id in sorted(business_ids):
            try:
                api.request(
                    f"/v2/businesses/{business_id}/offer-mappings",
                    api_key,
                    payload={},
                    params={"limit": 1},
                )
            except api.YandexApiError as error:
                if error.status == 403:
                    continue
                raise
            accessible.append(business_id)
        if len(accessible) != 1:
            raise ValueError(
                f"Яндекс {store_slug}: не удалось однозначно определить кабинет по доступу ключа; "
                "укажите business_id нужного кабинета в настройках интеграции"
            )
        _DISCOVERED[cache_key] = (time.monotonic() + 3600, accessible[0])
        return accessible[0]


def resolve_business_id(store_slug: str, api_key: str, *, campaigns: list[dict] | None = None) -> int:
    configured = tokens.get_business_id(store_slug)
    if configured:
        return configured

    campaign_ids = {row["id"] for row in tokens.get_campaigns(store_slug)}
    rows = api.get_campaigns(api_key) if campaigns is None else campaigns
    business_ids = {
        int(api.normalize_campaign(row)["business_id"] or 0)
        for row in rows
        if not campaign_ids or row.get("id") in campaign_ids
    } - {0}
    if len(business_ids) > 1 and not campaign_ids:
        return _accessible_business(store_slug, api_key, business_ids)
    if len(business_ids) != 1:
        raise ValueError(
            f"Яндекс {store_slug}: укажите business_id нужного кабинета в настройках интеграции; "
            "список магазинов API не определяет доступ ключа к каждому кабинету"
        )
    return business_ids.pop()
