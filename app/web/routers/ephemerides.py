"""Scoped ephemerides pages and weekly comments."""

import json
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.access.access_control import accessible_stores
from app.access.sections import has_access
from app.analytics import ephemerides
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.identity import SectionAccessLevel, SectionName, coerce_user
from app.repositories import ephemerides as repository
from app.web.routers.analyzer import require_product
from app.web.templating import fill_template, render_page

router = APIRouter()
Kind = Literal["competitor", "decision", "next"]


def authorize(request, *, write=False):
    if not has_access(
        request.state.user,
        SectionName.EPHEMERIDES,
        SectionAccessLevel.WRITE if write else SectionAccessLevel.READ,
    ):
        raise HTTPException(403, "Нет доступа к эфемеридам или прав на изменение комментариев")
    return accessible_stores(request.state.user, "WB")


def checked_week(day=None):
    today = datetime.now(MOSCOW_TIMEZONE).date()
    start = ephemerides.monday(day or today)
    if start < date(2000, 1, 3) or start > today:
        raise HTTPException(422, "Выберите неделю с 2000 года по текущую")
    return start


@router.get("/analytics/ephemerides", response_class=HTMLResponse)
@router.get("/analytics/ephemerides/comments", response_class=HTMLResponse)
async def page(request: Request):
    authorize(request)
    content = fill_template(
        "analytics/ephemerides.html",
        ephemerides_config=json.dumps(
            {
                "canEdit": has_access(request.state.user, SectionName.EPHEMERIDES, SectionAccessLevel.WRITE),
                "today": datetime.now(MOSCOW_TIMEZONE).date().isoformat(),
                "history": request.url.path.endswith("/comments"),
            }
        ).replace("</", "<\\/"),
    )
    return render_page(
        "CheckStock — Эфемериды",
        "ephemerides",
        content,
        request.state.user,
        content_class="content--analyzer",
    )


@router.get("/api/analytics/ephemerides")
async def data(request: Request, week: date | None = None):
    stores = authorize(request)
    return await run_in_threadpool(ephemerides.load, stores, checked_week(week), request.state.user)


@router.get("/api/analytics/ephemerides/comments")
async def comment_history(request: Request, week: date | None = None):
    stores = authorize(request)
    return await run_in_threadpool(ephemerides.history, stores, checked_week(week), request.state.user)


class CommentKey(BaseModel):
    store: str = Field(min_length=1, max_length=100)
    article: str = Field(min_length=1, max_length=200)
    week: date
    kind: Kind


class CommentChange(CommentKey):
    text: str = Field(max_length=4000)
    version: int = Field(ge=0, le=2147483646)


def check_key(payload, stores, user):
    if payload.store not in stores:
        raise HTTPException(404, "Товар не найден или недоступен")
    start = checked_week(payload.week)
    if start != payload.week:
        raise HTTPException(422, "Ключ недели должен указывать на понедельник")
    require_product(payload.store, payload.article, user)
    return payload.store, payload.article, start.isoformat(), payload.kind


@router.put("/api/analytics/ephemerides/comments")
async def save(request: Request, payload: CommentChange):
    stores = authorize(request, write=True)

    def update():
        key = check_key(payload, stores, request.state.user)
        if payload.kind == "decision" and payload.week != checked_week():
            raise HTTPException(403, "Решения можно редактировать только за текущую неделю")
        try:
            return repository.save_comment(
                key, payload.text, payload.version, coerce_user(request.state.user)
            )
        except repository.Conflict as exc:
            raise HTTPException(409, str(exc)) from exc

    return {"ok": True, "comment": await run_in_threadpool(update)}


@router.get("/api/analytics/ephemerides/comments/revisions")
async def revisions(
    request: Request,
    store: Annotated[str, Query(min_length=1, max_length=100)],
    article: Annotated[str, Query(min_length=1, max_length=200)],
    week: date,
    kind: Kind,
):
    stores = authorize(request)
    payload = CommentKey(store=store, article=article, week=week, kind=kind)

    def read():
        key = check_key(payload, stores, request.state.user)
        return repository.revisions(key)

    return {"ok": True, "revisions": await run_in_threadpool(read)}
