"""Category ancestry and weekly refresh calendar for Yandex Market."""

from datetime import datetime, timedelta

from app.core.domain import MOSCOW_TIMEZONE


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


def week_start(now=None):
    current = (now or datetime.now(MOSCOW_TIMEZONE)).astimezone(MOSCOW_TIMEZONE)
    start = (current - timedelta(days=current.weekday())).replace(hour=4, minute=0, second=0, microsecond=0)
    return start if start <= current else start - timedelta(days=7)


def next_delay(now=None):
    current = now or datetime.now(MOSCOW_TIMEZONE)
    return (week_start(current) + timedelta(days=7) - current).total_seconds()
