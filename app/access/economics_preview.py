"""Feature switch for the Ozon dashboard and daily economics calendar."""

from fastapi import HTTPException

from app.config import settings
from app.core.constants import JOBS
from app.dto.identity import User, coerce_user

PRIVATE_JOB_NAMES = frozenset(
    {
        "ozon_reputation_sync",
        JOBS.ozon_orders.name,
        JOBS.ozon_prices.name,
        JOBS.ozon_prices_close.name,
        JOBS.ozon_logistics.name,
        JOBS.ozon_storage.name,
        JOBS.ozon_buyout.name,
        JOBS.ozon_margin.name,
        JOBS.ozon_advertising_breakdown.name,
        "ozon_unit_economics_advertising_sync",
        "ozon_unit_economics_1c_source_sync",
    }
)


def can_preview(user: User | None) -> bool:
    normalized = coerce_user(user)
    return bool(normalized and normalized.is_active and settings.economics_preview_enabled)


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
