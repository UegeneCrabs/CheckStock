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


def _token_alerts(user: dict | None = None) -> list[dict[str, str]]:
    allowed_names = {STORES[slug]["name"] for slug in accessible_store_slugs(user)}
    warnings = [
        warning
        for warning in token_watch.get_warnings()
        if not allowed_names or warning.get("store") in allowed_names
    ]
    alerts: list[dict[str, str]] = []
    for w in warnings:
        when = format_dt(w["expires_at"])
        if w["expired"]:
            message = f"{w['store']} — ключ уже недействителен (истёк {when})"
        else:
            days = w["days_left"]
            tail = "сегодня" if days <= 0 else f"через {days} дн."
            message = f"{w['store']} — ключ истекает {tail} ({when})"
        alerts.append(
            {
                "title": "Скоро закончится срок действия ключа WB",
                "text": message,
            }
        )
    return alerts


def _advertising_alerts(user: dict | None = None) -> list[dict[str, str]]:
    store_slugs = accessible_store_slugs(user)
    try:
        states = db.list_unit_economics_1c_advertising_sync_states(store_slugs)
    except Exception:
        return []
    alerts: list[dict[str, str]] = []
    for state in states:
        if state.get("status") != "error":
            continue
        store_slug = str(state.get("store_slug") or "")
        store_name = STORES[store_slug]["name"] if store_slug in STORES else store_slug.upper()
        message = str(state.get("error") or "ошибка синхронизации")
        access_error = any(
            marker in message.casefold() for marker in ("доступ", "токен", "авторизац", "401", "403")
        )
        title = f"Кабинет {store_name}: затраты на рекламу не обновились"
        if access_error:
            title += " из-за доступа у API-ключа"
        alerts.append({"title": title, "text": "" if access_error else message})
    return alerts


def _render_alerts(alerts: list[dict[str, str]]) -> str:
    normalized = [alert for alert in alerts if alert.get("title") or alert.get("text")]
    if not normalized:
        return ""
    dots = "".join(
        f'<i class="system-alert-dot{" is-active" if index == 0 else ""}"></i>'
        for index in range(len(normalized))
    )
    items = "".join(
        (
            f'<article class="system-alert-item" data-system-alert-item{" hidden" if index else ""}>'
            f"<strong>{html.escape(str(alert.get('title') or ''))}</strong>"
            f"<span>{html.escape(str(alert.get('text') or ''))}</span>"
            "</article>"
        )
        for index, alert in enumerate(normalized)
    )
    return (
        '<section class="token-banner system-alerts" data-system-alerts role="status" aria-live="polite">'
        f'<span class="system-alert-dots" aria-hidden="true">{dots}</span>'
        '<span class="token-banner-icon" aria-hidden="true">!</span>'
        f'<div class="system-alert-items">{items}</div>'
        "</section>"
    )


def render_token_banner(user: dict | None = None) -> str:
    return _render_alerts(_token_alerts(user))


def render_system_alerts(
    user: dict | None = None,
    extra_alerts: list[dict[str, str]] | None = None,
) -> str:
    return _render_alerts([*_token_alerts(user), *_advertising_alerts(user), *(extra_alerts or [])])


