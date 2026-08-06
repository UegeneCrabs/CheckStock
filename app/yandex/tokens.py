"""
Доступы к API Яндекс Маркета.

Отличие от WB и Ozon: у Яндекса два разных идентификатора, и путать их нельзя.

    businessId  — кабинет. К нему привязан КАТАЛОГ товаров.
    campaignId  — магазин внутри кабинета. К нему привязаны ОСТАТКИ.

При этом у одного кабинета может быть несколько магазинов — по одному на
модель работы (FBY, FBS, DBS, Экспресс). То есть наш «магазин» на Яндексе
может оказаться набором из нескольких campaignId с разными схемами, и
остатки нужно спрашивать у каждого.

Формат secrets/yandex_tokens.json:

    {
      "rimili": {
        "api_key": "ACMA:...",
        "business_id": 123456,
        "campaigns": [
          {"id": 987654, "scheme": "fby", "name": "TRIS"},
          {"id": 987655, "scheme": "fbs", "name": "ФуллСервис"}
        ]
      }
    }

Поля business_id и campaigns можно не заполнять руками: их подскажет
scripts/check_yandex.py — он спрашивает их у самого Маркета по ключу.
"""

import json
from pathlib import Path

SECRETS_PATH = Path(__file__).resolve().parent.parent.parent / "secrets" / "yandex_tokens.json"


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
    """Есть ли магазин в файле ключей вообще — см. wb/tokens.is_listed."""
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
    """[{"id": 987654, "scheme": "fby"}, ...]. Пустой список — значит
    магазины ещё не прописаны и их надо получить у Маркета."""
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
        result.append({
            "id": campaign_id,
            "scheme": scheme,
            "name": str(item.get("name") or "").strip() or f"Магазин {campaign_id}",
            "scheme_key": scheme_key(scheme, campaign_id),
        })
    return result


# FBY у Яндекса — это склад площадки, то же самое, что FBO у WB и Ozon.
# Называем его так же: в интерфейсе колонки должны читаться одинаково, а
# площадочные жаргонизмы только мешают сравнивать магазины между собой.
FBY_SCHEME_KEY = "fbo"


def scheme_key(scheme: str, campaign_id: int) -> str:
    """Ключ схемы для хранения остатков.

    FBY у кабинета один, поэтому ключ простой. А вот FBS-магазинов может быть
    несколько, и это РАЗНЫЕ склады разных партнёров — сложить их в одну
    колонку значит потерять главное: у кого именно лежит товар. Поэтому в
    ключ входит идентификатор магазина.
    """
    scheme = (scheme or "").lower()
    if scheme in ("fby", "fbo"):
        return FBY_SCHEME_KEY
    return f"{scheme or 'fbs'}_{campaign_id}"


def scheme_label(campaign: dict) -> str:
    """Подпись колонки в таблице остатков."""
    if campaign["scheme_key"] == FBY_SCHEME_KEY:
        return "FBY — склады Маркета"
    return f"FBS {campaign['name']}"


def stores_with_credentials() -> list[str]:
    return [slug for slug, entry in _load().items()
            if isinstance(entry, dict) and entry.get("api_key")]
