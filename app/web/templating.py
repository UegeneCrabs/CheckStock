import html
from string import Template

from app import auth, db, health
from app.access_control import accessible_marketplaces
from app.config import settings
from app.dto.identity import SectionAccessLevel, SectionName
from app.formatting import format_dt
from app.section_access import (
    SECTION_GROUPS,
    SECTION_LABELS,
    SECTION_PARENTS,
    access_level,
    active_section,
    has_access,
    section_path,
)
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
        if not health.requires_team_action(message):
            continue
        alerts.append(
            {
                "title": f"Кабинет {store_name}: проверьте права API-ключа WB",
                "text": (
                    "Реклама не обновляется. Добавьте ключу доступ к разделу «Продвижение» "
                    "или замените ключ, затем повторите выгрузку."
                ),
            }
        )
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
    if auth.has_role(user, "admin") and has_access(user, SectionName.ADMIN_USERS):
        admin_cls = "active" if active == "admin" else ""
        admin_link = (
            f'                <a class="nav-item {admin_cls}" href="/admin" title="Админ-панель" aria-label="Админ-панель">'
            '<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"></path>'
            '<path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.08A1.7 1.7 0 0 0 9 19.37a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15 1.7 1.7 0 0 0 3.08 14H3v-4h.08A1.7 1.7 0 0 0 4.63 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63 1.7 1.7 0 0 0 10 3.08V3h4v.08A1.7 1.7 0 0 0 15 4.63a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.37 9 1.7 1.7 0 0 0 20.92 10H21v4h-.08A1.7 1.7 0 0 0 19.4 15Z"></path>'
            "</svg><span>Админ</span></a>"
        )
    if auth.has_role(user, "superadmin"):
        integrations_cls = "active" if active in {"admin_integrations", "admin_google_export"} else ""
        admin_link += (
            f'                <a class="nav-item {integrations_cls}" href="/admin/integrations" '
            'title="Интеграции и выгрузки" aria-label="Интеграции и выгрузки">'
            '<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M7 14a5 5 0 1 1 3.9 1.9L8 19H5v-3l2-2Z"></path>'
            '<path d="m14 8 2 2m-4 2 2 2"></path>'
            "</svg><span>Интеграции</span></a>"
        )

    full_name = user["full_name"] if user else ""
    name_parts = [part for part in full_name.split() if part]
    user_initials = "".join(part[0] for part in name_parts[:2]).upper() or "CS"
    stock_open = active in {
        "stock",
        "stock_total",
        "stock_supplies",
        "stock_inbound",
        "stock_randomizer",
        "stock_cost_report",
    }
    unit_1c_open = active in {
        "unit_1c_settings",
        "unit_1c_wb",
        "unit_1c_ozon",
        "unit_1c_yandex",
    }
    reports_open = active in {"unit_1c_reports", "unit_1c_target_price"}
    visible = {section: has_access(user, section) for section in SectionName}
    current_section = active_section(active)
    current_access = (
        access_level(user, current_section) if current_section is not None else SectionAccessLevel.WRITE
    )
    def hidden(allowed: bool) -> str:
        return "" if allowed else " hidden"

    header = fill_template(
        "header.html",
        stock_open="",
        stock_expanded="false",
        stock_group_hidden=hidden(any(visible[item] for item in SECTION_GROUPS[0][1])),
        stock_group_active="active" if stock_open else "",
        unit_1c_open="",
        unit_1c_expanded="false",
        unit_1c_group_hidden=hidden(any(visible[item] for item in SECTION_GROUPS[1][1])),
        unit_1c_group_active="active" if unit_1c_open else "",
        unit_1c_settings_active="active" if active == "unit_1c_settings" else "",
        unit_1c_wb_active="active" if active == "unit_1c_wb" else "",
        unit_1c_ozon_active="active" if active == "unit_1c_ozon" else "",
        unit_1c_yandex_active="active" if active == "unit_1c_yandex" else "",
        reports_open="",
        reports_expanded="false",
        reports_group_hidden=hidden(any(visible[item] for item in SECTION_GROUPS[2][1])),
        reports_group_active="active" if reports_open else "",
        unit_1c_reports_active="active" if active == "unit_1c_reports" else "",
        unit_1c_target_price_active="active" if active == "unit_1c_target_price" else "",
        ai_agents_active="active" if active == "ai_agents" else "",
        ai_agents_hidden=hidden(visible[SectionName.AI_AGENTS]),
        stock_active="active" if active == "stock" else "",
        stock_hidden=hidden(visible[SectionName.STOCK_BALANCES]),
        stock_total_active="active" if active == "stock_total" else "",
        stock_total_hidden=hidden(visible[SectionName.STOCK_TOTAL]),
        stock_supplies_active="active" if active == "stock_supplies" else "",
        stock_inbound_active="active" if active == "stock_inbound" else "",
        stock_supplies_hidden=hidden(visible[SectionName.STOCK_SUPPLIES]),
        stock_inbound_hidden=hidden(visible[SectionName.STOCK_INBOUND]),
        stock_randomizer_active="active" if active == "stock_randomizer" else "",
        stock_randomizer_hidden=hidden(
            visible[SectionName.STOCK_RANDOMIZER] and "WB" in accessible_marketplaces(user)
        ),
        stock_cost_report_active="active" if active == "stock_cost_report" else "",
        stock_cost_report_hidden=hidden(visible[SectionName.STOCK_COST_REPORT]),
        stock_operations_active="active" if active == "stock_operations" else "",
        stock_operations_hidden=hidden(visible[SectionName.STOCK_OPERATIONS]),
        stock_operations_path=section_path(user, SectionName.STOCK_OPERATIONS),
        unit_1c_wb_hidden=hidden(visible[SectionName.UNIT_ECONOMICS_WB]),
        unit_1c_ozon_hidden=hidden(visible[SectionName.UNIT_ECONOMICS_OZON]),
        unit_1c_yandex_hidden=hidden(visible[SectionName.UNIT_ECONOMICS_YANDEX]),
        unit_1c_reports_hidden=hidden(visible[SectionName.REPORT_UNIT_PROFIT]),
        unit_1c_target_price_hidden=hidden(visible[SectionName.REPORT_TARGET_PRICE]),
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
        content_class=html.escape(content_class),
        content=render_system_alerts(user, alerts) + content,
        section=(SECTION_PARENTS.get(current_section, current_section).value
                 if current_section is not None and not active.startswith("admin") else active),
        access_level=current_access.value,
    )
