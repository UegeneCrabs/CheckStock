import html
from string import Template

from app import auth, db
from app.config import settings
from app.formatting import format_dt
from app.stores import STORES
from app.wb import token_watch
from app.web.access import accessible_store_slugs


def read_template(name: str) -> str:
    return (settings.templates_dir / name).read_text(encoding="utf-8")


def fill_template(name: str, **values: str) -> str:
    return Template(read_template(name)).substitute(**values)


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
        cls = "active" if active == "admin" else ""
        admin_link = (
            f'                <a class="nav-item {cls}" href="/admin">'
            '<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"></path>'
            '<path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.08A1.7 1.7 0 0 0 9 19.37a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15 1.7 1.7 0 0 0 3.08 14H3v-4h.08A1.7 1.7 0 0 0 4.63 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63 1.7 1.7 0 0 0 10 3.08V3h4v.08A1.7 1.7 0 0 0 15 4.63a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.37 9 1.7 1.7 0 0 0 20.92 10H21v4h-.08A1.7 1.7 0 0 0 19.4 15Z"></path>'
            "</svg><span>Админка</span></a>"
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
    }
    page_heading = title.removeprefix("CheckStock — ").replace(" — ", " / ")
    sales_open = active.startswith("sales")

    header = fill_template(
        "header.html",
        sales_active="active" if sales_open else "",
        sales_open="is-open" if sales_open else "",
        sales_expanded="true" if sales_open else "false",
        sales_decision_active="active" if active == "sales_decision" else "",
        sales_ephemerides_active="active" if active == "sales_ephemerides" else "",
        sales_rnp_active="active" if active == "sales_rnp" else "",
        sales_unit_active="active" if active == "sales_unit" else "",
        sales_wb_fbs_active="active" if active == "sales_wb_fbs" else "",
        sales_ozon_active="active" if active == "sales_ozon" else "",
        sales_yandex_active="active" if active == "sales_yandex" else "",
        supply_active="active" if active == "supply" else "",
        stock_active="active" if active == "stock" else "",
        stock2_active="active" if active == "stock2" else "",
        admin_link=admin_link,
        user_name=html.escape(full_name),
        user_role=html.escape(db.ROLE_LABELS.get(user["role"], user["role"])) if user else "",
        user_initials=html.escape(user_initials),
    )
    return fill_template(
        "page.html",
        title=title,
        header=header,
        page_kicker=section_kickers.get(active, "РАБОЧЕЕ ПРОСТРАНСТВО"),
        page_heading=html.escape(page_heading),
        content_class=html.escape(content_class),
        content=render_token_banner(user) + content,
    )
