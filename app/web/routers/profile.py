import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.dto.identity import SectionAccessLevel, SectionName
from app.formatting import format_dt
from app.identity_policy import ROLE_LABELS, can_manage_users
from app.section_access import SECTION_LABELS, SECTION_PATHS, access_level
from app.stores import STORES
from app.web.access import accessible_store_slugs
from app.web.templating import fill_template, render_page

router = APIRouter()

ACCESS_LABELS = {
    SectionAccessLevel.NONE: "Нет доступа",
    SectionAccessLevel.READ: "Только чтение",
    SectionAccessLevel.WRITE: "Чтение и изменение",
}

ACCESS_DESCRIPTIONS = {
    SectionAccessLevel.NONE: "Раздел закрыт",
    SectionAccessLevel.READ: "Можно смотреть, изменения заблокированы",
    SectionAccessLevel.WRITE: "Можно смотреть и вносить изменения",
}


def _render_store_cards(user) -> str:
    cards = []
    for slug in accessible_store_slugs(user):
        store = STORES[slug]
        cards.append(
            '<div class="profile-store-card">'
            f'<span style="background:{store.color};color:{store.text}">{html.escape(store.initials)}</span>'
            f"<strong>{html.escape(store.name)}</strong>"
            '<small>Доступ открыт</small>'
            "</div>"
        )
    if cards:
        return "".join(cards)
    return '<p class="profile-empty">Нет доступных кабинетов</p>'


def _render_section_rows(user) -> tuple[str, int]:
    rows = []
    writable = 0
    for section in SectionName:
        level = access_level(user, section)
        writable += int(level is SectionAccessLevel.WRITE)
        label = html.escape(SECTION_LABELS[section])
        title = (
            f'<a href="{SECTION_PATHS[section]}">{label}</a>'
            if level is not SectionAccessLevel.NONE
            else f"<strong>{label}</strong>"
        )
        rows.append(
            f'<div class="profile-access-row profile-access-row--{level.value}">'
            '<span class="profile-access-mark" aria-hidden="true"></span>'
            f'<div class="profile-access-copy">{title}'
            f"<small>{html.escape(ACCESS_DESCRIPTIONS[level])}</small></div>"
            f'<span class="profile-access-badge">{html.escape(ACCESS_LABELS[level])}</span>'
            "</div>"
        )
    return "".join(rows), writable


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = request.state.user
    section_rows, writable_sections = _render_section_rows(user)
    name_parts = [part for part in user.full_name.split() if part]
    initials = "".join(part[0] for part in name_parts[:2]).upper() or "CS"
    stock_edit_allowed = (
        user.can_edit_stock
        and access_level(user, SectionName.STOCK) is SectionAccessLevel.WRITE
    )
    users_manage_allowed = can_manage_users(user)
    content = fill_template(
        "profile_content.html",
        user_initials=html.escape(initials),
        full_name=html.escape(user.full_name),
        email=html.escape(user.google_email or "Не указана"),
        login=html.escape(user.login),
        role=html.escape(ROLE_LABELS[user.role]),
        created_at=html.escape(format_dt(user.created_at.isoformat())),
        status_label="Активна" if user.is_active else "Заблокирована",
        status_class="active" if user.is_active else "blocked",
        store_cards=_render_store_cards(user),
        store_count=str(len(accessible_store_slugs(user))),
        section_rows=section_rows,
        writable_sections=str(writable_sections),
        stock_edit_label="Разрешено" if stock_edit_allowed else "Запрещено",
        stock_edit_class="yes" if stock_edit_allowed else "no",
        users_manage_label="Разрешено" if users_manage_allowed else "Запрещено",
        users_manage_class="yes" if users_manage_allowed else "no",
    )
    return render_page(
        "CheckStock — Профиль пользователя",
        "profile",
        content,
        user,
        "content--profile",
    )
