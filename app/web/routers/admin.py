import html
from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app import db
from app.access.authentication import require_active_user
from app.core.formatting import format_dt
from app.dto.identity import (
    ActivityCommand,
    ActivityLog,
    ActivityLogQuery,
    AuditedCreateUser,
    AuditedUserMutation,
    CreateUserCommand,
    CreateUserForm,
    LoginQuery,
    PasswordHashRequest,
    User,
    UserId,
    UserMutationKind,
)
from app.ff_import import export as ff_export
from app.jobs.tracking import run_tracked
from app.ozon import catalog as ozon_catalog
from app.ozon import sync as ozon_sync
from app.wb import catalog as wb_catalog
from app.wb import sync as wb_sync
from app.web.access import accessible_store_slugs
from app.web.dependencies import ContainerDependency, IdentityServiceDependency
from app.web.downloads import _download_headers
from app.web.templating import fill_template, render_page
from app.yandex import catalog as ya_catalog
from app.yandex import sync as ya_sync

router = APIRouter()


async def _target(identities, user_id):
    if user_id < 1:
        raise HTTPException(404, "Сотрудник не найден")
    target = await run_in_threadpool(identities.get_user, UserId(user_id))
    if target is None:
        raise HTTPException(404, "Сотрудник не найден")
    return target


async def _mutate(identities, command):
    try:
        await run_in_threadpool(identities.mutate_user, command)
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
    except IntegrityError:
        return JSONResponse(
            {"ok": False, "error": "Сотрудник связан с рабочими данными. Заблокируйте учётную запись."},
            status_code=409,
        )
    return JSONResponse({"ok": True})


@router.post("/admin/users/{user_id}/delete")
async def admin_delete_user(request: Request, user_id: int, identities: IdentityServiceDependency):
    actor = require_active_user(request)
    target = await _target(identities, user_id)
    return await _mutate(
        identities,
        AuditedUserMutation(
            kind=UserMutationKind.DELETE,
            user_id=user_id,
            activity=_activity(actor, "Удалён сотрудник", f"{target.full_name} ({target.login})"),
        ),
    )


@router.post("/admin/users/{user_id}/toggle-active")
async def admin_toggle_active(request: Request, user_id: int, identities: IdentityServiceDependency):
    actor = require_active_user(request)
    target = await _target(identities, user_id)
    return await _mutate(
        identities,
        AuditedUserMutation(
            kind=UserMutationKind.ACTIVE,
            user_id=user_id,
            is_active=not target.is_active,
            activity=_activity(
                actor,
                "Заблокирован сотрудник" if target.is_active else "Активирован сотрудник",
                target.full_name,
            ),
        ),
    )


@router.post("/admin/users/{user_id}/reset-password")
async def admin_reset_password(
    request: Request,
    user_id: int,
    identities: IdentityServiceDependency,
    container: ContainerDependency,
    password: str = Form(..., min_length=8, max_length=1024),
):
    actor = require_active_user(request)
    target = await _target(identities, user_id)
    password_hash = await run_in_threadpool(container.passwords.hash, PasswordHashRequest(password=password))
    return await _mutate(
        identities,
        AuditedUserMutation(
            kind=UserMutationKind.PASSWORD,
            user_id=user_id,
            password_hash=password_hash.root,
            activity=_activity(actor, "Сброшен пароль", target.full_name),
        ),
    )


