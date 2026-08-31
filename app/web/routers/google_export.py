from __future__ import annotations

import html
from dataclasses import replace
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app import auth, db, stock_sheet_export
from app.domain import MOSCOW_TIMEZONE
from app.formatting import format_dt
from app.repositories.stock_sheet_export import (
    ExportTarget,
    MarketplaceSpreadsheet,
    StockSheetExportSettings,
)
from app.stores import STORES
from app.sync_tracking import run_tracked
from app.web.templating import fill_template, render_page

router = APIRouter()

WEEKDAYS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)
MARKETPLACE_LABELS = {"WB": "Wildberries", "OZON": "Ozon", "YANDEX MARKET": "Яндекс Маркет"}
MARKETPLACE_FORM_PREFIXES = {"WB": "wb", "OZON": "ozon", "YANDEX MARKET": "yandex"}
METRIC_LABELS = {
    "ff_stock": "Остатки ФФ",
    "fbs_stock": "Текущий сток FBS",
    "fbo_stock": "Текущий сток FBO",
}


def _require_superadmin(request: Request) -> None:
    if not auth.has_role(request.state.user, "superadmin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _input(value: str) -> str:
    return html.escape(value, quote=True)


def _render_weekdays(selected: int) -> str:
    return "".join(
        f'<option value="{index}"{" selected" if index == selected else ""}>{label}</option>'
        for index, label in enumerate(WEEKDAYS)
    )


def _sheet_name(settings: StockSheetExportSettings, marketplace: str) -> str:
    for target in settings.targets:
        if target.marketplace == marketplace:
            return target.sheet_name
    return marketplace


def _render_store_card(settings: StockSheetExportSettings, *, active: bool) -> str:
    store = STORES[settings.store_slug]
    checked = " checked" if settings.enabled else ""
    daily_selected = " selected" if settings.schedule_kind == "daily" else ""
    weekly_selected = " selected" if settings.schedule_kind == "weekly" else ""
    status_class = "export-status--error" if settings.last_error else "export-status--ok"
    status_text = (
        f"Ошибка {format_dt(settings.last_attempt_at)}: {settings.last_error}"
        if settings.last_error
        else (
            f"Последняя успешная выгрузка: {format_dt(settings.last_success_at)}"
            if settings.last_success_at
            else "Выгрузка ещё не запускалась"
        )
    )
    marketplace_sections = []
    for marketplace in stock_sheet_export.repository.MARKETPLACES:
        prefix = MARKETPLACE_FORM_PREFIXES[marketplace]
        marketplace_label = MARKETPLACE_LABELS[marketplace]
        marketplace_sections.append(
            '<section class="export-marketplace">'
            f'<div class="export-marketplace-head"><div><span>{marketplace}</span>'
            f"<h3>{html.escape(marketplace_label)}</h3></div></div>"
            '<label class="export-url-field"><span>Ссылка на Google Таблицу</span>'
            f'<input class="input-control" type="url" name="{prefix}_spreadsheet_url" '
            f'value="{_input(settings.spreadsheet_url_for(marketplace))}" '
            f'placeholder="Таблица для {html.escape(marketplace_label)}" required></label>'
            '<label class="export-url-field"><span>Название листа</span>'
            f'<input class="input-control" name="{prefix}_sheet_name" '
            f'value="{_input(_sheet_name(settings, marketplace))}" maxlength="200" required></label>'
            '<p class="panel-desc">Диапазон A2:G будет полностью заменён: шапка в строке 2, товары — с строки 3.</p>'
            "</section>"
        )
    return (
        f'<form class="panel export-store-card" data-export-form data-store="{settings.store_slug}"'
        f"{'' if active else ' hidden'}>"
        '<div class="export-card-head"><div class="export-store-title">'
        f'<span class="store-dot" style="--store-color:{_input(store.color)}"></span>'
        f"<div><small>МАГАЗИН</small><h2>{html.escape(store.name)}</h2></div></div>"
        '<label class="export-enabled"><input type="checkbox" name="enabled" value="1"'
        f"{checked}><span>Автовыгрузка включена</span></label></div>"
        '<div class="export-schedule-grid">'
        '<label><span>Периодичность</span><select class="select-control" name="schedule_kind" '
        'data-schedule-kind><option value="daily"'
        f'{daily_selected}>Каждый день</option><option value="weekly"{weekly_selected}>Раз в неделю</option></select></label>'
        '<label data-weekday-field><span>День недели</span><select class="select-control" name="weekday">'
        f"{_render_weekdays(settings.weekday)}</select></label>"
        '<label><span>Время (Москва)</span><input class="input-control" type="time" name="run_time" '
        f'value="{_input(settings.run_time)}" required></label></div>'
        + "".join(marketplace_sections)
        + f'<p class="export-status {status_class}" data-export-status>{html.escape(status_text)}</p>'
        '<div class="export-actions"><button class="btn-primary" type="submit">Сохранить настройки</button>'
        '<button class="btn-secondary" type="button" data-export-now>Выгрузить сейчас</button></div>'
        "</form>"
    )


def _value(form, name: str) -> str:
    return str(form.get(name) or "").strip()


def _settings_from_form(
    store_slug: str,
    form,
    existing: StockSheetExportSettings,
) -> StockSheetExportSettings:
    try:
        weekday = int(_value(form, "weekday") or 0)
    except ValueError as error:
        raise ValueError("Некорректный день недели") from error
    targets: list[ExportTarget] = []
    spreadsheets: list[MarketplaceSpreadsheet] = []
    for marketplace in stock_sheet_export.repository.MARKETPLACES:
        prefix = MARKETPLACE_FORM_PREFIXES[marketplace]
        spreadsheets.append(
            MarketplaceSpreadsheet(
                marketplace=marketplace,
                spreadsheet_url=_value(form, f"{prefix}_spreadsheet_url"),
            )
        )
        sheet_name = _value(form, f"{prefix}_sheet_name")
        targets.extend(
            ExportTarget(
                marketplace=marketplace,
                metric=metric,
                sheet_name=sheet_name,
                key_column_name=stock_sheet_export.EXPORT_HEADERS[0],
                value_column_name=stock_sheet_export.EXPORT_METRIC_HEADERS[metric],
            )
            for metric in stock_sheet_export.repository.STOCK_METRICS
        )
    return replace(
        existing,
        enabled=_value(form, "enabled") == "1",
        schedule_kind=_value(form, "schedule_kind"),
        weekday=weekday,
        run_time=_value(form, "run_time"),
        spreadsheets=tuple(spreadsheets),
        updated_at=datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
        targets=tuple(targets),
    )


@router.get("/admin/google-export", response_class=HTMLResponse)
async def google_export_page(request: Request):
    _require_superadmin(request)
    await run_in_threadpool(stock_sheet_export.ensure_defaults)
    settings = await run_in_threadpool(stock_sheet_export.list_settings)
    content = fill_template(
        "google_export_content.html",
        store_tabs="".join(
            '<button type="button" class="export-store-tab'
            f'{" is-active" if index == 0 else ""}" data-export-store-tab="{item.store_slug}">'
            f"{html.escape(STORES[item.store_slug].name)}</button>"
            for index, item in enumerate(settings)
        ),
        store_cards="".join(
            _render_store_card(item, active=index == 0) for index, item in enumerate(settings)
        ),
        service_account_email=html.escape(google_service_account_email()),
    )
    return render_page(
        "CheckStock — Выгрузка в Google Таблицы",
        "admin_google_export",
        content,
        request.state.user,
        "content--google-export",
    )


def google_service_account_email() -> str:
    from app.ff_import import google_service_account

    return google_service_account.get_service_account_email()


@router.post("/admin/google-export/{store_slug}")
async def save_google_export_settings(request: Request, store_slug: str):
    _require_superadmin(request)
    if store_slug not in STORES:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    form = await request.form()
    existing = await run_in_threadpool(stock_sheet_export.get_settings, store_slug)
    try:
        settings = _settings_from_form(store_slug, form, existing)
        await run_in_threadpool(stock_sheet_export.save_settings, settings)
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)

    actor = request.state.user
    await run_in_threadpool(
        db.log_action,
        actor.id,
        actor.full_name,
        "Изменены настройки выгрузки",
        f"{STORES[store_slug].name}: {settings.schedule_kind} {settings.run_time}",
        datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
    )
    return JSONResponse({"ok": True})


@router.post("/admin/google-export/{store_slug}/run")
async def run_google_export(request: Request, store_slug: str):
    _require_superadmin(request)
    if store_slug not in STORES:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    try:
        report = await run_in_threadpool(
            run_tracked,
            "stock_sheet_export",
            "manual",
            lambda: stock_sheet_export.run_store(store_slug),
        )
    except Exception as error:
        return JSONResponse(
            {"ok": False, "error": f"{type(error).__name__}: {error}"},
            status_code=502,
        )
    return JSONResponse({"ok": True, "report": report})
