"""
Состояние доступов магазина ко всем площадкам.

Собирает две разные вещи в один список:

- ключа вообще нет — это видно сразу, по файлам в secrets/;
- ключ есть, но площадка его не приняла — это видно только по результату
  последней синхронизации, поэтому он сохраняется в sync_health.

Разделять их важно: «токен не заведён» решает тот, у кого есть доступ к
кабинету, а «токен не отдаёт склады» — тот, кто выпускал ключ, и подсказка
там нужна другая.
"""

from datetime import datetime, timezone

from app import db
from app.ozon import tokens as ozon_tokens
from app.wb import tokens as wb_tokens
from app.yandex import tokens as ya_tokens

# Площадки в фиксированном порядке: окно должно выглядеть одинаково у всех
# магазинов, а не перестраиваться в зависимости от того, что сломалось.
MARKETPLACES = ("WB", "OZON", "YANDEX MARKET")

TITLES = {
    "WB": "WB",
    "OZON": "OZON",
    "YANDEX MARKET": "Яндекс Маркет",
}

# Что проверяем у площадки. Названия короткие — они попадают в уведомление.
SCOPES = {
    "WB": {"catalog": "каталог", "fbs": "склады продавца FBS",
           "fbo": "склады маркетплейса FBO"},
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
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None


def _checked_at(row: dict) -> datetime | None:
    try:
        value = datetime.fromisoformat(row["checked_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
    """Заведён ли магазин в файле ключей этой площадки.

    Разница принципиальная. Магазина нет в файле — значит он на этой площадке
    не работает, и предупреждать не о чем. Магазин есть, но ключ пустой или
    не принимается — это недосмотр, о котором сказать надо.
    """
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
    """Состояние доступов магазина по площадкам.

    Возвращает только площадки, где ключ ЕСТЬ, но площадка его не приняла,
    в фиксированном порядке WB -> OZON -> Яндекс Маркет.

    Пустой или отсутствующий ключ проблемой не считается: магазин просто
    не работает на этой площадке. Отличить это от поломки иначе нельзя, а
    предупреждать обо всех незаполненных ключах — значит приучить закрывать
    окно не глядя.

    Формулировки короткие и без инструкций: ключи заводит не тот, кто
    работает с остатками, и подробности про категории доступа этому
    человеку ничего не дают — ему важно знать, что цифрам по площадке
    доверять нельзя.
    """
    problems: list[dict] = []
    health_rows = db.get_sync_health(store_slug)

    for marketplace in MARKETPLACES:
        title = TITLES.get(marketplace, marketplace)

        # Ключа нет или он пустой — считаем, что площадка не используется.
        # Предупреждать тут не о чем: не у каждого магазина есть кабинет на
        # каждом маркетплейсе, и напоминание об этом было бы шумом.
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

        # Если отказала только часть — уточняем какая. Это факт, а не
        # инструкция: без него непонятно, почему часть колонок заполнена.
        # Скобки внутри скобок не городим — читается плохо.
        partial = len(broken) < len(scopes)
        status = (f"Ключ недействителен для раздела «{', '.join(broken)}»."
                  if partial else "Ключ недействителен.")

        problems.append({
            "marketplace": marketplace,
            "title": title,
            "kind": "invalid",
            "status": status,
            "detail": "Данные по площадке не обновляются.",
        })

    return problems


def stores_with_problems() -> dict[str, list[dict]]:
    """Проблемы по всем магазинам — для общей страницы остатков."""
    from app.stores import STORES
    return {slug: store_problems(slug) for slug in STORES}
