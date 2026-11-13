import html
import re
from string import Template

from app.access.authentication import is_active_user
from app.access.sections import (
    ANALYTICS_SECTIONS,
    REPORT_SECTIONS,
    SECTION_LABELS,
    SECTION_PARENTS,
    STOCK_SECTIONS,
    UNIT_ECONOMICS_SECTIONS,
    active_section,
    section_path,
)
from app.config import settings
from app.dto.identity import SectionName
from app.web.client_templates import render_client_templates


def read_template(name: str) -> str:
    def read(current: str, parents: tuple[str, ...]) -> str:
        if current in parents:
            raise ValueError(f"Recursive template include: {current}")
        path = (settings.templates_dir / current).resolve()
        if not path.is_relative_to(settings.templates_dir.resolve()):
            raise ValueError(f"Template is outside the template directory: {current}")
        source = path.read_text(encoding="utf-8")
        return re.sub(
            r"<!-- include: ([\w/.-]+\.html) -->",
            lambda match: read(match[1], (*parents, current)),
            source,
        )

    return read(name, ())


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
            "Проверьте адрес страницы и состояние учётной записи."
        )
    else:
        heading = heading or "Нет доступных разделов"
        description = description or "Проверьте адрес страницы и повторите вход в систему."
    content = fill_template(
        "auth/access-denied.html",
        heading=html.escape(heading),
        description=html.escape(description),
    )
    return render_page("CheckStock — Нет доступа", "access_denied", content, user)


