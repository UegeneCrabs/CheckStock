from datetime import UTC, datetime

from app import db
from app.domain import MARKETPLACES
from app.ozon import tokens as ozon_tokens
from app.wb import tokens as wb_tokens
from app.yandex import tokens as ya_tokens

TITLES = {
    "WB": "WB",
    "OZON": "OZON",
    "YANDEX MARKET": "Яндекс Маркет",
}


SCOPES = {
    "WB": {"catalog": "каталог", "fbs": "склады продавца FBS", "fbo": "склады маркетплейса FBO"},
    "OZON": {"catalog": "каталог", "stocks": "остатки"},
    "YANDEX MARKET": {"catalog": "каталог", "stocks": "остатки"},
}


def _credentials_updated_at(marketplace: str) -> datetime | None:
    if marketplace == "WB":
        path = wb_tokens.TOKENS_PATH
    elif marketplace == "OZON":
        path = ozon_tokens.TOKENS_PATH
    elif marketplace == "YANDEX MARKET":
        path = ya_tokens.SECRETS_PATH
    else:
        return None

    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return None


def _checked_at(row: dict) -> datetime | None:
    try:
        value = datetime.fromisoformat(row["checked_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _fresh_health_rows(marketplace: str, rows: list[dict]) -> list[dict]:
    updated_at = _credentials_updated_at(marketplace)
    if updated_at is None:
        return rows

    fresh = []
    for row in rows:
        checked_at = _checked_at(row)
        if checked_at is None or checked_at >= updated_at:
            fresh.append(row)
    return fresh


def is_listed(marketplace: str, store_slug: str) -> bool:

    if marketplace == "WB":
        return wb_tokens.is_listed(store_slug)
    if marketplace == "OZON":
        return ozon_tokens.is_listed(store_slug)
    if marketplace == "YANDEX MARKET":
        return ya_tokens.is_listed(store_slug)
    return False


def has_token(marketplace: str, store_slug: str) -> bool:
    if marketplace == "WB":
        return wb_tokens.has_token(store_slug)
    if marketplace == "OZON":
        return ozon_tokens.has_credentials(store_slug)
    if marketplace == "YANDEX MARKET":
        return ya_tokens.has_credentials(store_slug)
    return False


def store_problems(store_slug: str) -> list[dict]:

    problems: list[dict] = []
    health_rows = db.get_sync_health(store_slug)

    for marketplace in MARKETPLACES:
        title = TITLES.get(marketplace, marketplace)

        if not has_token(marketplace, store_slug):
            continue

        rows = _fresh_health_rows(
            marketplace,
            [r for r in health_rows if r["marketplace"] == marketplace],
        )
        if not rows:
            continue

        scopes = SCOPES.get(marketplace, {})
        broken = [scopes.get(r["scope"], r["scope"]) for r in rows]

        partial = len(broken) < len(scopes)
        status = (
            f"Ключ недействителен для раздела «{', '.join(broken)}»." if partial else "Ключ недействителен."
        )

        problems.append(
            {
                "marketplace": marketplace,
                "title": title,
                "kind": "invalid",
                "status": status,
                "detail": "Данные по площадке не обновляются.",
            }
        )

    return problems


def stores_with_problems() -> dict[str, list[dict]]:

    from app.stores import STORES

    return {slug: store_problems(slug) for slug in STORES}
