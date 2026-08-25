import html
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError

from app import auth, db
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
    PermissionName,
    Role,
    SectionAccessLevel,
    SectionName,
    User,
    UserCollection,
    UserCountQuery,
    UserId,
    UserMutationKind,
)
from app.ff_import import export as ff_export
from app.formatting import format_dt
from app.identity_policy import ROLE_LABELS
from app.ozon import catalog as ozon_catalog
from app.ozon import sync as ozon_sync
from app.section_access import SECTION_LABELS, access_level
from app.stores import STORES
from app.wb import catalog as wb_catalog
from app.wb import sync as wb_sync
from app.web.access import accessible_store_slugs, has_store_access
from app.web.dependencies import ContainerDependency, IdentityServiceDependency
from app.web.downloads import _download_headers
from app.web.templating import fill_template, render_page
from app.yandex import catalog as ya_catalog
from app.yandex import sync as ya_sync

router = APIRouter()


def _activity(actor: User, action: str, details: str) -> ActivityCommand:
    return ActivityCommand(
        user_id=actor.id,
        user_name=actor.full_name,
        action=action,
        details=details,
        created_at=datetime.now(UTC),
    )


def _guard_user_action(actor: User, target: User | None, what: str) -> str | None:

    if target is None:
        return "сотрудник не найден"
    if target.id == actor.id:
        return f"нельзя {what} самого себя"
    if not can_manage_user(actor, target):
        return (
            f"{what} можно только для сотрудников — администраторов и "
            "суперадминистраторов может изменять только суперадминистратор"
        )
    return None


@router.post("/admin/users/{user_id}/delete")
async def admin_delete_user(
    request: Request,
    user_id: int,
    identities: IdentityServiceDependency,
):
    actor = request.state.user
    if not auth.can_manage_users(actor):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    target = await run_in_threadpool(identities.get_user, UserId(user_id))
    error = _guard_user_action(actor, target, "удалить")
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)

    if target.role is Role.SUPERADMIN:
        left = await run_in_threadpool(
            identities.count_superadmins,
            UserCountQuery(exclude_user_id=user_id),
        )
        if left.root == 0:
            return JSONResponse(
                {"ok": False, "error": "это последний суперадминистратор — сначала назначьте другого"},
                status_code=400,
            )

    command = AuditedUserMutation(
        kind=UserMutationKind.DELETE,
        user_id=user_id,
        activity=_activity(
            actor,
            "Удалён сотрудник",
            f"{target.full_name} ({target.login})",
        ),
    )
    await run_in_threadpool(identities.mutate_user, command)
    return JSONResponse({"ok": True})


@router.post("/admin/users/{user_id}/reset-password")
async def admin_reset_password(
    request: Request,
    user_id: int,
    identities: IdentityServiceDependency,
    container: ContainerDependency,
    password: str = Form(..., min_length=8, max_length=1024),
):

    actor = request.state.user
    if not auth.has_role(actor, "admin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    target = await run_in_threadpool(identities.get_user, UserId(user_id))
    error = _guard_user_action(actor, target, "сбросить пароль")
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)
    password_hash = await run_in_threadpool(
        container.passwords.hash,
        PasswordHashRequest(password=password),
    )
    command = AuditedUserMutation(
        kind=UserMutationKind.PASSWORD,
        user_id=user_id,
        password_hash=password_hash.root,
        activity=_activity(
            actor,
            "Сброшен пароль сотрудника",
            f"{target.full_name} ({target.login})",
        ),
    )
    await run_in_threadpool(identities.mutate_user, command)
    return JSONResponse({"ok": True})