@router.post("/admin/users")
async def admin_create_user(
    request: Request, identities: IdentityServiceDependency, container: ContainerDependency
):
    actor = require_active_user(request)
    form = await request.form()
    try:
        payload = CreateUserForm.model_validate(dict(form))
    except ValidationError as error:
        field = str(error.errors()[0]["loc"][0])
        message = "Пароль: от 8 до 1024 символов" if field == "password" else f"Проверьте поле {field}"
        return JSONResponse({"ok": False, "field": field, "error": message}, status_code=400)
    if await run_in_threadpool(identities.get_user_by_login, LoginQuery(login=payload.login)):
        return JSONResponse(
            {"ok": False, "field": "login", "error": "Такой логин уже занят"}, status_code=400
        )
    password_hash = await run_in_threadpool(
        container.passwords.hash, PasswordHashRequest(password=payload.password)
    )
    command = AuditedCreateUser(
        user=CreateUserCommand(
            full_name=payload.full_name,
            google_email=payload.google_email,
            login=payload.login,
            password_hash=password_hash.root,
            created_at=datetime.now(UTC),
        ),
        activity=_activity(actor, "Создан сотрудник", f"{payload.full_name} ({payload.login})"),
    )
    try:
        await run_in_threadpool(identities.create_user_with_activity, command)
    except IntegrityError:
        return JSONResponse(
            {"ok": False, "field": "login", "error": "Такой логин уже занят"}, status_code=400
        )
    return JSONResponse({"ok": True})


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, identities: IdentityServiceDependency):
    actor = require_active_user(request)
    users = await run_in_threadpool(identities.list_users)
    activity = await run_in_threadpool(identities.get_activity, ActivityLogQuery())
    rows = []
    for user in users.root:
        disabled = " disabled" if user.id == actor.id else ""
        status = "Активен" if user.is_active else "Заблокирован"
        toggle = "Заблокировать" if user.is_active else "Активировать"
        rows.append(
            f'<tr data-user-id="{user.id}"><td>{html.escape(user.full_name)}</td>'
            f"<td>{html.escape(user.login)}</td><td>{html.escape(user.google_email)}</td><td>{status}</td>"
            f'<td><button type="button" data-action="toggle-active"{disabled}>{toggle}</button> '
            '<button type="button" data-action="reset-password">Сменить пароль</button> '
            f'<button type="button" data-action="delete"{disabled}>Удалить</button></td></tr>'
        )
    content = fill_template(
        "admin/index.html",
        user_rows="".join(rows),
        users_total=str(len(users.root)),
        users_active=str(sum(user.is_active for user in users.root)),
        log_rows=render_log_rows(actor, activity),
    )
    return render_page("CheckStock — Админ-панель", "admin", content, actor, "content--admin")


def _activity(actor: User, action: str, details: str) -> ActivityCommand:
    return ActivityCommand(
        user_id=actor.id,
        user_name=actor.full_name,
        action=action,
        details=details,
        created_at=datetime.now(UTC),
    )


def render_log_rows(user: User, activity: ActivityLog) -> str:
    entries = activity.root
    if not entries:
        return '<tr class="empty-row"><td colspan="5">Пока пусто</td></tr>'
    rows = []
    for e in entries:
        operation_id = e.operation_id
        file_cell = (
            f'<a class="log-download" href="/admin/operations/{operation_id}/xlsx" '
            f'title="Скачать файл операции">xlsx</a>'
            if operation_id
            else '<span class="u-note">—</span>'
        )
        rows.append(
            "<tr>"
            f'<td data-label="Когда">{html.escape(format_dt(e.created_at.isoformat()))}</td>'
            f'<td data-label="Сотрудник">{html.escape(e.user_name)}</td>'
            f'<td data-label="Действие">{html.escape(e.action)}</td>'
            f'<td data-label="Подробности">{html.escape(e.details or "")}</td>'
            f'<td data-label="Файл">{file_cell}</td>'
            "</tr>"
        )
    return "".join(rows)


@router.get("/admin/operations/{operation_id}/xlsx")
async def download_operation(request: Request, operation_id: int):

    require_active_user(request)

    try:
        content, filename = await run_in_threadpool(ff_export.build_operation_xlsx, operation_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Операция не найдена") from error
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


@router.post("/admin/sync-stock")
async def sync_stock(request: Request):
    actor = require_active_user(request)
    store_slugs = accessible_store_slugs(actor)

    def sync_catalogs():
        return (
            wb_catalog.sync_all(store_slugs),
            ozon_catalog.sync_all(store_slugs),
            ya_catalog.sync_all(store_slugs),
        )

    def sync_stocks():
        return (
            wb_sync.sync_all(store_slugs),
            ozon_sync.sync_all(store_slugs),
            ya_sync.sync_all(store_slugs),
        )

    wb_catalog_report, catalog_report, ya_catalog_report = await run_in_threadpool(
        run_tracked, "catalog_sync", "manual", sync_catalogs
    )
    report, ozon_report, ya_report = await run_in_threadpool(run_tracked, "stock_sync", "manual", sync_stocks)
    for slug, entry in wb_catalog_report.items():
        if slug in report:
            report[slug]["wb_catalog"] = entry

    for slug, entry in ozon_report.items():
        if slug in report:
            report[slug]["ozon"] = entry.get("ozon")
            report[slug]["ozon_token"] = entry.get("token")
            report[slug]["ozon_catalog"] = catalog_report.get(slug)

    for slug, entry in ya_report.items():
        if slug in report:
            report[slug]["yandex"] = entry.get("yandex")
            report[slug]["yandex_token"] = entry.get("token")
            report[slug]["yandex_catalog"] = ya_catalog_report.get(slug)

    last_sync = await run_in_threadpool(db.get_last_sync_at)
    return JSONResponse({"report": report, "last_sync": format_dt(last_sync)})