def render_page(
    title: str,
    active: str,
    content: str,
    user: dict | None = None,
    content_class: str = "",
) -> str:
    stylesheet = re.compile(r'<link\b(?=[^>]*\brel=["\']stylesheet["\'])[^>]*>', re.IGNORECASE)
    page_styles = "\n".join(dict.fromkeys(stylesheet.findall(content)))
    content = stylesheet.sub("", content)
    admin_link = ""
    if is_active_user(user):
        admin_cls = "active" if active == "admin" else ""
        admin_link = (
            f'                <a class="nav-item {admin_cls}" href="/admin" title="Админ-панель" aria-label="Админ-панель">'
            '<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"></path>'
            '<path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.08A1.7 1.7 0 0 0 9 19.37a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15 1.7 1.7 0 0 0 3.08 14H3v-4h.08A1.7 1.7 0 0 0 4.63 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63 1.7 1.7 0 0 0 10 3.08V3h4v.08A1.7 1.7 0 0 0 15 4.63a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.37 9 1.7 1.7 0 0 0 20.92 10H21v4h-.08A1.7 1.7 0 0 0 19.4 15Z"></path>'
            "</svg><span>Админ</span></a>"
        )
    if is_active_user(user):
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
        "stock_cost_report",
    }
    unit_1c_open = active in {
        "unit_1c_settings",
        "unit_1c_wb",
        "unit_1c_ozon",
        "unit_1c_yandex",
    }
    reports_open = active in {
        "wb_standard_costs",
        "unit_1c_reports",
        "unit_1c_spp_report",
        "unit_1c_rating_report",
        "unit_1c_target_price",
        "unit_1c_reports_yandex",
        "unit_1c_target_price_yandex",
    }
    visible = {section: is_active_user(user) for section in SectionName}
    current_section = active_section(active)

    def hidden(allowed: bool) -> str:
        return "" if allowed else " hidden"

    marketplace = {
        "wb_standard_costs": ("wb", "WB", "Wildberries"),
        "analytics_sales_api": ("wb", "WB", "Wildberries"),
        "analyzer": ("wb", "WB", "Wildberries"),
        "yandex_analyzer": ("ym", "Я", "Яндекс Маркет"),
        "yandex_ephemerides": ("ym", "Я", "Яндекс Маркет"),
        "yandex_sales_api": ("ym", "Я", "Яндекс Маркет"),
        "ephemerides": ("wb", "WB", "Wildberries"),
        "unit_1c_wb": ("wb", "WB", "Wildberries"),
        "unit_1c_ozon": ("ozon", "O", "Ozon"),
        "unit_1c_yandex": ("ym", "Я", "Яндекс Маркет"),
        "unit_1c_reports": ("wb", "WB", "Wildberries"),
        "unit_1c_spp_report": ("wb", "WB", "Wildberries"),
        "unit_1c_rating_report": ("wb", "WB", "Wildberries"),
        "unit_1c_target_price": ("wb", "WB", "Wildberries"),
        "unit_1c_reports_yandex": ("ym", "Я", "Яндекс Маркет"),
        "unit_1c_target_price_yandex": ("ym", "Я", "Яндекс Маркет"),
    }.get(active)
    marketplace_badge = ""
    if marketplace:
        color, mark, label = marketplace
        marketplace_section = (
            "Аналитика"
            if active in {item.value for item in ANALYTICS_SECTIONS}
            else "Отчёты"
            if reports_open
            else "Юнит-экономика"
        )
        marketplace_badge = (
            f'<span class="marketplace-badge" aria-label="{marketplace_section}: {label}">'
            f'<span class="marketplace-badge-mark" aria-hidden="true">{mark}</span>'
            f'<span class="marketplace-badge-label">{label}</span></span>'
        )
    header = fill_template(
        "layout/header.html",
        finance_hidden=hidden(visible[SectionName.FINANCE_YANDEX]),
        finance_active="active" if active == "finance_yandex" else "",
        finance_expanded="false",
        marketplace_header_class=" marketplace-" + marketplace[0] if marketplace else "",
        marketplace_badge=marketplace_badge,
        analytics_hidden=hidden(any(visible[item] for item in ANALYTICS_SECTIONS)),
        analytics_active="active" if active in {item.value for item in ANALYTICS_SECTIONS} else "",
        stock_open="",
        stock_expanded="false",
        stock_group_hidden=hidden(any(visible[item] for item in STOCK_SECTIONS)),
        stock_group_active="active" if stock_open else "",
        unit_1c_open="",
        unit_1c_expanded="false",
        unit_1c_group_hidden=hidden(any(visible[item] for item in UNIT_ECONOMICS_SECTIONS)),
        unit_1c_group_active="active" if unit_1c_open else "",
        unit_1c_settings_active="active" if active == "unit_1c_settings" else "",
        unit_1c_wb_active="active" if active == "unit_1c_wb" else "",
        unit_1c_ozon_active="active" if active == "unit_1c_ozon" else "",
        unit_1c_yandex_active="active" if active == "unit_1c_yandex" else "",
        wb_conditions_active="active" if active == "wb_standard_costs" else "",
        wb_conditions_hidden=hidden(visible[SectionName.REPORT_WB_CONDITIONS]),
        reports_open="",
        reports_expanded="false",
        reports_group_hidden=hidden(any(visible[item] for item in REPORT_SECTIONS)),
        reports_group_active="active" if reports_open else "",
        unit_1c_reports_active="active" if active == "unit_1c_reports" else "",
        unit_1c_spp_report_active="active" if active == "unit_1c_spp_report" else "",
        unit_1c_rating_report_active="active" if active == "unit_1c_rating_report" else "",
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
        stock_arrivals_active="active" if active == "stock_arrivals" else "",
        stock_arrivals_hidden=hidden(visible[SectionName.STOCK_ARRIVALS]),
        stock_cost_report_active="active" if active == "stock_cost_report" else "",
        stock_cost_report_hidden=hidden(visible[SectionName.STOCK_COST_REPORT]),
        stock_operations_active="active" if active == "stock_operations" else "",
        stock_operations_hidden=hidden(visible[SectionName.STOCK_OPERATIONS]),
        stock_operations_path=section_path(user, SectionName.STOCK_OPERATIONS),
        unit_1c_wb_hidden=hidden(visible[SectionName.UNIT_ECONOMICS_WB]),
        unit_1c_ozon_hidden=hidden(visible[SectionName.UNIT_ECONOMICS_OZON]),
        unit_1c_yandex_hidden=hidden(visible[SectionName.UNIT_ECONOMICS_YANDEX]),
        unit_1c_reports_yandex_active="active" if active == "unit_1c_reports_yandex" else "",
        unit_1c_target_price_yandex_active="active" if active == "unit_1c_target_price_yandex" else "",
        unit_1c_reports_yandex_hidden=hidden(visible[SectionName.REPORT_UNIT_PROFIT_YANDEX]),
        unit_1c_target_price_yandex_hidden=hidden(visible[SectionName.REPORT_TARGET_PRICE_YANDEX]),
        unit_1c_reports_hidden=hidden(visible[SectionName.REPORT_UNIT_PROFIT]),
        unit_1c_spp_report_hidden=hidden(visible[SectionName.REPORT_SPP]),
        unit_1c_rating_report_hidden=hidden(visible[SectionName.REPORT_RATING]),
        unit_1c_target_price_hidden=hidden(visible[SectionName.REPORT_TARGET_PRICE]),
        admin_link=admin_link,
        user_name=html.escape(full_name),
        user_initials=html.escape(user_initials),
        profile_active="profile--active" if active == "profile" else "",
    )
    return fill_template(
        "layout/page.html",
        title=title,
        header=header,
        content_class=html.escape(content_class),
        content=content,
        section=(
            SECTION_PARENTS.get(current_section, current_section).value
            if current_section is not None and not active.startswith("admin")
            else active
        ),
        client_templates=render_client_templates(read_template("layout/page.html") + content),
        page_styles=page_styles,
    )