@router.post("/admin/users/{user_id}/toggle-stock-edit")
async def admin_toggle_stock_edit(
    request: Request,
    user_id: int,
    identities: IdentityServiceDependency,
):

    actor = request.state.user
    target = await run_in_threadpool(identities.get_user, UserId(user_id))

    error = _guard_user_action(actor, target, "менять права")
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=403)

    allowed = not auth.can_edit_stock(target)

    command = AuditedUserMutation(
        kind=UserMutationKind.PERMISSION,
        user_id=user_id,
        permission=PermissionName.EDIT_STOCK,
        allowed=allowed,
        activity=_activity(
            actor,
            "Разрешены изменения остатков" if allowed else "Запрещены изменения остатков",
            target.full_name,
        ),
    )
    await run_in_threadpool(identities.mutate_user, command)
    return JSONResponse({"ok": True, "allowed": allowed})


@router.post("/admin/users/{user_id}/toggle-active")
async def admin_toggle_active(
    request: Request,
    user_id: int,
    identities: IdentityServiceDependency,
):

    actor = request.state.user
    if not auth.has_role(actor, "admin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    target = await run_in_threadpool(identities.get_user, UserId(user_id))
    error = _guard_user_action(actor, target, "заблокировать")
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)

    new_state = not target.is_active
    if not new_state and target.role is Role.SUPERADMIN:
        left = await run_in_threadpool(
            identities.count_superadmins,
            UserCountQuery(exclude_user_id=user_id),
        )
        if left.root == 0:
            return JSONResponse(
                {"ok": False, "error": "это последний суперадминистратор — сначала назначьте другого"},
                status_code=400,
            )

    command = AuditedUserMutation(
        kind=UserMutationKind.ACTIVE,
        user_id=user_id,
        is_active=new_state,
        activity=_activity(
            actor,
            "Разблокирован сотрудник" if new_state else "Заблокирован сотрудник",
            f"{target.full_name} ({target.login})",
        ),
    )
    await run_in_threadpool(identities.mutate_user, command)
    return JSONResponse({"ok": True, "is_active": new_state})


@router.post("/admin/users/{user_id}/stores")
async def admin_update_user_stores(
    request: Request,
    user_id: int,
    identities: IdentityServiceDependency,
):
    actor = request.state.user
    if not auth.has_role(actor, "admin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    target = await run_in_threadpool(identities.get_user, UserId(user_id))
    error = _guard_user_action(actor, target, "изменять доступ к кабинетам")
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=403)

    form = await request.form()
    store_slugs = normalize_admin_store_selection(actor, tuple(form.getlist("stores")))
    if not store_slugs:
        return JSONResponse({"ok": False, "error": "выберите хотя бы один кабинет"}, status_code=400)

    labels = ", ".join(STORES[slug].name for slug in store_slugs)
    command = AuditedUserMutation(
        kind=UserMutationKind.STORES,
        user_id=user_id,
        store_slugs=tuple(store_slugs),
        activity=_activity(
            actor,
            "Изменён доступ сотрудника к кабинетам",
            f"{target.full_name}: {labels}",
        ),
    )
    await run_in_threadpool(identities.mutate_user, command)
    return JSONResponse({"ok": True, "stores": store_slugs})


@router.post("/admin/users/{user_id}/role")
async def admin_update_user_role(
    request: Request,
    user_id: int,
    identities: IdentityServiceDependency,
    role: str = Form(...),
):
    actor = request.state.user
    if not auth.has_role(actor, "superadmin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    target = await run_in_threadpool(identities.get_user, UserId(user_id))
    error = _guard_user_action(actor, target, "менять роль")
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)
    try:
        new_role = Role(role)
    except ValueError:
        return JSONResponse({"ok": False, "error": "неизвестная роль"}, status_code=400)
    if target.role is Role.SUPERADMIN and new_role is not Role.SUPERADMIN:
        left = await run_in_threadpool(
            identities.count_superadmins,
            UserCountQuery(exclude_user_id=user_id),
        )
        if left.root == 0:
            return JSONResponse(
                {"ok": False, "error": "это последний суперадминистратор — сначала назначьте другого"},
                status_code=400,
            )
    command = AuditedUserMutation(
        kind=UserMutationKind.ROLE,
        user_id=user_id,
        role=new_role,
        activity=_activity(
            actor,
            "Изменена роль сотрудника",
            f"{target.full_name}: {ROLE_LABELS[target.role]} → {ROLE_LABELS[new_role]}",
        ),
    )
    await run_in_threadpool(identities.mutate_user, command)
    return JSONResponse({"ok": True, "role": new_role.value})


