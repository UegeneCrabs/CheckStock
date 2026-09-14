from datetime import UTC, datetime, timedelta

from app.application.inbound_supplies import InboundSourceError

MAX_PAGES = 1000
HISTORY_DAYS = 90


def object_row(value: object, context: str) -> dict:
    if not isinstance(value, dict):
        raise InboundSourceError(f"Некорректный ответ: {context}.")
    return value


def rows(value: object, context: str) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise InboundSourceError(f"Некорректный список: {context}.")
    return value


def identifier(value: object, context: str) -> str:
    if value is None or isinstance(value, (dict, list, bool)) or not str(value).strip():
        raise InboundSourceError(f"Отсутствует идентификатор: {context}.")
    return str(value).strip()


def count(value: object, context: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise InboundSourceError(f"Некорректное количество: {context}.")
    try:
        number = int(str(value))
    except (ValueError, TypeError) as error:
        raise InboundSourceError(f"Не получено количество: {context}.") from error
    if number < 0:
        raise InboundSourceError(f"Отрицательное количество: {context}.")
    return number


def recent(value: object, now: datetime | None = None) -> bool:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp >= (now or datetime.now(UTC)) - timedelta(days=HISTORY_DAYS)


def api_error(marketplace: str, error: Exception) -> InboundSourceError:
    status = getattr(error, "status", None)
    if status in (401, 403):
        message = {
            "WB": "Проверьте ключ WB и доступ к категории «Поставки».",
            "OZON": "Проверьте Client ID, ключ Ozon и права на чтение FBO-поставок и актов.",
            "YANDEX MARKET": "Проверьте ключ Яндекс Маркета и право «Получение информации по FBY-заявкам».",
        }[marketplace]
    elif status in (420, 429):
        message = "Площадка ограничила частоту запросов. Обновление повторится по расписанию."
    elif status and status >= 500:
        message = "Площадка временно недоступна. Обновление повторится по расписанию."
    else:
        message = "Не удалось получить поставки от площадки."
    return InboundSourceError(f"{message}{f' Код {status}.' if status else ''}")
