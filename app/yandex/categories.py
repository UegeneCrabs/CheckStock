"""Category ancestry and weekly refresh calendar for Yandex Market."""

from datetime import datetime, timedelta
from threading import Lock

from app.core.domain import MOSCOW_TIMEZONE

TREE_SOURCE = "category_tree"
_catalog_lock = Lock()


def category_paths(tree: dict) -> dict[int, dict]:
    result = {}
    pending = [(tree, [])]
    while pending:
        node, ancestors = pending.pop()
        if not isinstance(node, dict) or not node.get("id") or not node.get("name"):
            raise ValueError("Яндекс вернул неполное дерево категорий")
        category_id = int(node["id"])
        if category_id in result:
            raise ValueError("Яндекс повторил категорию в дереве")
        children = node.get("children") or []
        if not isinstance(children, list):
            raise ValueError("Яндекс вернул некорректные дочерние категории")
        path = [*ancestors, {"id": category_id, "name": str(node["name"])}]
        result[category_id] = {
            "category_id": category_id,
            "category_name": str(node["name"]),
            "path": path,
            "leaf": not children,
        }
        pending.extend((child, path) for child in children)
    return result


def save_tree(tree):
    from app.repositories import yandex_economics as repository

    paths = category_paths(tree)
    items = [
        {
            "id": row["category_id"],
            "name": row["category_name"],
            "parent_id": row["path"][-2]["id"] if len(row["path"]) > 1 else None,
            "leaf": row["leaf"],
        }
        for row in paths.values()
    ]
    payload = {"root_id": int(tree["id"]), "items": items}
    repository.save_source("", "", TREE_SOURCE, payload)
    return payload


def catalog(store):
    """Share the public tree locally; retain the last successful tree on API failure."""
    from app.repositories import yandex_economics as repository
    from app.yandex import api, tokens

    with _catalog_lock:
        saved = repository.source("", "", TREE_SOURCE)
        try:
            current = datetime.fromisoformat(saved["updated_at"]) >= week_start()
        except (KeyError, TypeError, ValueError):
            current = False
        if current:
            return {**saved["values"], "stale": False}
        try:
            tree = api.request("/v2/categories/tree", tokens.get_api_key(store), payload={"language": "RU"})
            return {**save_tree(tree), "stale": False}
        except (api.YandexApiError, KeyError, ValueError):
            if saved.get("values", {}).get("items"):
                return {**saved["values"], "stale": True}
            raise ValueError(
                "Не удалось загрузить категории ЯМ. Проверьте доступ API и повторите попытку."
            ) from None


def week_start(now=None):
    current = (now or datetime.now(MOSCOW_TIMEZONE)).astimezone(MOSCOW_TIMEZONE)
    start = (current - timedelta(days=current.weekday())).replace(hour=4, minute=0, second=0, microsecond=0)
    return start if start <= current else start - timedelta(days=7)


def next_delay(now=None):
    current = now or datetime.now(MOSCOW_TIMEZONE)
    return (week_start(current) + timedelta(days=7) - current).total_seconds()
