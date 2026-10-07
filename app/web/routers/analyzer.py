"""Analyzer page and scoped data/notes endpoints."""

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import db
from app.access.access_control import accessible_stores
from app.access.sections import has_access
from app.analytics import analyzer
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.identity import SectionAccessLevel, SectionName, coerce_user
from app.repositories import analyzer as repository
from app.web.templating import fill_template, render_page

router = APIRouter()


def authorize(request, *, write=False):
    level = SectionAccessLevel.WRITE if write else SectionAccessLevel.READ
    if not has_access(request.state.user, SectionName.ANALYZER, level):
        raise HTTPException(403, "Нет доступа к анализатору или прав на изменение записей")
    return accessible_stores(request.state.user, "WB")


@router.get("/analytics/analyzer", response_class=HTMLResponse)
async def analyzer_page(request: Request):
    authorize(request)
    content = fill_template(
        "analytics/analyzer.html",
        analyzer_config=json.dumps(
            {
                "canEdit": has_access(request.state.user, SectionName.ANALYZER, SectionAccessLevel.WRITE),
                "today": datetime.now(MOSCOW_TIMEZONE).date().isoformat(),
            }
        ).replace("</", "<\\/"),
    )
    return render_page(
        "CheckStock — Анализатор", "analyzer", content, request.state.user, content_class="content--analyzer"
    )


@router.get("/api/analytics/analyzer")
async def analyzer_data(request: Request, week: date | None = None):
    stores = authorize(request)
    today = datetime.now(MOSCOW_TIMEZONE).date()
    start = week or today
    start -= timedelta(days=start.weekday())
    if start > today or start < date(2000, 1, 3):
        raise HTTPException(422, "Выберите неделю с 2000 года по текущую")
    return await run_in_threadpool(analyzer.load, stores, start, request.state.user)


class NoteText(BaseModel):
    note: str = Field(min_length=1, max_length=4000)


class NoteRequest(NoteText):
    store: str = Field(min_length=1, max_length=100)
    article: str = Field(min_length=1, max_length=200)
    day: date


def validate_note(text: str) -> str:
    text = text.strip()
    if not text:
        raise HTTPException(422, "Введите текст записи")
    return text


def require_product(store, article, user):
    products = db.get_catalog_items(store, "WB")
    references = {row["article"]: row for row in db.get_unit_economics_1c_product_reference_rows((store,))}
    if not any(row["article"] == article for row in products) or not analyzer.permitted(
        references.get(article, {}).get("manager"), user
    ):
        raise HTTPException(404, "Товар не найден или недоступен")


@router.post("/api/analytics/analyzer/notes")
async def analyzer_note(request: Request, payload: NoteRequest):
    stores = authorize(request, write=True)
    if payload.store not in stores:
        raise HTTPException(403, "Нет доступа к кабинету")
    text = validate_note(payload.note)
    if payload.day != datetime.now(MOSCOW_TIMEZONE).date():
        raise HTTPException(422, "Добавлять и изменять комментарии можно только за сегодня")

    def save():
        require_product(payload.store, payload.article, request.state.user)
        return repository.add_note(
            payload.store, payload.article, payload.day.isoformat(), text, coerce_user(request.state.user)
        )

    return {"ok": True, "note": {**await run_in_threadpool(save), "can_edit": True}}


@router.put("/api/analytics/analyzer/notes/{note_id}")
async def analyzer_note_update(request: Request, note_id: int, payload: NoteText):
    stores = authorize(request, write=True)
    text = validate_note(payload.note)

    def save():
        original = repository.get_note(note_id)
        if original is None or original["store_slug"] not in stores:
            raise HTTPException(404, "Запись не найдена или недоступна")
        require_product(original["store_slug"], original["article"], request.state.user)
        if original["action_date"] != datetime.now(MOSCOW_TIMEZONE).date().isoformat():
            raise HTTPException(422, "Изменять комментарии можно только за сегодня")
        try:
            return repository.update_note(original, text, coerce_user(request.state.user))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return {"ok": True, "note": {**await run_in_threadpool(save), "can_edit": True}}
