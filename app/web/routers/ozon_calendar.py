from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from app.access.economics_preview import require_preview
from app.access.resources import accessible_stores
from app.container import ozon_calendar_service
from app.core.constants import TIME, HTTPStatus, Marketplace, WebErrorCode
from app.core.stores import STORES
from app.dto.ozon_calendar import (
    OzonCalendarCell,
    OzonCalendarConflict,
    OzonCalendarContext,
    OzonCalendarCorrection,
    OzonCalendarKey,
    OzonCalendarListing,
    OzonCalendarPreview,
    OzonCalendarQuery,
)
from app.dto.ui import UiStore
from app.web.dependencies import AsyncResourcesDependency

router = APIRouter(prefix="/api/unit-economics-1c/ozon/calendar")


def authorize(request: Request, store: str) -> None:
    require_preview(request.state.user)
    if store not in accessible_stores(request.state.user, Marketplace.OZON):
        raise HTTPException(HTTPStatus.FORBIDDEN, WebErrorCode.FORBIDDEN)


@router.get("/config")
async def context(request: Request) -> OzonCalendarContext:
    require_preview(request.state.user)
    return OzonCalendarContext(
        stores=tuple(
            UiStore(slug=slug, name=STORES[slug].name, marketplaces=(Marketplace.OZON,))
            for slug in accessible_stores(request.state.user, Marketplace.OZON)
        ),
        today=datetime.now(TIME.moscow_timezone).date(),
    )


@router.get("")
async def listing(
    request: Request, query: Annotated[OzonCalendarQuery, Query()], resources: AsyncResourcesDependency
) -> OzonCalendarListing:
    authorize(request, query.store)
    return await ozon_calendar_service(resources).listing(query)


@router.get("/cell")
async def cell(
    request: Request, query: Annotated[OzonCalendarKey, Query()], resources: AsyncResourcesDependency
) -> OzonCalendarCell:
    authorize(request, query.store)
    try:
        return await ozon_calendar_service(resources).cell(query)
    except LookupError:
        raise HTTPException(HTTPStatus.NOT_FOUND, WebErrorCode.NOT_FOUND) from None


@router.post("/preview")
async def preview(
    request: Request, payload: OzonCalendarCorrection, resources: AsyncResourcesDependency
) -> OzonCalendarPreview:
    authorize(request, payload.store)
    try:
        return await ozon_calendar_service(resources).preview(payload)
    except LookupError:
        raise HTTPException(HTTPStatus.NOT_FOUND, WebErrorCode.NOT_FOUND) from None
    except OzonCalendarConflict:
        raise HTTPException(HTTPStatus.CONFLICT, WebErrorCode.SETTINGS_CONFLICT) from None


@router.post("/correction")
async def correct(
    request: Request, payload: OzonCalendarCorrection, resources: AsyncResourcesDependency
) -> OzonCalendarCell:
    authorize(request, payload.store)
    try:
        user = request.state.user
        return await ozon_calendar_service(resources).correct(payload, f"{user.id}: {user.full_name}")
    except LookupError:
        raise HTTPException(HTTPStatus.NOT_FOUND, WebErrorCode.NOT_FOUND) from None
    except OzonCalendarConflict:
        raise HTTPException(HTTPStatus.CONFLICT, WebErrorCode.SETTINGS_CONFLICT) from None
