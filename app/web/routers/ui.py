from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from app.access.authentication import is_active_user
from app.access.economics_preview import can_preview
from app.access.resources import accessible_marketplaces, accessible_stores
from app.config import settings
from app.core.constants import WebErrorCode
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
    return UiContext(
        user=UiUser.model_validate(user),
        stores=tuple(
            UiStore(slug=slug, name=STORES[slug].name, marketplaces=accessible_marketplaces(user, slug))
            for slug in stores
        ),
        sections=tuple(SectionName),
        inbound_interval_seconds=settings.inbound_sync_interval_seconds,
        warehouse_display_limit=settings.warehouse_display_limit,
        economics_preview=can_preview(user),
        alerts=await IntegrationRepository(resources.database).alerts(stores),
    )


@router.get("/api/ui/templates")
async def templates(request: Request, modules: Annotated[list[str], Query()]) -> dict[str, str]:
    if not is_active_user(request.state.user):
        raise HTTPException(403, WebErrorCode.FORBIDDEN)
    try:
        return load_client_templates(tuple(modules))
    except ValueError as error:
        raise HTTPException(422, WebErrorCode.INVALID_TARGET) from error
