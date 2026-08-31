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

ACCESS_ERROR_MARKERS = (
    "нет доступа",
    "доступ запрещён",
    "не хватает прав",
    "ключ отозван",
    "api_disabled",
    "ошибка 401",
    "ошибка 403",
    " 401",
    " 403",
)


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


def requires_team_action(message: object) -> bool:
    error = str(message or "").casefold()
    return any(marker in error for marker in ACCESS_ERROR_MARKERS)


def _is_access_error(row: dict) -> bool:
    return requires_team_action(row.get("error"))


def store_problems(store_slug: str) -> list[dict]:

    problems: list[dict] = []
    health_rows = db.get_sync_health(store_slug)

    for marketplace in MARKETPLACES:
        title = TITLES.get(marketplace, marketplace)

        if not has_token(marketplace, store_slug):
            if is_listed(marketplace, store_slug):
                problems.append(
                    {
                        "marketplace": marketplace,
                        "title": title,
                        "kind": "invalid",
                        "status": "Нужно проверить API-ключ: ключ не заполнен или недействителен.",
                        "detail": (
                            "Обновите ключ в настройках интеграции и повторите выгрузку. "
                            "До исправления показываем ранее загруженные данные."
                        ),
                    }
                )
            continue

        stock_scopes = SCOPES.get(marketplace, {})
        rows = _fresh_health_rows(
            marketplace,
            [
                row
                for row in health_rows
                if row["marketplace"] == marketplace and row["scope"] in stock_scopes
            ],
        )
        if not rows:
            continue

        actionable_rows = [row for row in rows if _is_access_error(row)]
        if not actionable_rows:
            continue
        broken = list(dict.fromkeys(stock_scopes[row["scope"]] for row in actionable_rows))
        status = f"Нужно проверить права API-ключа для раздела «{', '.join(broken)}»."
        detail = (
            "Добавьте ключу доступ к этому разделу или замените ключ, затем повторите выгрузку. "
            "До исправления показываем ранее загруженные данные."
        )

        problems.append(
            {
                "marketplace": marketplace,
                "title": title,
                "kind": "invalid",
                "status": status,
                "detail": detail,
            }
        )

    return problems


def stores_with_problems() -> dict[str, list[dict]]:

    from app.stores import STORES

    return {slug: store_problems(slug) for slug in STORES}
