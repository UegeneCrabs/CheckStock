import json

from app.config import settings

SECRETS_PATH = settings.yandex_tokens_path


def _load() -> dict:
    """Accept the historical spelling while using the canonical application slug."""
    if not SECRETS_PATH.exists():
        return {}
    try:
        with SECRETS_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    if "sokoloff" not in data and isinstance(data.get("sokolof"), dict):
        data["sokoloff"] = data.pop("sokolof")
    return data


def is_listed(store_slug: str) -> bool:

    return store_slug in _load()


def has_credentials(store_slug: str) -> bool:
    return bool(get_accounts(store_slug))


def get_api_key(store_slug: str) -> str:
    accounts = get_accounts(store_slug)
    if not accounts:
        raise KeyError(f"нет ключа Яндекс Маркета для магазина {store_slug}")
    return str(accounts[0]["api_key"])


def get_api_key_for_business(store_slug: str, business_id: int) -> str:
    for account in get_accounts(store_slug):
        if account["business_id"] == business_id:
            return str(account["api_key"])
    raise KeyError(f"нет ключа Яндекс Маркета для кабинета {business_id}")


def get_accounts(store_slug: str) -> list[dict]:
    entry = _load().get(store_slug) or {}
    raw_accounts = entry.get("accounts")
    if not isinstance(raw_accounts, list):
        key = str(entry.get("api_key") or "").strip()
        raw_accounts = [
            {"business_id": business_id, "api_key": key} for business_id in get_business_ids(store_slug)
        ]
    accounts: list[dict] = []
    seen: set[int] = set()
    for item in raw_accounts:
        if not isinstance(item, dict):
            continue
        try:
            business_id = int(item.get("business_id"))
        except (TypeError, ValueError):
            continue
        api_key = str(item.get("api_key") or "").strip()
        if business_id > 0 and api_key and business_id not in seen:
            campaign_ids: list[int] = []
            for value in item.get("campaign_ids") or []:
                try:
                    campaign_id = int(value)
                except (TypeError, ValueError):
                    continue
                if campaign_id > 0 and campaign_id not in campaign_ids:
                    campaign_ids.append(campaign_id)
            accounts.append({"business_id": business_id, "api_key": api_key, "campaign_ids": campaign_ids})
            seen.add(business_id)
    return accounts


def get_business_id(store_slug: str) -> int | None:
    business_ids = get_business_ids(store_slug)
    return business_ids[0] if business_ids else None


def get_business_ids(store_slug: str) -> list[int]:
    entry = _load().get(store_slug) or {}
    accounts = entry.get("accounts")
    if isinstance(accounts, list):
        values = [item.get("business_id") for item in accounts if isinstance(item, dict)]
    else:
        values = entry.get("business_ids")
    if not isinstance(values, list):
        values = [entry.get("business_id")]
    result: list[int] = []
    for value in values:
        try:
            business_id = int(value)
        except (TypeError, ValueError):
            continue
        if business_id > 0 and business_id not in result:
            result.append(business_id)
    return result


def get_campaigns(store_slug: str) -> list[dict]:

    entry = _load().get(store_slug) or {}
    campaigns = entry.get("campaigns")
    if not isinstance(campaigns, list):
        return []

    result = []
    for item in campaigns:
        if not isinstance(item, dict):
            continue
        try:
            campaign_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue

        scheme = str(item.get("scheme") or "").lower()
        result.append(
            {
                "id": campaign_id,
                "scheme": scheme,
                "name": str(item.get("name") or "").strip() or f"Магазин {campaign_id}",
                "scheme_key": scheme_key(scheme, campaign_id),
            }
        )
    return result


FBY_SCHEME_KEY = "fbo"
FBS_SCHEME_KEY = "fbs"


def scheme_key(scheme: str, campaign_id: int) -> str:

    scheme = (scheme or "").lower()
    if scheme in ("fby", "fbo"):
        return FBY_SCHEME_KEY
    return FBS_SCHEME_KEY


def scheme_label(campaign: dict) -> str:

    if campaign["scheme_key"] == FBY_SCHEME_KEY:
        return "FBY — склады Маркета"
    return "FBS — склады продавца"


def stores_with_credentials() -> list[str]:
    return [slug for slug in _load() if get_accounts(slug)]
