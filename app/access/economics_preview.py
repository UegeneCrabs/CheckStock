"""Account-scoped rollout of the Ozon dashboard and daily economics calendar."""

from fastapi import HTTPException

from app.config import settings
from app.dto.identity import User, coerce_user

PRIVATE_JOB_NAMES = frozenset(
    {
        "ozon_reputation_sync",
        "ozon_unit_economics_turnover_sync",
        "ozon_unit_economics_advertising_sync",
        "ozon_unit_economics_1c_source_sync",
    }
)


def can_preview(user: User | None) -> bool:
    normalized = coerce_user(user)
    return bool(
        normalized
        and normalized.is_active
        and settings.economics_preview_user_id > 0
        and normalized.id == settings.economics_preview_user_id
    )


def require_preview(user: User | None) -> None:
    if not can_preview(user):
        raise HTTPException(404, "Страница не найдена")


def calendar_link(user: User | None, *, yandex: bool = False) -> str:
    if not can_preview(user):
        return ""
    path = "/sales/unit-economics-1c" + ("/yandex-market" if yandex else "")
    return (
        f'<a class="ue1c-ghost-button" id="ue1c-calendar-link" href="{path}?calendar=1">Параметры по дням</a>'
    )
