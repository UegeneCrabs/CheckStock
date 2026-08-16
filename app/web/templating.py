import html
from string import Template

from app import auth, db
from app.config import settings
from app.dto.identity import SectionAccessLevel, SectionName
from app.formatting import format_dt
from app.section_access import SECTION_LABELS, SECTION_PATHS, access_level, active_section, has_access
from app.stores import STORES
from app.wb import token_watch
from app.web.access import accessible_store_slugs


def read_template(name: str) -> str:
    return (settings.templates_dir / name).read_text(encoding="utf-8")


def fill_template(name: str, **values: str) -> str:
    return Template(read_template(name)).substitute(**values)


def render_access_denied_page(
    user: dict | None,
    *,
    section: SectionName | None = None,
    heading: str | None = None,
    description: str | None = None,
) -> str:
    if section is not None:
        section_label = SECTION_LABELS[section]
        heading = heading or "Нет доступа к вкладке"
        description = description or (
            f"Вкладка «{section_label}» недоступна для вашей учётной записи. "
            "Если она нужна для работы, обратитесь к суперадминистратору."
        )
    else:
        heading = heading or "Нет доступных разделов"
        description = description or ("Обратитесь к суперадминистратору, чтобы он открыл нужные разделы.")
    content = fill_template(
        "access_denied_content.html",
        heading=html.escape(heading),
        description=html.escape(description),
    )
    return render_page("CheckStock — Нет доступа", "access_denied", content, user)


def render_token_banner(user: dict | None = None) -> str:

    allowed_names = {STORES[slug]["name"] for slug in accessible_store_slugs(user)}
    warnings = [
        warning
        for warning in token_watch.get_warnings()
        if not allowed_names or warning.get("store") in allowed_names
    ]
    if not warnings:
        return ""

    items = []
    for w in warnings:
        when = format_dt(w["expires_at"])
        if w["expired"]:
            items.append(
                f"<strong>{html.escape(w['store'])}</strong> — ключ уже недействителен (истёк {when})"
            )
        else:
            days = w["days_left"]
            tail = "сегодня" if days <= 0 else f"через {days} дн."
            items.append(f"<strong>{html.escape(w['store'])}</strong> — ключ истекает {tail} ({when})")

    return (
        '<div class="token-banner">'
        '<span class="token-banner-icon">!</span>'
        '<div><p class="token-banner-title">Скоро закончится срок действия ключа WB</p>'
        f'<p class="token-banner-text">{"; ".join(items)}. '
        "Сообщите администратору о необходимости замены ключа — иначе остатки перестанут обновляться.</p></div>"
        "</div>"
    )


