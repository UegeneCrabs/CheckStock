from __future__ import annotations

import html
from dataclasses import replace
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import db
from app.access import auth
from app.config import settings as app_settings
from app.core.domain import MOSCOW_TIMEZONE
from app.core.formatting import format_dt
from app.core.stores import STORES
from app.exports import project_sheet as project_sheet_export
from app.exports import stock_sheet as stock_sheet_export
from app.integrations import google_week_sales, google_week_search, google_week_stock, google_week_update
from app.jobs.locks import SyncJobBusyError
from app.jobs.tracking import run_tracked
from app.repositories import google_week_update as week_repository
from app.repositories.project_sheet_export import MarketplaceExportTarget, ProjectSheetExportSettings
from app.repositories.stock_sheet_export import (
    ExportTarget,
    MarketplaceSpreadsheet,
    StockSheetExportSettings,
)
from app.web.google_week_sales import render_result as render_week_sales_result
from app.web.google_week_search import render_result as render_week_search_result
from app.web.templating import fill_template

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
PROJECT_MARKETPLACES = ("YANDEX MARKET", "OZON", "WB")
METRIC_LABELS = {
    "ff_stock": "Остатки ФФ",
    "fbs_stock": "Текущий сток FBS",
    "fbo_stock": "Текущий сток FBO",
    "fbs_orders": "Заказы FBS за 30 дней",
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


def _sheet_name(
    settings: StockSheetExportSettings,
    marketplace: str,
    metric: str = "ff_stock",
) -> str:
    for target in settings.targets:
        if target.marketplace == marketplace and target.metric == metric:
            return target.sheet_name
    return marketplace if metric in stock_sheet_export.repository.STOCK_METRICS else ""


def _render_store_card(settings: StockSheetExportSettings, *, active: bool) -> str:
    store = STORES[settings.store_slug]
    checked = " checked" if settings.enabled else ""
    daily_selected = " selected" if settings.schedule_kind == "daily" else ""
    weekly_selected = " selected" if settings.schedule_kind == "weekly" else ""
    last_error = stock_sheet_export.current_error(settings)
    status_class = "export-status--error" if last_error else "export-status--ok"
    status_parts = []
    if settings.last_success_at:
        status_parts.append(f"Последняя успешная выгрузка: {format_dt(settings.last_success_at)}.")
    if last_error:
        status_parts.append(
            f"Ошибка последней полной выгрузки {format_dt(settings.last_attempt_at)}: {last_error}"
        )
    status_text = " ".join(status_parts) or "Выгрузка ещё не запускалась"
    marketplace_sections = []
    combined_store_hint = (
        '<p class="panel-desc"><strong>TOYKA добавляется автоматически:</strong> '
        "стоки, товары в пути и FBS-заказы суммируются с ROCKKIDDO и записываются в назначения этого магазина. "
        "Ручной запуск и расписание настраиваются у ROCKKIDDO.</p>"
        if settings.store_slug == "rockkiddo"
        else ""
    )
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
            f'placeholder="Таблица для {html.escape(marketplace_label)}"></label>'
            '<label class="export-url-field"><span>Название листа со стоками</span>'
            f'<input class="input-control" name="{prefix}_sheet_name" '
            f'value="{_input(_sheet_name(settings, marketplace))}" maxlength="200" '
            'placeholder="Оставьте пустым, чтобы не выгружать"></label>'
            '<p class="panel-desc">Необязательно. Шапка выгружается в строку 2, товары — с строки 3. '
            "A:I содержат общие показатели; начиная с J — нераспределённые остатки отдельно по каждому ФФ. "
            "E содержит их сумму, ТОТАЛ равен сумме E:I. Перед записью диапазон A2:Z полностью очищается, "
            "включая старые данные и формулы. Затем записываются текущая шапка и остатки.</p>"
            '<label class="export-url-field"><span>Лист заказов FBS за 30 дней</span>'
            f'<input class="input-control" name="{prefix}_fbs_orders_sheet_name" '
            f'value="{_input(_sheet_name(settings, marketplace, "fbs_orders"))}" maxlength="200" '
            'placeholder="Оставьте пустым, чтобы не выгружать"></label>'
            '<p class="panel-desc">Необязательно. В A2:B выгружаются только артикулы с ненулевым числом активных FBS-заказов.</p>'
            '<div class="export-marketplace-actions">'
            f'<button class="btn-secondary" type="button" data-export-scope data-marketplace="{marketplace}" '
            'data-export-kind="stocks">Выгрузить стоки</button>'
            f'<button class="btn-secondary" type="button" data-export-scope data-marketplace="{marketplace}" '
            'data-export-kind="fbs_orders">Выгрузить FBS-заказы</button>'
            "</div>"
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
        '<label><span>Периодичность</span><select class="integration-select" name="schedule_kind" '
        'data-schedule-kind><option value="daily"'
        f'{daily_selected}>Каждый день</option><option value="weekly"{weekly_selected}>Раз в неделю</option></select></label>'
        '<label data-weekday-field><span>День недели</span><select class="integration-select" name="weekday">'
        f"{_render_weekdays(settings.weekday)}</select></label>"
        '<label><span>Время (Москва)</span><input class="input-control" type="time" name="run_time" '
        f'value="{_input(settings.run_time)}" required></label></div>'
        + combined_store_hint
        + '<div class="integration-export-marketplaces">'
        + "".join(marketplace_sections)
        + "</div>"
        + f'<p class="export-status {status_class}" data-export-status role="status" aria-live="polite">{html.escape(status_text)}</p>'
        '<p class="integration-hint">Ручная выгрузка использует сохранённые настройки. После изменений сначала нажмите «Сохранить настройки».</p>'
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
        targets.append(
            ExportTarget(
                marketplace=marketplace,
                metric="fbs_orders",
                sheet_name=_value(form, f"{prefix}_fbs_orders_sheet_name"),
                key_column_name=stock_sheet_export.ORDER_EXPORT_HEADERS[0],
                value_column_name=stock_sheet_export.ORDER_EXPORT_HEADERS[1],
            )
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
    return RedirectResponse("/admin/integrations?tab=google", status_code=303)


async def render_google_export() -> str:
    """Embed one shared export editor for every project in the integrations page."""
    await run_in_threadpool(project_sheet_export.ensure_defaults)
    settings = await run_in_threadpool(project_sheet_export.get_settings)
    return fill_template(
        "integrations/google-export.html",
        export_form=_render_project_export(settings),
        service_account_email=html.escape(google_service_account_email()),
        week_update=await run_in_threadpool(_render_week_update),
    )


def _render_project_export(settings: ProjectSheetExportSettings) -> str:
    saved = bool(settings.updated_at and any(target.spreadsheet_url.strip() for target in settings.targets))
    last_error = project_sheet_export.current_error(settings)
    status_parts = []
    if settings.last_success_at:
        status_parts.append(f"Последняя успешная выгрузка: {format_dt(settings.last_success_at)}.")
    if last_error:
        status_parts.append(f"Ошибка {format_dt(settings.last_attempt_at)}: {last_error}")
    status_text = " ".join(status_parts) or (
        "Выгрузка ещё не запускалась" if saved else "Сохраните общие настройки перед первой выгрузкой"
    )
    marketplace_sections = []
    for marketplace in PROJECT_MARKETPLACES:
        prefix = MARKETPLACE_FORM_PREFIXES[marketplace]
        target = settings.target(marketplace)
        has_url = bool(target.spreadsheet_url.strip())
        stock_disabled = "" if saved and has_url and target.stock_sheet_name else " disabled"
        orders_disabled = "" if saved and has_url and target.orders_sheet_name else " disabled"
        marketplace_sections.append(
            '<section class="export-marketplace">'
            '<div class="export-marketplace-head"><div>'
            f"<h3>{html.escape(MARKETPLACE_LABELS[marketplace])}</h3></div></div>"
            '<label class="export-url-field"><span>Ссылка на Google Таблицу площадки</span>'
            f'<input class="input-control" type="url" name="{prefix}_spreadsheet_url" '
            f'value="{_input(target.spreadsheet_url)}" placeholder="https://docs.google.com/spreadsheets/d/…"></label>'
            '<label class="export-url-field"><span>Лист остатков</span>'
            f'<input class="input-control" name="{prefix}_sheet_name" '
            f'value="{_input(target.stock_sheet_name)}" maxlength="100" '
            'placeholder="Название листа остатков"></label>'
            '<label class="export-url-field"><span>Лист заказов FBS за 30 дней</span>'
            f'<input class="input-control" name="{prefix}_fbs_orders_sheet_name" '
            f'value="{_input(target.orders_sheet_name)}" maxlength="100" '
            'placeholder="Название листа заказов"></label>'
            '<p class="panel-desc">Пустое название листа отключает эту выгрузку. '
            "Для остатков и заказов каждой площадки укажите разные листы.</p>"
            '<div class="export-marketplace-actions">'
            f'<button class="btn-secondary" type="button" data-export-scope data-marketplace="{marketplace}" '
            f'data-url-field="{prefix}_spreadsheet_url" data-export-kind="stocks" '
            f'data-sheet-field="{prefix}_sheet_name"{stock_disabled}>Выгрузить остатки</button>'
            f'<button class="btn-secondary" type="button" data-export-scope data-marketplace="{marketplace}" '
            f'data-url-field="{prefix}_spreadsheet_url" data-export-kind="fbs_orders" '
            f'data-sheet-field="{prefix}_fbs_orders_sheet_name"{orders_disabled}>'
            "Выгрузить заказы</button></div></section>"
        )
    projects = "".join(
        f'<span class="export-project-chip">{html.escape(store.name)}</span>' for store in STORES.values()
    )
    has_targets = any(
        target.spreadsheet_url.strip() and (target.stock_sheet_name or target.orders_sheet_name)
        for target in settings.targets
    )
    return (
        '<form class="panel export-project-form" data-export-form '
        f'data-saved="{str(saved).lower()}">'
        '<div class="export-card-head"><div class="export-store-title"><div>'
        "<small>ВЫГРУЗКА ПО ПЛОЩАДКАМ</small><h2>Все 7 проектов на каждой площадке</h2></div></div>"
        '<label class="export-enabled"><input type="checkbox" name="enabled" value="1"'
        f"{' checked' if settings.enabled else ''}><span>Автовыгрузка включена</span></label></div>"
        '<div class="export-projects" aria-label="Проекты в выгрузке">'
        + projects
        + '</div><p class="integration-hint">Для каждой площадки укажите отдельный файл Google Таблиц. '
        "В каждый файл выгружаются все 7 проектов с названием проекта у каждого товара. "
        "Одинаковые артикулы разных проектов остаются отдельными строками, в том числе ROCKKIDDO и TOYKA.</p>"
        '<div class="export-schedule-grid">'
        '<label><span>Периодичность</span><select class="integration-select" name="schedule_kind" data-schedule-kind>'
        f'<option value="daily"{" selected" if settings.schedule_kind == "daily" else ""}>Каждый день</option>'
        f'<option value="weekly"{" selected" if settings.schedule_kind == "weekly" else ""}>Раз в неделю</option>'
        "</select></label><label data-weekday-field><span>День недели</span>"
        '<select class="integration-select" name="weekday">'
        f"{_render_weekdays(settings.weekday)}</select></label><label><span>Время (Москва)</span>"
        f'<input class="input-control" type="time" name="run_time" value="{_input(settings.run_time)}" required>'
        '</label></div><div class="integration-export-marketplaces">'
        + "".join(marketplace_sections)
        + '</div><p class="integration-hint">В остатках A — ПРОЕКТ, B:J — основные показатели, '
        "с K — детализация по ФФ. В заказах A — ПРОЕКТ, B — артикул, C — количество. "
        "Заказы считаются за 30 завершённых дней по Москве, без сегодняшнего дня.</p>"
        '<p class="integration-hint">При устаревшем снимке поставок или ошибке обновления используются '
        "последние подтверждённые значения с предупреждением о давности. Если подтверждённых данных нет, "
        "«В пути на склады МП» и ТОТАЛ соответствующих товаров остаются пустыми.</p>"
        f'<p class="export-status {"export-status--error" if last_error else "export-status--ok"}" '
        f'data-export-status role="status" aria-live="polite">{html.escape(status_text)}</p>'
        '<p class="integration-hint" data-export-save-hint>Ручная выгрузка использует сохранённые настройки. '
        "После изменений сначала нажмите «Сохранить настройки».</p>"
        '<div class="export-actions"><button class="btn-primary" type="submit">Сохранить настройки</button>'
        '<button class="btn-secondary" type="button" data-export-now'
        f"{'' if saved and has_targets else ' disabled'}>Выгрузить всё сейчас</button></div></form>"
    )


def _project_settings_from_form(form, existing: ProjectSheetExportSettings) -> ProjectSheetExportSettings:
    try:
        weekday = int(_value(form, "weekday") or 0)
    except ValueError as error:
        raise ValueError("Некорректный день недели") from error
    return replace(
        existing,
        enabled=_value(form, "enabled") == "1",
        schedule_kind=_value(form, "schedule_kind"),
        weekday=weekday,
        run_time=_value(form, "run_time"),
        updated_at=datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
        targets=tuple(
            MarketplaceExportTarget(
                marketplace=marketplace,
                spreadsheet_url=_value(form, f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_spreadsheet_url"),
                stock_sheet_name=_value(form, f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_sheet_name"),
                orders_sheet_name=_value(
                    form, f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_fbs_orders_sheet_name"
                ),
            )
            for marketplace in PROJECT_MARKETPLACES
        ),
    )


def google_service_account_email() -> str:
    from app.ff_import import google_service_account

    return google_service_account.get_service_account_email()


def _week_payload() -> dict:
    settings = week_repository.get_settings()
    now = datetime.now(MOSCOW_TIMEZONE)
    next_run = google_week_update.next_run_at(settings, now)
    if not settings.enabled:
        schedule_text = "Автообновление выключено"
    elif not app_settings.background_sync_enabled:
        schedule_text = "Фоновые задачи приложения отключены. Доступен ручной запуск"
    else:
        schedule_text = f"Ближайший запуск: {format_dt(next_run.isoformat())}"
    status = "Обновление ещё не запускалось"
    if settings.last_success_at:
        status = f"Последнее обновление: {format_dt(settings.last_success_at)} — {settings.last_value}"
    if settings.last_error:
        status += f". Ошибка {format_dt(settings.last_attempt_at)}: {settings.last_error}"
    search = week_repository.get_search_state()
    search_next = google_week_update.next_run_at(google_week_search.schedule_settings(settings, search), now)
    if not search.enabled:
        search_schedule = "Поиск по расписанию выключен"
    elif not app_settings.background_sync_enabled:
        search_schedule = "Фоновые задачи отключены. Поиск доступен по кнопке"
    else:
        if google_week_update.pending_run(settings, now) and next_run:
            search_next = max(search_next, next_run)
        search_schedule = f"Ближайший поиск: {format_dt(search_next.isoformat())}"
        if settings.enabled:
            search_schedule += ". После успешного обновления недели"
    result = search.result
    stale = bool(result and not google_week_search.result_matches_settings(result, settings))
    search_status = "Поиск ещё не запускался"
    if search.last_success_at:
        search_status = f"Последний успешный поиск: {format_dt(search.last_success_at)}"
    if stale:
        if result.get("matching_key") != google_week_search.MATCHING_KEY:
            search_status += ". Ключ сопоставления изменён на ARTICLE + Проект — выполните новый поиск"
        else:
            search_status += ". Таблица, лист или ячейки изменены — выполните новый поиск"
    if search.last_error:
        search_status += f". Ошибка {format_dt(search.last_attempt_at)}: {search.last_error}"
    sales = week_repository.get_sales_state()
    sales_next = google_week_update.next_run_at(google_week_search.schedule_settings(settings, sales), now)
    if not sales.enabled:
        sales_schedule = "Выгрузка заказов по расписанию выключена"
    elif not app_settings.background_sync_enabled:
        sales_schedule = "Фоновые задачи отключены. Выгрузка доступна по кнопке"
    else:
        if google_week_update.pending_run(settings, now) and next_run:
            sales_next = max(sales_next, next_run)
        sales_schedule = f"Ближайшая выгрузка: {format_dt(sales_next.isoformat())}"
        if settings.enabled:
            sales_schedule += ". После успешного обновления недели"
    sales_result = sales.result
    sales_stale = bool(
        sales_result and not google_week_search.result_matches_settings(sales_result, settings)
    )
    sales_status = "Выгрузка заказов ещё не запускалась"
    if sales_result:
        sales_status = f"Последняя выгрузка: {format_dt(sales_result['processed_at'])}"
        if not sales_result["complete"]:
            sales_status += ". Выполнена с замечаниями"
    if sales_stale:
        if sales_result.get("matching_key") != google_week_search.MATCHING_KEY:
            sales_status += ". Ключ сопоставления изменён на ARTICLE + Проект — выполните новую выгрузку"
        else:
            sales_status += ". Настройки назначения изменены — выполните новую выгрузку"
    if sales.last_error:
        sales_status += f". Ошибка {format_dt(sales.last_attempt_at)}: {sales.last_error}"
    stock = week_repository.get_stock_state()
    stock_next = google_week_update.next_run_at(google_week_search.schedule_settings(settings, stock), now)
    if not stock.enabled:
        stock_schedule = "Выгрузка остатков FBO по расписанию выключена"
    elif not app_settings.background_sync_enabled:
        stock_schedule = "Фоновые задачи отключены. Выгрузка доступна по кнопке"
    else:
        if google_week_update.pending_run(settings, now) and next_run:
            stock_next = max(stock_next, next_run)
        stock_schedule = f"Ближайшая выгрузка FBO: {format_dt(stock_next.isoformat())}"
        if settings.enabled:
            stock_schedule += ". После успешного обновления недели"
    stock_result = stock.result
    stock_stale = bool(
        stock_result and not google_week_stock.result_matches_settings(stock_result, settings, stock)
    )
    stock_status = "Выгрузка остатков FBO ещё не запускалась"
    if stock_result:
        stock_status = f"Последняя выгрузка FBO: {format_dt(stock_result['processed_at'])}"
        if not stock_result["complete"]:
            stock_status += ". Выполнена с замечаниями"
    if stock_stale:
        if stock_result.get("matching_key") != google_week_search.MATCHING_KEY:
            stock_status += ". Ключ сопоставления изменён на ARTICLE + Проект — выполните новую выгрузку"
        else:
            stock_status += ". Настройки назначения изменены — выполните новую выгрузку"
    if stock.last_error:
        stock_status += f". Ошибка {format_dt(stock.last_attempt_at)}: {stock.last_error}"
    return {
        "ok": True,
        "value": google_week_update.last_completed_week(now),
        "schedule_text": schedule_text,
        "status_text": status,
        "has_error": bool(settings.last_error),
        "saved": bool(settings.updated_at),
        "search_enabled": search.enabled,
        "search_schedule_text": search_schedule,
        "search_status_text": search_status,
        "search_has_error": bool(search.last_error),
        "search_html": render_week_search_result(None if stale else result),
        "sales_enabled": sales.enabled,
        "sales_schedule_text": sales_schedule,
        "sales_status_text": sales_status,
        "sales_has_error": bool(sales.last_error or (sales_result and not sales_result["complete"])),
        "sales_html": render_week_sales_result(None if sales_stale else sales_result),
        "stock_enabled": stock.enabled,
        "stock_sheet_name": stock.sheet_name,
        "stock_schedule_text": stock_schedule,
        "stock_status_text": stock_status,
        "stock_has_error": bool(stock.last_error or (stock_result and not stock_result["complete"])),
        "stock_html": render_week_sales_result(
            None if stock_stale else stock_result,
            button_label="Выгрузить остатки FBO",
            data_label="истории FBO",
            backup_folder="google-week-stock",
        ),
    }


def _render_week_update() -> str:
    settings = week_repository.get_settings()
    state = _week_payload()
    return fill_template(
        "integrations/google-week-update.html",
        spreadsheet_url=_input(settings.spreadsheet_url),
        sheet_name=_input(settings.sheet_name),
        cells=_input(", ".join(settings.cells)),
        enabled_checked=" checked" if settings.enabled else "",
        weekdays=_render_weekdays(settings.weekday),
        run_time=_input(settings.run_time),
        value=html.escape(state["value"]),
        schedule_text=html.escape(state["schedule_text"]),
        status_text=html.escape(state["status_text"]),
        status_class="export-status--error" if state["has_error"] else "",
        run_disabled="" if state["saved"] else " disabled",
        search_enabled_checked=" checked" if state["search_enabled"] else "",
        search_schedule_text=html.escape(state["search_schedule_text"]),
        search_status_text=html.escape(state["search_status_text"]),
        search_status_class="export-status--error" if state["search_has_error"] else "",
        search_results=state["search_html"],
        sales_enabled_checked=" checked" if state["sales_enabled"] else "",
        sales_schedule_text=html.escape(state["sales_schedule_text"]),
        sales_status_text=html.escape(state["sales_status_text"]),
        sales_status_class="export-status--error" if state["sales_has_error"] else "",
        sales_results=state["sales_html"],
        stock_enabled_checked=" checked" if state["stock_enabled"] else "",
        stock_sheet_name=_input(state["stock_sheet_name"]),
        stock_schedule_text=html.escape(state["stock_schedule_text"]),
        stock_status_text=html.escape(state["stock_status_text"]),
        stock_status_class="export-status--error" if state["stock_has_error"] else "",
        stock_results=state["stock_html"],
    )


@router.get("/admin/google-week-update")
async def google_week_update_status(request: Request):
    _require_superadmin(request)
    return JSONResponse(await run_in_threadpool(_week_payload), headers={"Cache-Control": "no-store"})


@router.post("/admin/google-week-update")
async def save_google_week_update(request: Request):
    _require_superadmin(request)
    form = await request.form()
    try:
        try:
            weekday = int(_value(form, "weekday"))
        except ValueError as error:
            raise ValueError("Выберите день недели") from error
        settings = week_repository.WeekUpdateSettings(
            enabled=_value(form, "enabled") == "1",
            spreadsheet_url=_value(form, "spreadsheet_url"),
            sheet_name=_value(form, "sheet_name"),
            cells=google_week_update.parse_cells(_value(form, "cells")),
            weekday=weekday,
            run_time=_value(form, "run_time"),
        )
        await run_in_threadpool(
            google_week_update.save_settings,
            settings,
            search_enabled=_value(form, "search_enabled") == "1",
            sales_enabled=_value(form, "sales_enabled") == "1",
            stock_enabled=_value(form, "stock_enabled") == "1" if "stock_sheet_name" in form else None,
            stock_sheet_name=_value(form, "stock_sheet_name") if "stock_sheet_name" in form else None,
        )
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
    except SyncJobBusyError:
        return JSONResponse(
            {"ok": False, "error": "Обновление уже выполняется. Сохраните настройки после его завершения"},
            status_code=409,
        )
    actor = request.state.user
    await run_in_threadpool(
        db.log_action,
        actor.id,
        actor.full_name,
        "Изменены настройки обновления недели",
        f"{settings.sheet_name}: {', '.join(settings.cells)}; день {settings.weekday + 1}, {settings.run_time} МСК",
        datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
    )
    return JSONResponse(await run_in_threadpool(_week_payload))


@router.post("/admin/google-week-update/run")
async def run_google_week_update(request: Request):
    _require_superadmin(request)
    try:
        report = await run_in_threadpool(
            run_tracked,
            google_week_update.JOB_NAME,
            "manual",
            google_week_update.run_now,
        )
    except SyncJobBusyError:
        return JSONResponse({"ok": False, "error": "Обновление уже выполняется"}, status_code=409)
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
    state = await run_in_threadpool(_week_payload)
    return JSONResponse({**state, "report": report})


@router.post("/admin/google-week-update/search")
async def search_google_week_headers(request: Request):
    _require_superadmin(request)
    try:
        result = await run_in_threadpool(
            run_tracked,
            google_week_update.JOB_NAME,
            "manual",
            google_week_search.run_now,
        )
    except SyncJobBusyError:
        return JSONResponse({"ok": False, "error": "Обновление или поиск уже выполняется"}, status_code=409)
    except ValueError as error:
        state = await run_in_threadpool(_week_payload)
        return JSONResponse({**state, "ok": False, "error": str(error)}, status_code=400)
    state = await run_in_threadpool(_week_payload)
    return JSONResponse({**state, "search_result": result})


@router.post("/admin/google-week-update/sales")
async def export_google_week_sales(request: Request):
    _require_superadmin(request)
    try:
        result = await run_in_threadpool(
            run_tracked,
            google_week_update.JOB_NAME,
            "manual",
            google_week_sales.run_now,
        )
    except SyncJobBusyError:
        return JSONResponse(
            {"ok": False, "error": "Обновление, поиск или выгрузка уже выполняется"}, status_code=409
        )
    except ValueError as error:
        state = await run_in_threadpool(_week_payload)
        return JSONResponse({**state, "ok": False, "error": str(error)}, status_code=400)
    state = await run_in_threadpool(_week_payload)
    return JSONResponse({**state, "sales_result": result})


@router.post("/admin/google-week-update/stock")
async def export_google_week_stock(request: Request):
    _require_superadmin(request)
    try:
        result = await run_in_threadpool(
            run_tracked,
            google_week_update.JOB_NAME,
            "manual",
            google_week_stock.run_now,
        )
    except SyncJobBusyError:
        return JSONResponse(
            {"ok": False, "error": "Обновление, поиск или выгрузка уже выполняется"}, status_code=409
        )
    except ValueError as error:
        state = await run_in_threadpool(_week_payload)
        return JSONResponse({**state, "ok": False, "error": str(error)}, status_code=400)
    state = await run_in_threadpool(_week_payload)
    return JSONResponse({**state, "stock_result": result})


@router.post("/admin/google-export/settings")
async def save_project_export_settings(request: Request):
    _require_superadmin(request)
    form = await request.form()
    existing = await run_in_threadpool(project_sheet_export.get_settings)
    try:
        settings = _project_settings_from_form(form, existing)
        await run_in_threadpool(project_sheet_export.save_settings, settings)
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
    except SyncJobBusyError:
        return JSONResponse(
            {"ok": False, "error": "Выгрузка уже выполняется. Сохраните настройки после её завершения"},
            status_code=409,
        )
    actor = request.state.user
    await run_in_threadpool(
        db.log_action,
        actor.id,
        actor.full_name,
        "Изменены общие настройки выгрузки",
        f"Все проекты: {settings.schedule_kind} {settings.run_time} МСК",
        datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
    )
    return JSONResponse({"ok": True})


@router.post("/admin/google-export/run")
async def run_project_export(request: Request):
    _require_superadmin(request)
    form = await request.form()
    marketplace = _value(form, "marketplace") or None
    export_kind = _value(form, "export_kind") or None
    if (marketplace is None) != (export_kind is None):
        return JSONResponse(
            {"ok": False, "error": "Укажите маркетплейс и тип выгрузки вместе"},
            status_code=400,
        )
    if marketplace is not None and marketplace not in PROJECT_MARKETPLACES:
        return JSONResponse({"ok": False, "error": "Неизвестный маркетплейс"}, status_code=400)
    if export_kind is not None and export_kind not in stock_sheet_export.EXPORT_KINDS:
        return JSONResponse({"ok": False, "error": "Неизвестный тип выгрузки"}, status_code=400)
    try:
        report = await run_in_threadpool(
            run_tracked,
            "stock_sheet_export",
            "manual",
            lambda: project_sheet_export.run_export(
                marketplace=marketplace,
                export_kind=export_kind,
            ),
        )
    except SyncJobBusyError:
        return JSONResponse({"ok": False, "error": "Выгрузка уже выполняется"}, status_code=409)
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
    except Exception as error:
        return JSONResponse(
            {"ok": False, "error": f"{type(error).__name__}: {error}"},
            status_code=502,
        )
    successful_at = report.get("last_success_at")
    return JSONResponse(
        {
            "ok": True,
            "report": report,
            "last_success_text": (
                f"Последняя успешная выгрузка: {format_dt(successful_at)}" if successful_at else None
            ),
        }
    )


def _retired_store_export_response() -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error": "Выгрузка по магазинам заменена общей выгрузкой всех проектов. "
            "Обновите страницу и сохраните ссылки на таблицы и листы отдельно для каждой площадки.",
        },
        status_code=410,
    )


@router.post("/admin/google-export/{store_slug}")
async def save_google_export_settings(request: Request, store_slug: str):
    _require_superadmin(request)
    return _retired_store_export_response()


@router.post("/admin/google-export/{store_slug}/run")
async def run_google_export(request: Request, store_slug: str):
    _require_superadmin(request)
    return _retired_store_export_response()
