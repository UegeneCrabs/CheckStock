"""Resolve a store's account without choosing another account visible to the key."""

from app.yandex import api, tokens


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
    if len(business_ids) != 1:
        raise ValueError(
            f"Яндекс {store_slug}: укажите business_id нужного кабинета в настройках интеграции; "
            "список магазинов API не определяет доступ ключа к каждому кабинету"
        )
    return business_ids.pop()
