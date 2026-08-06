"""
Слежение за сроком действия ключей WB.

Дата окончания зашита в самом токене (JWT, поле exp), поэтому проверка
локальная — без запросов к API. Раз в неделю (по воскресеньям) данные
перечитываются заново: ключ могли заменить, и у нового будет другой срок.

Если до окончания осталось меньше WARN_DAYS дней (или срок уже вышел) —
на страницах показывается предупреждение, чтобы ключ успели заменить до
того, как синхронизация встанет.
"""

import logging
from datetime import datetime, timedelta, timezone

from app import db
from app.stores import STORES
from app.wb import tokens as wb_tokens

logger = logging.getLogger("checkstock.tokens")

WARN_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def refresh_token_info() -> int:
    """Перечитывает сроки действия всех ключей и сохраняет их в БД.
    Возвращает количество обработанных магазинов."""
    now_iso = _now().isoformat()
    processed = 0

    with db.WRITE_LOCK:
        for slug in STORES:
            if not wb_tokens.has_token(slug):
                continue
            expiry = wb_tokens.get_token_expiry(wb_tokens.get_token(slug))
            db.upsert_wb_token_info(slug, expiry.isoformat() if expiry else None, now_iso)
            processed += 1

    logger.info("Сроки действия ключей WB обновлены: %s магазинов", processed)
    return processed


def should_refresh(last_checked: str | None) -> bool:
    """Обновлять ли данные сейчас.

    Обновляем, если проверки ещё не было, если она была больше недели назад
    (сервер мог быть выключен в воскресенье), либо если сегодня воскресенье и
    сегодня ещё не проверяли.
    """
    if not last_checked:
        return True
    try:
        checked = datetime.fromisoformat(last_checked)
    except ValueError:
        return True

    now = _now()
    if (now - checked) >= timedelta(days=7):
        return True
    # 6 = воскресенье
    return now.weekday() == 6 and checked.date() != now.date()


def get_warnings() -> list[dict]:
    """Ключи, которые скоро протухнут или уже протухли.

    Возвращает список {"store", "expires_at", "days_left", "expired"}.
    """
    now = _now()
    warnings = []

    for info in db.get_wb_token_infos():
        if not info["expires_at"]:
            continue
        try:
            expires = datetime.fromisoformat(info["expires_at"])
        except ValueError:
            continue

        days_left = (expires - now).days
        if days_left > WARN_DAYS:
            continue

        store = STORES.get(info["store_slug"], {})
        warnings.append({
            "store": store.get("name", info["store_slug"]),
            "expires_at": info["expires_at"],
            "days_left": days_left,
            "expired": expires <= now,
        })

    # Сначала самые срочные
    warnings.sort(key=lambda w: w["days_left"])
    return warnings