def render_page(
    title: str,
    active: str,
    content: str,
    user: dict | None = None,
    content_class: str = "",
    alerts: list[dict[str, str]] | None = None,
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
        "unit_1c_settings": "ЮНИТ-ЭКОНОМИКА 1С / ВВОД ДАННЫХ ПО КАБИНЕТАМ",
        "unit_1c_wb": "ЮНИТ-ЭКОНОМИКА 1С / WILDBERRIES",
        "unit_1c_ozon": "ЮНИТ-ЭКОНОМИКА 1С / OZON",
        "unit_1c_yandex": "ЮНИТ-ЭКОНОМИКА 1С / ЯНДЕКС МАРКЕТ",
        "unit_1c_reports": "ОТЧЁТЫ / ЮНИТОЧНАЯ ПРИБЫЛЬ",
        "supply": "ПОСТАВКИ И ЗАЯВКИ",
        "stock": "УПРАВЛЕНИЕ ЗАПАСАМИ",
        "stock_total": "УПРАВЛЕНИЕ ЗАПАСАМИ / ОСТАТКИ ТОТАЛ",
        "stock2": "УПРАВЛЕНИЕ ЗАПАСАМИ",
        "stock_supplies": "УПРАВЛЕНИЕ ЗАПАСАМИ / ПОСТАВКИ",
        "stock_randomizer": "УПРАВЛЕНИЕ ЗАПАСАМИ / СВЕРКА С ФФ",
        "stock_cost_report": "УПРАВЛЕНИЕ ЗАПАСАМИ / ДВИЖЕНИЕ И ЗЦ",
        "admin": "НАСТРОЙКИ И ДОСТУПЫ",
        "admin_activity": "ИСПОЛЬЗОВАНИЕ СИСТЕМЫ",
        "admin_google_export": "АВТОМАТИЗАЦИЯ / GOOGLE ТАБЛИЦЫ",
        "profile": "ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ",
        "access_denied": "ДОСТУП ОГРАНИЧЕН",
    }
    page_heading = title.removeprefix("CheckStock — ").replace(" — ", " / ")
    sales_open = active in {"sales", "sales_ephemerides", "sales_rnp"}
    stock_open = active in {
        "stock",
        "stock_total",
        "stock2",
        "stock_supplies",
        "stock_randomizer",
        "stock_cost_report",
    }
    unit_1c_open = active in {
        "unit_1c_settings",
        "unit_1c_wb",
        "unit_1c_ozon",
        "unit_1c_yandex",
    }
    reports_open = active == "unit_1c_reports"
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
        unit_1c_open="is-open" if unit_1c_open else "",
        unit_1c_expanded="true" if unit_1c_open else "false",
        unit_1c_group_hidden=hidden(visible[SectionName.UNIT_ECONOMICS_1C]),
        unit_1c_group_active="active" if unit_1c_open else "",
        unit_1c_settings_active="active" if active == "unit_1c_settings" else "",
        unit_1c_wb_active="active" if active == "unit_1c_wb" else "",
        unit_1c_ozon_active="active" if active == "unit_1c_ozon" else "",
        unit_1c_yandex_active="active" if active == "unit_1c_yandex" else "",
        reports_open="is-open" if reports_open else "",
        reports_expanded="true" if reports_open else "false",
        reports_group_hidden=hidden(visible[SectionName.UNIT_ECONOMICS_1C]),
        reports_group_active="active" if reports_open else "",
        unit_1c_reports_active="active" if active == "unit_1c_reports" else "",
        supply_active="active" if active == "supply" else "",
        supply_hidden=hidden(visible[SectionName.SUPPLY]),
        stock_active="active" if active == "stock" else "",
        stock_hidden=hidden(visible[SectionName.STOCK]),
        stock_total_active="active" if active == "stock_total" else "",
        stock_total_hidden=hidden(visible[SectionName.STOCK]),
        stock2_active="active" if active == "stock2" else "",
        stock2_hidden=hidden(visible[SectionName.STOCK_OVERVIEW]),
        stock_supplies_active="active" if active == "stock_supplies" else "",
        stock_supplies_hidden=hidden(visible[SectionName.STOCK]),
        stock_randomizer_active="active" if active == "stock_randomizer" else "",
        stock_randomizer_hidden=hidden(visible[SectionName.STOCK]),
        stock_cost_report_active="active" if active == "stock_cost_report" else "",
        stock_cost_report_hidden=hidden(visible[SectionName.STOCK]),
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
        content=render_system_alerts(user, alerts) + content,
        section=current_section.value if current_section is not None else active,
        access_level=current_access.value,
    )
