import json

from app.config import settings

SECRETS_PATH = settings.yandex_tokens_path


def _load() -> dict:
    if not SECRETS_PATH.exists():
        return {}
    try:
        with SECRETS_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_listed(store_slug: str) -> bool:

    return store_slug in _load()


def has_credentials(store_slug: str) -> bool:
    entry = _load().get(store_slug) or {}
    return bool(entry.get("api_key"))


def get_api_key(store_slug: str) -> str:
    entry = _load().get(store_slug) or {}
    key = str(entry.get("api_key") or "").strip()
    if not key:
        raise KeyError(f"нет ключа Яндекс Маркета для магазина {store_slug}")
    return key


def get_business_id(store_slug: str) -> int | None:
    entry = _load().get(store_slug) or {}
    try:
        return int(entry["business_id"])
    except (KeyError, TypeError, ValueError):
        return None


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


def scheme_key(scheme: str, campaign_id: int) -> str:

    scheme = (scheme or "").lower()
    if scheme in ("fby", "fbo"):
        return FBY_SCHEME_KEY
    return f"{scheme or 'fbs'}_{campaign_id}"


def scheme_label(campaign: dict) -> str:

    if campaign["scheme_key"] == FBY_SCHEME_KEY:
        return "FBY — склады Маркета"
    return f"FBS {campaign['name']}"


def stores_with_credentials() -> list[str]:
    return [slug for slug, entry in _load().items() if isinstance(entry, dict) and entry.get("api_key")]
