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
