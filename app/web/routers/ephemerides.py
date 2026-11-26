"""Scoped ephemerides pages and weekly comments."""

import json
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.access.authentication import is_active_user
from app.access.resources import accessible_stores
from app.analytics import ephemerides
from app.container import ephemerides_goal_repository
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.ephemerides_goal import (
    WEEKLY_GOAL_MAX,
    WeeklyGoal,
    WeeklyGoalChange,
    WeeklyGoalResponse,
    WeeklyGoalsApplied,
    WeeklyGoalsApply,
)
from app.dto.identity import coerce_user
from app.infrastructure.ephemerides_goal_repository import WeeklyGoalConflict
from app.repositories import ephemerides as repository
from app.web.routers.analyzer import require_product
from app.web.templating import fill_template, render_page

router = APIRouter()
Kind = Literal["competitor", "decision", "next"]


def authorize(request, *, write=False):
    if not is_active_user(request.state.user):
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
                "canEdit": is_active_user(request.state.user),
                "today": datetime.now(MOSCOW_TIMEZONE).date().isoformat(),
                "history": request.url.path.endswith("/comments"),
                "weeklyGoalMax": WEEKLY_GOAL_MAX,
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
    start = checked_week(week)

    def load():
        goals = ephemerides_goal_repository().for_weeks(stores, start, start - timedelta(weeks=1))
        return ephemerides.load(stores, start, request.state.user, manual_goals=goals)

    return await run_in_threadpool(load)


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


def checked_week_key(week: date) -> date:
    start = checked_week(week)
    if start != week:
        raise HTTPException(422, "Ключ недели должен указывать на понедельник")
    return start


def check_product_week(store, article, week, stores, user):
    if store not in stores:
        raise HTTPException(404, "Товар не найден или недоступен")
    start = checked_week_key(week)
    require_product(store, article, user)
    return start


def check_key(payload, stores, user):
    start = check_product_week(payload.store, payload.article, payload.week, stores, user)
    return payload.store, payload.article, start.isoformat(), payload.kind


@router.put("/api/analytics/ephemerides/goals", response_model=WeeklyGoalResponse)
async def save_goal(request: Request, payload: WeeklyGoalChange) -> WeeklyGoalResponse | JSONResponse:
    stores = authorize(request, write=True)

    def update() -> WeeklyGoal:
        check_product_week(payload.store, payload.article, payload.week, stores, request.state.user)
        return ephemerides_goal_repository().save(payload)

    try:
        goal = await run_in_threadpool(update)
    except WeeklyGoalConflict as error:
        return JSONResponse(
            status_code=409, content={"detail": str(error), "goal": error.latest.model_dump(mode="json")}
        )
    return WeeklyGoalResponse(goal=goal)


@router.post("/api/analytics/ephemerides/goals/apply")
async def apply_goals(request: Request, payload: WeeklyGoalsApply) -> WeeklyGoalsApplied:
    stores = authorize(request, write=True)
    week = checked_week_key(payload.week)
    changed = await run_in_threadpool(lambda: ephemerides_goal_repository().apply(stores, week))
    return WeeklyGoalsApplied(changed=changed)


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
