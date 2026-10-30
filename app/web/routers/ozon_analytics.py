from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse

from app.access.authentication import is_active_user
from app.access.resources import accessible_stores
from app.container import ozon_analytics_service
from app.core.constants import (
    OZON_ANALYTICS,
    HTTPStatus,
    Marketplace,
    OzonAnalyticsReason,
)
from app.dto.ozon_analytics import (
    OzonAnalyticsAnalyzer,
    OzonAnalyticsAuthor,
    OzonAnalyticsCommentChange,
    OzonAnalyticsCommentKey,
    OzonAnalyticsCommentResponse,
    OzonAnalyticsCommentRevisionsResponse,
    OzonAnalyticsConflict,
    OzonAnalyticsEphemerides,
    OzonAnalyticsForecastSettings,
    OzonAnalyticsHistory,
    OzonAnalyticsNoteCreate,
    OzonAnalyticsNoteResponse,
    OzonAnalyticsNoteRevisionsResponse,
    OzonAnalyticsNoteUpdate,
    OzonAnalyticsRequestError,
    OzonAnalyticsSales,
    OzonAnalyticsSalesQuery,
    OzonAnalyticsWeekQuery,
)
from app.web.client_page import client_page
from app.web.dependencies import AsyncResourcesDependency

router = APIRouter()


def authorize(request: Request) -> tuple[str, ...]:
    if not is_active_user(request.state.user):
        raise HTTPException(HTTPStatus.FORBIDDEN, OzonAnalyticsReason.INACTIVE_USER)
    return accessible_stores(request.state.user, Marketplace.OZON)


def author(request: Request) -> OzonAnalyticsAuthor:
    authorize(request)
    return OzonAnalyticsAuthor(id=request.state.user.id, name=request.state.user.full_name)


async def invoke[Result](operation: Awaitable[Result]) -> Result:
    try:
        return await operation
    except OzonAnalyticsRequestError as error:
        raise HTTPException(error.status, error.reason) from error
    except OzonAnalyticsConflict as error:
        raise HTTPException(HTTPStatus.CONFLICT, OzonAnalyticsReason.CONFLICT) from error


@router.get(OZON_ANALYTICS.api + "/settings/{store}", response_model=OzonAnalyticsForecastSettings)
async def forecast_settings(
    request: Request, resources: AsyncResourcesDependency, store: str
) -> OzonAnalyticsForecastSettings:
    return await invoke(
        ozon_analytics_service(resources.database).forecast_settings(authorize(request), store)
    )


@router.put(OZON_ANALYTICS.api + "/settings/{store}", response_model=OzonAnalyticsForecastSettings)
async def save_forecast(
    request: Request, resources: AsyncResourcesDependency, store: str, change: OzonAnalyticsForecastSettings
) -> OzonAnalyticsForecastSettings:
    return await invoke(
        ozon_analytics_service(resources.database).save_forecast(
            authorize(request), store, change, author(request)
        )
    )


@router.get(OZON_ANALYTICS.pages + "/analyzer", response_class=HTMLResponse)
async def analyzer_page(request: Request) -> HTMLResponse:
    authorize(request)
    return HTMLResponse(client_page("analytics/ozon/analyzer.html"))


@router.get(OZON_ANALYTICS.pages + "/ephemerides", response_class=HTMLResponse)
@router.get(OZON_ANALYTICS.pages + "/ephemerides/comments", response_class=HTMLResponse)
async def ephemerides_page(request: Request) -> HTMLResponse:
    authorize(request)
    return HTMLResponse(client_page("analytics/ozon/ephemerides.html"))


@router.get(OZON_ANALYTICS.pages + "/sales-api", response_class=HTMLResponse)
async def sales_page(request: Request) -> HTMLResponse:
    authorize(request)
    return HTMLResponse(client_page("analytics/ozon/sales-api.html"))


@router.get(OZON_ANALYTICS.api + "/analyzer", response_model=OzonAnalyticsAnalyzer)
async def analyzer(
    request: Request, resources: AsyncResourcesDependency, query: Annotated[OzonAnalyticsWeekQuery, Query()]
) -> OzonAnalyticsAnalyzer:
    return await invoke(ozon_analytics_service(resources.database).analyzer(authorize(request), query))