@router.post("/admin/users/{user_id}/sections")
async def admin_update_user_sections(
    request: Request,
    user_id: int,
    identities: IdentityServiceDependency,
):
    actor = request.state.user
    if not auth.has_role(actor, "superadmin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    target = await run_in_threadpool(identities.get_user, UserId(user_id))
    error = _guard_user_action(actor, target, "изменять права доступа по разделам")
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)
    if target.role is Role.SUPERADMIN:
        return JSONResponse(
            {"ok": False, "error": "суперадминистратору всегда предоставлен полный доступ"},
            status_code=400,
        )
    form = await request.form()
    permissions: dict[SectionName, SectionAccessLevel] = {}
    try:
        for section in SectionName:
            permissions[section] = SectionAccessLevel(str(form.get(section.value) or ""))
    except ValueError:
        return JSONResponse({"ok": False, "error": "проверьте права разделов"}, status_code=400)
    details = ", ".join(f"{SECTION_LABELS[section]}: {level.value}" for section, level in permissions.items())
    command = AuditedUserMutation(
        kind=UserMutationKind.SECTIONS,
        user_id=user_id,
        section_access=permissions,
        activity=_activity(
            actor,
            "Изменены права доступа по разделам",
            f"{target.full_name}: {details}",
        ),
    )
    await run_in_threadpool(identities.mutate_user, command)
    return JSONResponse({"ok": True})


def creatable_roles(actor: User) -> tuple[Role, ...]:

    if auth.has_role(actor, "superadmin"):
        return tuple(Role)
    if auth.has_role(actor, "admin"):
        return (Role.USER,)
    return ()


def can_manage_user(actor: User, target: User) -> bool:

    if not auth.can_manage_users(actor):
        return False
    if target.id == actor.id:
        return False
    if auth.has_role(actor, "superadmin"):
        return True
    if auth.has_role(actor, "admin"):
        actor_stores = set(accessible_store_slugs(actor))
        target_stores = set(target.store_slugs or tuple(STORES))
        return target.role is Role.USER and target_stores.issubset(actor_stores)
    return False


def render_role_options(actor: User) -> str:
    return "\n".join(
        f'                    <option value="{role.value}">{html.escape(ROLE_LABELS[role])}</option>'
        for role in creatable_roles(actor)
    )


def render_user_role_options(user: User) -> str:
    return "".join(
        f'<option value="{role.value}"{" selected" if user.role is role else ""}>'
        f"{html.escape(ROLE_LABELS[role])}</option>"
        for role in Role
    )


def assignable_store_slugs(actor: User) -> tuple[str, ...]:
    if auth.has_role(actor, "superadmin"):
        return tuple(STORES)
    return accessible_store_slugs(actor)


def normalize_admin_store_selection(actor: User, raw_slugs: tuple[str, ...]) -> tuple[str, ...]:
    allowed = set(assignable_store_slugs(actor))
    selected = []
    seen = set()
    for slug in raw_slugs:
        key = str(slug or "").strip().lower()
        if key in allowed and key not in seen:
            seen.add(key)
            selected.append(key)
    return tuple(selected)


def render_store_badges(store_slugs: tuple[str, ...]) -> str:
    slugs = [slug for slug in store_slugs if slug in STORES]
    if not slugs:
        return '<span class="u-note">—</span>'
    return "".join(f'<span class="u-store-badge">{html.escape(STORES[slug].name)}</span>' for slug in slugs)


