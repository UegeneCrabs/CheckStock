from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from app.access.authentication import is_active_user
from app.access.economics_preview import can_preview
from app.access.resources import accessible_marketplaces, accessible_stores
from app.config import settings
from app.core.constants import INBOUND_SCHEDULE, WebErrorCode
from app.core.export_journal import diagnostic
from app.core.stores import STORES
from app.dto.identity import SectionName
from app.dto.ui import UiContext, UiStore, UiUser
from app.infrastructure.integration_repository import IntegrationRepository
from app.web.client_templates import load_client_templates
from app.web.dependencies import AsyncResourcesDependency

router = APIRouter()


@router.get("/api/ui/context")
async def context(request: Request, resources: AsyncResourcesDependency) -> UiContext:
    user = request.state.user
    if not is_active_user(user):
        raise HTTPException(403, WebErrorCode.FORBIDDEN)
    stores = accessible_stores(user)
    for alert in await IntegrationRepository(resources.database).alerts(stores):
        diagnostic(
            "source_health",
            marketplace=alert.marketplace,
            store=alert.store_slug,
            scope=alert.scope,
            checked_at=alert.checked_at,
            expires_at=alert.expires_at,
            detail=alert.error or alert.code.value,
        )
    return UiContext(
        user=UiUser.model_validate(user),
        stores=tuple(
            UiStore(slug=slug, name=STORES[slug].name, marketplaces=accessible_marketplaces(user, slug))
            for slug in stores
        ),
        sections=tuple(section for section in SectionName if section != SectionName.STOCK_RANDOMIZER),
        inbound_interval_seconds=settings.inbound_sync_interval_seconds,
        inbound_schedule=INBOUND_SCHEDULE.description,
        warehouse_display_limit=settings.warehouse_display_limit,
        economics_preview=can_preview(user),
    )


@router.get("/api/ui/templates")
async def templates(request: Request, modules: Annotated[list[str], Query()]) -> dict[str, str]:
    if not is_active_user(request.state.user):
        raise HTTPException(403, WebErrorCode.FORBIDDEN)
    try:
        return load_client_templates(tuple(modules))
    except ValueError as error:
        raise HTTPException(422, WebErrorCode.INVALID_TARGET) from error