@router.get(OZON_ANALYTICS.api + "/ephemerides", response_model=OzonAnalyticsEphemerides)
async def ephemerides(
    request: Request, resources: AsyncResourcesDependency, query: Annotated[OzonAnalyticsWeekQuery, Query()]
) -> OzonAnalyticsEphemerides:
    return await invoke(ozon_analytics_service(resources.database).ephemerides(authorize(request), query))


@router.get(OZON_ANALYTICS.api + "/ephemerides/comments", response_model=OzonAnalyticsHistory)
async def comment_history(
    request: Request, resources: AsyncResourcesDependency, query: Annotated[OzonAnalyticsWeekQuery, Query()]
) -> OzonAnalyticsHistory:
    return await invoke(ozon_analytics_service(resources.database).history(authorize(request), query))


@router.get(OZON_ANALYTICS.api + "/sales-api", response_model=OzonAnalyticsSales)
async def sales(
    request: Request, resources: AsyncResourcesDependency, query: Annotated[OzonAnalyticsSalesQuery, Query()]
) -> OzonAnalyticsSales:
    return await invoke(ozon_analytics_service(resources.database).sales(authorize(request), query))


@router.post(OZON_ANALYTICS.api + "/analyzer/notes", response_model=OzonAnalyticsNoteResponse)
async def add_note(
    request: Request, resources: AsyncResourcesDependency, payload: OzonAnalyticsNoteCreate
) -> OzonAnalyticsNoteResponse:
    return OzonAnalyticsNoteResponse(
        note=await invoke(
            ozon_analytics_service(resources.database).create_note(
                authorize(request), payload, author(request)
            )
        )
    )


@router.put(OZON_ANALYTICS.api + "/analyzer/notes/{note_id}", response_model=OzonAnalyticsNoteResponse)
async def edit_note(
    request: Request,
    resources: AsyncResourcesDependency,
    note_id: Annotated[int, Path(gt=0)],
    payload: OzonAnalyticsNoteUpdate,
) -> OzonAnalyticsNoteResponse:
    return OzonAnalyticsNoteResponse(
        note=await invoke(
            ozon_analytics_service(resources.database).update_note(
                authorize(request), note_id, payload, author(request)
            )
        )
    )


@router.get(
    OZON_ANALYTICS.api + "/analyzer/notes/{note_id}/revisions",
    response_model=OzonAnalyticsNoteRevisionsResponse,
)
async def note_revisions(
    request: Request, resources: AsyncResourcesDependency, note_id: Annotated[int, Path(gt=0)]
) -> OzonAnalyticsNoteRevisionsResponse:
    return OzonAnalyticsNoteRevisionsResponse(
        revisions=await invoke(
            ozon_analytics_service(resources.database).note_revisions(authorize(request), note_id)
        )
    )


@router.put(OZON_ANALYTICS.api + "/ephemerides/comments", response_model=OzonAnalyticsCommentResponse)
async def save_comment(
    request: Request, resources: AsyncResourcesDependency, payload: OzonAnalyticsCommentChange
) -> OzonAnalyticsCommentResponse:
    return OzonAnalyticsCommentResponse(
        comment=await invoke(
            ozon_analytics_service(resources.database).save_comment(
                authorize(request), payload, author(request)
            )
        )
    )


@router.get(
    OZON_ANALYTICS.api + "/ephemerides/comments/revisions",
    response_model=OzonAnalyticsCommentRevisionsResponse,
)
async def comment_revisions(
    request: Request,
    resources: AsyncResourcesDependency,
    key: Annotated[OzonAnalyticsCommentKey, Query()],
) -> OzonAnalyticsCommentRevisionsResponse:
    return OzonAnalyticsCommentRevisionsResponse(
        revisions=await invoke(
            ozon_analytics_service(resources.database).comment_revisions(authorize(request), key)
        )
    )