def render_page(
    title: str, active: str, content: str, user: dict | None = None, content_class: str = ""
) -> str:
    admin_link = ""
    if auth.has_role(user, "admin"):
        admin_cls = "active" if active == "admin" else ""
        admin_link = (
            f'                <a class="nav-item {admin_cls}" href="/admin" title="Админ-панель" aria-label="Админ-панель">'
            '<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"></path>'
            '<path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.08A1.7 1.7 0 0 0 9 19.37a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15 1.7 1.7 0 0 0 3.08 14H3v-4h.08A1.7 1.7 0 0 0 4.63 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63 1.7 1.7 0 0 0 10 3.08V3h4v.08A1.7 1.7 0 0 0 15 4.63a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.37 9 1.7 1.7 0 0 0 20.92 10H21v4h-.08A1.7 1.7 0 0 0 19.4 15Z"></path>'
            "</svg><span>Админ</span></a>"
        )
    if auth.has_role(user, "superadmin"):
        usage_cls = "active" if active == "admin_activity" else ""
        admin_link += (
            f'                <a class="nav-item {usage_cls}" href="/admin/activity" title="Статистика использования" aria-label="Статистика использования">'
            '<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M4 19V9"></path><path d="M10 19V5"></path>'
            '<path d="M16 19v-7"></path><path d="M22 19H2"></path>'
            "</svg><span>Статистика</span></a>"
        )
        export_cls = "active" if active == "admin_google_export" else ""
        admin_link += (
            f'                <a class="nav-item {export_cls}" href="/admin/google-export" '
            'title="Выгрузка в Google Таблицы" aria-label="Выгрузка в Google Таблицы">'
            '<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M4 4h16v16H4Z"></path><path d="M4 9h16M9 4v16"></path>'
            "</svg><span>Выгрузки</span></a>"
        )

    full_name = user["full_name"] if user else ""
    name_parts = [part for part in full_name.split() if part]
    user_initials = "".join(part[0] for part in name_parts[:2]).upper() or "CS"
    section_kickers = {
        "sales": "ПРОДАЖИ И АНАЛИТИКА",
        "sales_decision": "ПРОДАЖИ И АНАЛИТИКА / WILDBERRIES",
        "sales_ephemerides": "ПРОДАЖИ И АНАЛИТИКА",
        "sales_rnp": "ПРОДАЖИ И АНАЛИТИКА",
        "sales_unit": "ЮНИТ-ЭКОНОМИКА",
        "sales_wb_fbs": "ЮНИТ-ЭКОНОМИКА / WB FBS",
        "sales_ozon": "ЮНИТ-ЭКОНОМИКА / OZON",
        "sales_yandex": "ЮНИТ-ЭКОНОМИКА / ЯНДЕКС МАРКЕТ",
        "supply": "ПОСТАВКИ И ЗАЯВКИ",
        "stock": "УПРАВЛЕНИЕ ЗАПАСАМИ",
        "stock2": "УПРАВЛЕНИЕ ЗАПАСАМИ",
        "admin": "НАСТРОЙКИ И ДОСТУПЫ",
        "admin_activity": "ИСПОЛЬЗОВАНИЕ СИСТЕМЫ",
        "admin_google_export": "АВТОМАТИЗАЦИЯ / GOOGLE ТАБЛИЦЫ",
        "profile": "ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ",
        "access_denied": "ДОСТУП ОГРАНИЧЕН",
    }
    page_heading = title.removeprefix("CheckStock — ").replace(" — ", " / ")
    sales_open = active in {"sales", "sales_ephemerides", "sales_rnp"}
    stock_open = active in {"stock", "stock2"}
    unit_open = active in {"sales_unit", "sales_wb_fbs", "sales_ozon", "sales_yandex"}
    visible = {section: has_access(user, section) for section in SectionName}
    sales_sections = (
        SectionName.SALES,
        SectionName.EPHEMERIDES,
        SectionName.RNP,
    )
    stock_sections = (SectionName.STOCK, SectionName.STOCK_OVERVIEW)
    first_sales = next((section for section in sales_sections if visible[section]), None)
    first_stock = next((section for section in stock_sections if visible[section]), None)
    current_section = active_section(active)
    current_access = (
        access_level(user, current_section) if current_section is not None else SectionAccessLevel.WRITE
    )

    def hidden(allowed: bool) -> str:
        return "" if allowed else " hidden"

    header = fill_template(
        "header.html",
        decision_active="active" if active == "sales_decision" else "",
        decision_hidden=hidden(visible[SectionName.DECISION_CENTER]),
        sales_active="active" if sales_open else "",
        sales_open="is-open" if sales_open else "",
        sales_expanded="true" if sales_open else "false",
        sales_group_hidden=hidden(first_sales is not None),
        sales_href=SECTION_PATHS[first_sales] if first_sales is not None else "/access-denied",
        sales_overview_hidden=hidden(visible[SectionName.SALES]),
        sales_ephemerides_hidden=hidden(visible[SectionName.EPHEMERIDES]),
        sales_rnp_hidden=hidden(visible[SectionName.RNP]),
        sales_overview_active="active" if active == "sales" else "",
        sales_ephemerides_active="active" if active == "sales_ephemerides" else "",
        sales_rnp_active="active" if active == "sales_rnp" else "",
        stock_open="is-open" if stock_open else "",
        stock_expanded="true" if stock_open else "false",
        stock_group_hidden=hidden(first_stock is not None),
        stock_href=SECTION_PATHS[first_stock] if first_stock is not None else "/access-denied",
        stock_group_active="active" if stock_open else "",
        unit_open="is-open" if unit_open else "",
        unit_expanded="true" if unit_open else "false",
        unit_group_hidden=hidden(visible[SectionName.UNIT_ECONOMICS]),
        unit_href="/sales/unit-economics/wb-fbs",
        unit_group_active="active" if unit_open else "",
        sales_wb_fbs_active="active" if active == "sales_wb_fbs" else "",
        sales_ozon_active="active" if active == "sales_ozon" else "",
        sales_yandex_active="active" if active == "sales_yandex" else "",
        supply_active="active" if active == "supply" else "",
        supply_hidden=hidden(visible[SectionName.SUPPLY]),
        stock_active="active" if active == "stock" else "",
        stock_hidden=hidden(visible[SectionName.STOCK]),
        stock2_active="active" if active == "stock2" else "",
        stock2_hidden=hidden(visible[SectionName.STOCK_OVERVIEW]),
        admin_link=admin_link,
        user_name=html.escape(full_name),
        user_role=html.escape(db.ROLE_LABELS.get(user["role"], user["role"])) if user else "",
        user_initials=html.escape(user_initials),
        profile_active="profile--active" if active == "profile" else "",
    )
    return fill_template(
        "page.html",
        title=title,
        header=header,
        page_kicker=section_kickers.get(active, "РАБОЧЕЕ ПРОСТРАНСТВО"),
        page_heading=html.escape(page_heading),
        content_class=html.escape(content_class),
        content=render_token_banner(user) + content,
        section=current_section.value if current_section is not None else active,
        access_level=current_access.value,
    )