def render_store_checkboxes(
    actor: User,
    selected_slugs: tuple[str, ...] | None = None,
    name: str = "stores",
    disabled: bool = False,
) -> str:
    selected = set(selected_slugs or assignable_store_slugs(actor))
    disabled_attr = " disabled" if disabled else ""
    items = []
    for slug in assignable_store_slugs(actor):
        store = STORES[slug]
        checked = " checked" if slug in selected else ""
        items.append(
            '<label class="u-store-option">'
            f'<input type="checkbox" name="{html.escape(name)}" value="{html.escape(slug)}"{checked}{disabled_attr}>'
            f"<span>{html.escape(store.name)}</span>"
            "</label>"
        )
    return '<div class="u-store-grid">' + "".join(items) + "</div>"


def render_user_rows(actor: User, users: UserCollection) -> str:
    if not users.root:
        return '<tr class="empty-row"><td colspan="8">Пока нет сотрудников</td></tr>'
    rows = []
    for user in users.root:
        active = user.is_active
        status = (
            '<span class="u-status u-status--on">активен</span>'
            if active
            else '<span class="u-status u-status--off">заблокирован</span>'
        )

        can_edit = auth.can_edit_stock(user)
        edit_status = (
            '<span class="u-status u-status--on">разрешены</span>'
            if can_edit
            else '<span class="u-status u-status--off">запрещены</span>'
        )
        if can_manage_user(actor, user):
            store_cell = render_store_checkboxes(actor, user.store_slugs)
            superadmin_controls = ""
            stock_control = (
                f'<button type="button" class="u-act u-act--stock">'
                f"{'Запретить изменения' if can_edit else 'Разрешить изменения'}</button>"
            )
            if auth.has_role(actor, "superadmin"):
                permissions = {section.value: access_level(user, section).value for section in SectionName}
                superadmin_controls = (
                    '<div class="u-role-editor">'
                    f'<select class="select-control u-role-select">{render_user_role_options(user)}</select>'
                    '<button type="button" class="u-act u-act--role">Сохранить роль</button>'
                    "</div>"
                )
                if user.role is not Role.SUPERADMIN:
                    superadmin_controls += (
                        f'<button type="button" class="u-act u-act--sections" data-section-access="'
                        f'{html.escape(json.dumps(permissions, ensure_ascii=False), quote=True)}">'
                        "Права доступа</button>"
                    )
                stock_control = ""
            actions = (
                f'<div class="u-actions" data-user-id="{user.id}" '
                f'data-user-name="{html.escape(user.full_name, quote=True)}" '
                f'data-active="{"1" if active else "0"}" '
                f'data-can-edit="{"1" if can_edit else "0"}">'
                '<button type="button" class="u-act u-act--stores">Сохранить доступ</button>'
                '<button type="button" class="u-act u-act--reset">Сбросить пароль</button>'
                f'<button type="button" class="u-act u-act--toggle">{"Заблокировать" if active else "Разблокировать"}</button>'
                f"{stock_control}"
                f"{superadmin_controls}"
                '<button type="button" class="u-act u-act--delete">Удалить</button>'
                "</div>"
            )
        elif user.id == actor.id:
            store_cell = render_store_badges(user.store_slugs)
            actions = '<span class="u-note">это вы</span>'
        else:
            store_cell = render_store_badges(user.store_slugs)
            actions = '<span class="u-note">—</span>'

        rows.append(
            "<tr>"
            f"<td>{html.escape(user.full_name)}</td>"
            f"<td>{html.escape(user.google_email)}</td>"
            f"<td>{html.escape(user.login)}</td>"
            f"<td>{html.escape(ROLE_LABELS[user.role])}</td>"
            f"<td>{status}</td>"
            f"<td>{edit_status}</td>"
            f"<td>{store_cell}</td>"
            f"<td>{actions}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_log_rows(user: User, activity: ActivityLog) -> str:
    entries = activity.root
    allowed = set(accessible_store_slugs(user))
    if len(allowed) != len(STORES):
        filtered = []
        for entry in entries:
            operation_id = entry.operation_id
            if not operation_id:
                filtered.append(entry)
                continue
            operation = db.get_operation(operation_id)
            if operation and operation.get("store_slug") in allowed:
                filtered.append(entry)
        entries = tuple(filtered)
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


def _format_duration(seconds: int) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин"
    return f"{total} сек"


def _usage_location(section_key: str | None, path: str | None) -> str:
    if section_key in SectionName._value2member_map_:
        return str(SECTION_LABELS.get(SectionName(section_key), section_key))
    if path == "/admin/activity":
        return "Статистика использования"
    if path and path.startswith("/admin"):
        return "Админ-панель"
    if path == "/access-denied":
        return "Нет доступа"
    return "—"


def render_usage_dashboard(data: dict[str, object]) -> str:
    people = data["people"]
    sections = data["sections"]
    sessions = data["sessions"]
    people_rows = []
    for person in people:
        section_label = _usage_location(person["last_section"], person["last_path"])
        status = (
            '<span class="u-status u-status--on">онлайн</span>'
            if person["online"]
            else '<span class="u-status u-status--off">не онлайн</span>'
        )
        people_rows.append(
            "<tr>"
            f'<td>{html.escape(str(person["full_name"]))}<small class="usage-login">'
            f"{html.escape(str(person['login']))}</small></td>"
            f"<td>{status}</td>"
            f"<td>{html.escape(section_label)}</td>"
            f"<td>{html.escape(format_dt(person['last_seen']))}</td>"
            f"<td>{html.escape(_format_duration(person['active_today']))}</td>"
            f"<td>{html.escape(_format_duration(person['active_period']))}</td>"
            f"<td>{person['page_views']}</td>"
            "</tr>"
        )
    if not people_rows:
        people_rows.append('<tr class="empty-row"><td colspan="7">Пока нет пользователей</td></tr>')

    max_section_seconds = max((int(item["active_seconds"]) for item in sections), default=0)
    section_rows = []
    for item in sections:
        key = item["section"]
        label = SECTION_LABELS.get(SectionName(key), key) if key in SectionName._value2member_map_ else key
        width = (
            max(3, round(int(item["active_seconds"]) * 100 / max_section_seconds))
            if max_section_seconds
            else 0
        )
        section_rows.append(
            '<div class="usage-section-row">'
            '<div class="usage-section-copy">'
            f"<strong>{html.escape(str(label))}</strong>"
            f"<span>{item['page_views']} открытий · {item['unique_users']} пользователей · "
            f"{html.escape(_format_duration(item['active_seconds']))}</span>"
            "</div>"
            f'<div class="usage-section-bar"><span style="width:{width}%"></span></div>'
            "</div>"
        )
    if not section_rows:
        section_rows.append('<p class="panel-desc">Статистика появится после первых посещений.</p>')

    session_rows = []
    for item in sessions:
        state = "онлайн" if item["online"] else ("вышел" if item["ended_at"] else "неактивен")
        location = _usage_location(item["last_section"], item["last_path"])
        session_rows.append(
            "<tr>"
            f'<td>{html.escape(str(item["full_name"]))}<small class="usage-login">'
            f"{html.escape(str(item['login']))}</small></td>"
            f"<td>{html.escape(format_dt(item['started_at']))}</td>"
            f"<td>{html.escape(format_dt(item['last_seen_at']))}</td>"
            f"<td>{html.escape(_format_duration(item['active_seconds']))}</td>"
            f"<td>{html.escape(location)}</td>"
            f"<td>{html.escape(state)}</td>"
            "</tr>"
        )
    if not session_rows:
        session_rows.append('<tr class="empty-row"><td colspan="6">Входов пока не зафиксировано</td></tr>')

    return (
        '<div class="usage-cards">'
        f"<article><span>Онлайн сейчас</span><strong>{data['online_count']}</strong><small>активность за последние 10 минут</small></article>"
        f"<article><span>Активны сегодня</span><strong>{data['active_today_count']}</strong><small>уникальные пользователи</small></article>"
        f"<article><span>Время сегодня</span><strong>{html.escape(_format_duration(data['active_today_seconds']))}</strong><small>суммарно по пользователям</small></article>"
        f"<article><span>Открытий разделов</span><strong>{data['period_page_views']}</strong><small>за выбранный период</small></article>"
        "</div>"
        '<div class="admin-layout usage-layout">'
        '<section class="panel"><h3 class="panel-title">Использование разделов</h3>'
        f'<p class="panel-desc">Популярность за последние {data["days"]} дней</p>'
        f'<div class="usage-sections">{"".join(section_rows)}</div></section>'
        '<section class="panel"><h3 class="panel-title">Пользователи</h3>'
        '<p class="panel-desc">Статус обновляется автоматически каждые 30 секунд</p>'
        '<div class="table-wrap"><table class="data-table"><thead><tr>'
        "<th>Сотрудник</th><th>Статус</th><th>Сейчас</th><th>Последняя активность</th>"
        "<th>Сегодня</th><th>За период</th><th>Открытий</th>"
        f"</tr></thead><tbody>{''.join(people_rows)}</tbody></table></div></section></div>"
        '<section class="panel panel--wide"><h3 class="panel-title">История входов</h3>'
        '<p class="panel-desc">Последние 100 сессий. Время считается только пока пользователь активен.</p>'
        '<div class="table-wrap table-wrap--scroll-10"><table class="data-table"><thead><tr>'
        "<th>Сотрудник</th><th>Вошёл</th><th>Последняя активность</th><th>Был онлайн</th><th>Последний раздел</th><th>Статус</th>"
        f"</tr></thead><tbody>{''.join(session_rows)}</tbody></table></div></section>"
    )


@router.get("/admin/activity", response_class=HTMLResponse)
async def admin_activity_page(
    request: Request,
    container: ContainerDependency,
    days: int = Query(30, ge=1, le=365),
):
    if not auth.has_role(request.state.user, "superadmin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    dashboard = await run_in_threadpool(container.usage.dashboard, days)
    content = fill_template(
        "admin_activity_content.html",
        days=str(days),
        dashboard=render_usage_dashboard(dashboard),
    )
    return render_page(
        "CheckStock — Статистика использования",
        "admin_activity",
        content,
        request.state.user,
        "content--usage",
    )


@router.get("/admin/activity/data")
async def admin_activity_data(
    request: Request,
    container: ContainerDependency,
    days: int = Query(30, ge=1, le=365),
):
    if not auth.has_role(request.state.user, "superadmin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    dashboard = await run_in_threadpool(container.usage.dashboard, days)
    return JSONResponse({"ok": True, "html": render_usage_dashboard(dashboard)})


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, identities: IdentityServiceDependency):
    user = request.state.user
    if not auth.has_role(user, "admin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    read_only = not auth.can_manage_users(user)
    users = await run_in_threadpool(identities.list_users)
    activity = await run_in_threadpool(identities.get_activity, ActivityLogQuery())
    log_rows = await run_in_threadpool(render_log_rows, user, activity)
    content = fill_template(
        "admin_content.html",
        role_options=render_role_options(user),
        store_options=render_store_checkboxes(user, disabled=read_only),
        user_rows=render_user_rows(user, users),
        log_rows=log_rows,
        create_hint=(
            '<p class="panel-desc panel-desc--warn">Режим просмотра: '
            "создание и изменение сотрудников недоступно.</p>"
            if read_only
            else ""
        ),
        form_disabled=" disabled" if read_only else "",
    )
    return render_page("CheckStock — Админ-панель", "admin", content, user)


@router.get("/admin/operations/{operation_id}/xlsx")
async def download_operation(request: Request, operation_id: int):

    if not auth.has_role(request.state.user, "admin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    operation = await run_in_threadpool(db.get_operation, operation_id)
    if operation is not None and not has_store_access(request.state.user, operation["store_slug"]):
        raise HTTPException(status_code=403, detail="Нет доступа к этому магазину")

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


@router.post("/admin/users")
async def admin_create_user(
    request: Request,
    identities: IdentityServiceDependency,
    container: ContainerDependency,
):
    actor = request.state.user
    if not auth.can_manage_users(actor):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    form = await request.form()
    store_slugs = normalize_admin_store_selection(actor, tuple(form.getlist("stores")))
    try:
        payload = CreateUserForm(
            full_name=str(form.get("full_name") or ""),
            google_email=str(form.get("google_email") or ""),
            login=str(form.get("login") or ""),
            password=str(form.get("password") or ""),
            role=str(form.get("role") or ""),
            store_slugs=store_slugs,
        )
    except ValidationError:
        return JSONResponse({"ok": False, "error": "проверьте заполнение полей"}, status_code=400)
    if payload.role not in creatable_roles(actor):
        return JSONResponse(
            {"ok": False, "error": "у вас нет прав заводить сотрудников с этой ролью"},
            status_code=403,
        )
    existing = await run_in_threadpool(
        identities.get_user_by_login,
        LoginQuery(login=payload.login),
    )
    if existing is not None:
        return JSONResponse({"ok": False, "error": "такой логин уже занят"}, status_code=400)
    password_hash = await run_in_threadpool(
        container.passwords.hash,
        PasswordHashRequest(password=payload.password),
    )
    created_at = datetime.now(UTC)
    user_command = CreateUserCommand(
        full_name=payload.full_name,
        google_email=payload.google_email,
        login=payload.login,
        password_hash=password_hash.root,
        role=payload.role,
        created_at=created_at,
        store_slugs=payload.store_slugs,
    )
    store_labels = ", ".join(STORES[slug].name for slug in payload.store_slugs)
    command = AuditedCreateUser(
        user=user_command,
        activity=_activity(
            actor,
            "Создан сотрудник",
            f"{payload.full_name} ({payload.login}), роль: {ROLE_LABELS[payload.role]}, кабинеты: {store_labels}",
        ),
    )
    await run_in_threadpool(identities.create_user_with_activity, command)
    return JSONResponse({"ok": True})


@router.post("/admin/sync-stock")
async def sync_stock(request: Request):
    actor = request.state.user
    if (
        not auth.has_role(actor, "admin")
        or access_level(actor, SectionName.STOCK) is not SectionAccessLevel.WRITE
    ):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    store_slugs = accessible_store_slugs(actor)
    wb_catalog_report = await run_in_threadpool(wb_catalog.sync_all, store_slugs)
    report = await run_in_threadpool(wb_sync.sync_all, store_slugs)
    for slug, entry in wb_catalog_report.items():
        if slug in report:
            report[slug]["wb_catalog"] = entry

    catalog_report = await run_in_threadpool(ozon_catalog.sync_all, store_slugs)
    ozon_report = await run_in_threadpool(ozon_sync.sync_all, store_slugs)
    for slug, entry in ozon_report.items():
        if slug in report:
            report[slug]["ozon"] = entry.get("ozon")
            report[slug]["ozon_token"] = entry.get("token")
            report[slug]["ozon_catalog"] = catalog_report.get(slug)

    ya_catalog_report = await run_in_threadpool(ya_catalog.sync_all, store_slugs)
    ya_report = await run_in_threadpool(ya_sync.sync_all, store_slugs)
    for slug, entry in ya_report.items():
        if slug in report:
            report[slug]["yandex"] = entry.get("yandex")
            report[slug]["yandex_token"] = entry.get("token")
            report[slug]["yandex_catalog"] = ya_catalog_report.get(slug)

    last_sync = await run_in_threadpool(db.get_last_sync_at)
    return JSONResponse({"report": report, "last_sync": format_dt(last_sync)})
