import json
from collections.abc import Callable
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse

from app.access.authentication import is_active_user
from app.access.resources import accessible_stores
from app.application.yandex_analytics import (
    AnalyticsConflict,
    AnalyticsForbidden,
    AnalyticsNotFound,
    YandexAnalyticsService,
)
from app.container import ApplicationContainer
from app.core.constants import WB_ANALYZER, YANDEX_ANALYTICS
from app.dto.identity import User, coerce_user
from app.dto.yandex_analytics import (
    AnalyzerResponse,
    CommentChange,
    CommentKey,
    CommentResponse,
    DSettings,
    EphemeridesResponse,
    Kind,
    NoteChange,
    NoteCreate,
    NoteResponse,
    RevisionResponse,
    SalesResponse,
)
from app.web.templating import fill_template, render_page

router = APIRouter()


def authorize(request: Request) -> tuple[str, ...]:
    if not is_active_user(request.state.user):
        raise HTTPException(403, "inactive_user")
    return tuple(accessible_stores(request.state.user, YANDEX_ANALYTICS.marketplace))


def service(request: Request) -> YandexAnalyticsService:
    container: ApplicationContainer = request.app.state.container
    return container.yandex_analytics


def actor(request: Request) -> User:
    user = coerce_user(request.state.user)
    if user is None or not user.is_active:
        raise HTTPException(403, "inactive_user")
    return user


async def invoke[Result](callback: Callable[[], Result]) -> Result:
    try:
        return await run_in_threadpool(callback)
    except AnalyticsNotFound as error:
        raise HTTPException(404, str(error)) from error
    except AnalyticsForbidden as error:
        raise HTTPException(403, str(error)) from error
    except AnalyticsConflict as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/analytics/yandex-market/analyzer", response_class=HTMLResponse)
@router.get("/analytics/yandex-market/ephemerides", response_class=HTMLResponse)
@router.get("/analytics/yandex-market/ephemerides/comments", response_class=HTMLResponse)
@router.get("/analytics/yandex-market/sales-api", response_class=HTMLResponse)
async def page(request: Request) -> HTMLResponse:
    authorize(request)
    history = request.url.path.endswith("/comments")
    name = request.url.path.split("/")[3]
    config = {
        "today": service(request).clock().date().isoformat(),
        "maxDays": YANDEX_ANALYTICS.max_days,
        "maxD": WB_ANALYZER.max_d,
        "canEdit": True,
        "history": history,
        "marketplace": YANDEX_ANALYTICS.marketplace,
        "apiBase": "/api/analytics/yandex-market",
        "pageBase": "/analytics/yandex-market",
        "storageKey": "checkstock.yandex-market." + name + ".v1",
        "exportPrefix": "yandex-market",
        "metricIdentity": "fullArticle",
        "hourlySettings": name == "analyzer",
    }
    content = fill_template(
        "analytics/" + name + ".html",
        **{name.replace("-", "_") + "_config": json.dumps(config).replace("</", "<\\/")},
    )
    active = "yandex_" + name.replace("-", "_")
    markup = await run_in_threadpool(
        render_page,
        "CheckStock",
        active,
        content,
        request.state.user,
        content_class="content--sales-api" if name == "sales-api" else "content--analyzer",
    )
    return HTMLResponse(markup)


@router.get("/api/analytics/yandex-market/analyzer")
async def analyzer(request: Request, week: date | None = None) -> AnalyzerResponse:
    stores = authorize(request)
    return await invoke(lambda: service(request).analyzer(stores, week))


@router.get("/api/analytics/yandex-market/ephemerides")
async def ephemerides(request: Request, week: date | None = None) -> EphemeridesResponse:
    stores = authorize(request)
    return await invoke(lambda: service(request).ephemerides(stores, week))


@router.get("/api/analytics/yandex-market/ephemerides/comments")
async def comment_history(request: Request, week: date | None = None) -> EphemeridesResponse:
    stores = authorize(request)
    return await invoke(lambda: service(request).ephemerides(stores, week, history=True))


@router.get("/api/analytics/yandex-market/sales-api")
async def sales(
    request: Request, date_from: date | None = None, date_to: date | None = None
) -> SalesResponse:
    stores = authorize(request)
    return await invoke(lambda: service(request).sales(stores, date_from, date_to))


@router.post("/api/analytics/yandex-market/analyzer/notes")
async def add_note(request: Request, payload: NoteCreate) -> NoteResponse:
    stores = authorize(request)
    return NoteResponse(note=await invoke(lambda: service(request).add_note(stores, payload, actor(request))))


@router.put("/api/analytics/yandex-market/analyzer/notes/{note_id}")
async def edit_note(request: Request, note_id: int, payload: NoteChange) -> NoteResponse:
    stores = authorize(request)
    return NoteResponse(
        note=await invoke(lambda: service(request).edit_note(stores, note_id, payload, actor(request)))
    )


@router.put("/api/analytics/yandex-market/ephemerides/comments")
async def save_comment(request: Request, payload: CommentChange) -> CommentResponse:
    stores = authorize(request)
    return CommentResponse(
        comment=await invoke(lambda: service(request).save_comment(stores, payload, actor(request)))
    )


@router.get("/api/analytics/yandex-market/ephemerides/comments/revisions")
async def revisions(
    request: Request,
    store: Annotated[str, Query(min_length=1, max_length=100)],
    article: Annotated[str, Query(min_length=1, max_length=200)],
    week: date,
    kind: Kind,
) -> RevisionResponse:
    stores = authorize(request)
    key = CommentKey(store=store, article=article, week=week, kind=kind)
    return RevisionResponse(revisions=await invoke(lambda: service(request).revisions(stores, key)))


@router.get("/api/analytics/yandex-market/settings/{store}")
async def settings(request: Request, store: str) -> DSettings:
    stores = authorize(request)
    return await invoke(lambda: service(request).settings(stores, store))


@router.put("/api/analytics/yandex-market/settings/{store}")
async def save_settings(request: Request, store: str, payload: DSettings) -> DSettings:
    stores = authorize(request)
    return await invoke(lambda: service(request).save_settings(stores, store, payload))
