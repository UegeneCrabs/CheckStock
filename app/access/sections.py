from __future__ import annotations

from app.access.access_control import (
    ActionPermission,
    accessible_marketplaces,
    accessible_stores,
    profile_has_permission,
)
from app.dto.identity import Role, SectionAccessLevel, SectionName, User, coerce_user

S = SectionName
L = SectionAccessLevel

SECTION_GROUPS = (
    (
        "Сток",
        (
            S.STOCK_BALANCES,
            S.STOCK_TOTAL,
            S.STOCK_SUPPLIES,
            S.STOCK_INBOUND,
            S.STOCK_RANDOMIZER,
            S.STOCK_COST_REPORT,
            S.STOCK_OPERATIONS,
        ),
    ),
    ("Юнит-экономика 1С", (S.UNIT_ECONOMICS_WB, S.UNIT_ECONOMICS_OZON, S.UNIT_ECONOMICS_YANDEX)),
    ("Отчёты", (S.REPORT_UNIT_PROFIT, S.REPORT_TARGET_PRICE)),
    ("Система", (S.AI_AGENTS, S.ADMIN_USERS, S.ADMIN_GOOGLE_EXPORT, S.ADMIN_INTEGRATIONS)),
)
SECTION_LABELS = {
    S.STOCK_BALANCES: "Остатки и склады",
    S.UNIT_ECONOMICS_WB: "Юнит-экономика 1С · Wildberries",
    S.STOCK: "Сток · Остатки и склады",
    S.STOCK_TOTAL: "Остатки Тотал",
    S.STOCK_SUPPLIES: "Поставки",
    S.STOCK_INBOUND: "Поставки FBO",
    S.STOCK_RANDOMIZER: "Рандомайзер",
    S.STOCK_COST_REPORT: "Движение и ЗЦ",
    S.STOCK_OPERATIONS: "История операций в кабинете",
    S.UNIT_ECONOMICS_1C: "Юнит-экономика 1С · Wildberries",
    S.UNIT_ECONOMICS_OZON: "Юнит-экономика 1С · Ozon",
    S.UNIT_ECONOMICS_YANDEX: "Юнит-экономика 1С · Яндекс Маркет",
    S.REPORT_UNIT_PROFIT: "Юниточная прибыль",
    S.REPORT_TARGET_PRICE: "Целевая цена",
    S.AI_AGENTS: "ИИ-агенты",
    S.ADMIN_USERS: "Админ-панель · Сотрудники и журнал",
    S.ADMIN_GOOGLE_EXPORT: "Выгрузка в Google Таблицы",
    S.ADMIN_INTEGRATIONS: "API-ключи и фоновые выгрузки",
}
SECTION_PATHS = {
    S.STOCK_BALANCES: "/stock",
    S.UNIT_ECONOMICS_WB: "/sales/unit-economics-1c",
    S.STOCK: "/stock",
    S.STOCK_TOTAL: "/stock/total",
    S.STOCK_SUPPLIES: "/stock/supplies",
    S.STOCK_INBOUND: "/stock/inbound",
    S.STOCK_RANDOMIZER: "/stock/randomizer",
    S.STOCK_COST_REPORT: "/stock/cost-report",
    S.STOCK_OPERATIONS: "/stock",
    S.UNIT_ECONOMICS_1C: "/sales/unit-economics-1c",
    S.UNIT_ECONOMICS_OZON: "/sales/unit-economics-1c/ozon",
    S.UNIT_ECONOMICS_YANDEX: "/sales/unit-economics-1c/yandex-market",
    S.REPORT_UNIT_PROFIT: "/sales/unit-economics-1c/reports/unit-profit",
    S.REPORT_TARGET_PRICE: "/sales/unit-economics-1c/reports/target-price",
    S.AI_AGENTS: "/ai-agents",
    S.ADMIN_USERS: "/admin",
    S.ADMIN_GOOGLE_EXPORT: "/admin/google-export",
    S.ADMIN_INTEGRATIONS: "/admin/integrations",
}
SECTION_DESCRIPTIONS = {
    S.STOCK_BALANCES: "Остатки по кабинетам, склады, приёмка, перемещения и отгрузки.",
    S.UNIT_ECONOMICS_WB: "Таблица WB, расчёты, цены и параметры товаров.",
    S.STOCK: "Остатки по кабинетам, склады, приёмка, перемещения и отгрузки.",
    S.STOCK_TOTAL: "Общий остаток по кабинетам и выгрузка в Excel.",
    S.STOCK_SUPPLIES: "План поставок: просмотр, добавление и изменение ручных записей.",
    S.STOCK_INBOUND: "Входящие поставки маркетплейсов; изменение разрешает ручное обновление.",
    S.STOCK_RANDOMIZER: "Сверка остатков WB; изменение разрешает выбирать новый артикул.",
    S.STOCK_COST_REPORT: "Движение товаров, закупочная стоимость и отметки перевода на FBS.",
    S.STOCK_OPERATIONS: "История движений внутри кабинета и выгрузка операций.",
    S.UNIT_ECONOMICS_1C: "Таблица WB, расчёты, цены и параметры товаров.",
    S.UNIT_ECONOMICS_OZON: "Вкладка Ozon находится в разработке.",
    S.UNIT_ECONOMICS_YANDEX: "Товары Яндекс Маркета, калькулятор и настройки выкупа.",
    S.REPORT_UNIT_PROFIT: "Прибыль за выбранный период и выгрузка в Excel.",
    S.REPORT_TARGET_PRICE: "Рекомендованные цены; изменение разрешает задавать цели товара.",
    S.AI_AGENTS: "Просмотр своих API-ключей; изменение разрешает выпуск и отзыв. Данные ограничены правами вкладок.",
    S.ADMIN_USERS: "Доступна администраторам. Изменение — управление сотрудниками в пределах своей роли.",
    S.ADMIN_GOOGLE_EXPORT: "Только суперадминистратор: настройки и запуск выгрузки.",
    S.ADMIN_INTEGRATIONS: "Только суперадминистратор: ключи маркетплейсов и фоновые задания.",
}
SECTION_PARENTS = {
    **{section: S.STOCK for section in SECTION_GROUPS[0][1] if section is not S.STOCK},
    **{
        section: S.UNIT_ECONOMICS_1C
        for section in (
            S.UNIT_ECONOMICS_WB,
            S.UNIT_ECONOMICS_OZON,
            S.UNIT_ECONOMICS_YANDEX,
            S.REPORT_UNIT_PROFIT,
            S.REPORT_TARGET_PRICE,
        )
    },
}
READ_ONLY_SECTIONS = {S.STOCK_TOTAL, S.STOCK_OPERATIONS, S.UNIT_ECONOMICS_OZON, S.REPORT_UNIT_PROFIT}
SUPERADMIN_SECTIONS = {S.ADMIN_GOOGLE_EXPORT, S.ADMIN_INTEGRATIONS}
SECTION_MARKETPLACES = {
    S.STOCK_RANDOMIZER: "WB",
    S.UNIT_ECONOMICS_WB: "WB",
    S.UNIT_ECONOMICS_OZON: "OZON",
    S.UNIT_ECONOMICS_YANDEX: "YANDEX MARKET",
    S.REPORT_UNIT_PROFIT: "WB",
    S.REPORT_TARGET_PRICE: "WB",
}
_ACCESS_LEVEL = {L.NONE: 0, L.READ: 1, L.WRITE: 2}


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/") or path == prefix + ".xlsx"


def section_for_path(path: str) -> SectionName | None:
    path = path.rstrip("/") or "/"
    for prefix, section in (
        ("/stock/total", S.STOCK_TOTAL),
        ("/stock/supplies", S.STOCK_SUPPLIES),
        ("/stock/planning", S.STOCK_SUPPLIES),
        ("/stock/inbound", S.STOCK_INBOUND),
        ("/stock/randomizer", S.STOCK_RANDOMIZER),
        ("/stock/cost-report", S.STOCK_COST_REPORT),
        ("/sales/unit-economics-1c/reports/unit-profit", S.REPORT_UNIT_PROFIT),
        ("/api/unit-economics-1c/reports/unit-profit", S.REPORT_UNIT_PROFIT),
        ("/sales/unit-economics-1c/reports/target-price", S.REPORT_TARGET_PRICE),
        ("/api/unit-economics-1c/reports/target-price", S.REPORT_TARGET_PRICE),
        ("/sales/unit-economics-1c/ozon", S.UNIT_ECONOMICS_OZON),
        ("/api/unit-economics-1c/ozon", S.UNIT_ECONOMICS_OZON),
        ("/sales/unit-economics-1c/yandex-market", S.UNIT_ECONOMICS_YANDEX),
        ("/api/unit-economics-1c/yandex-market", S.UNIT_ECONOMICS_YANDEX),
        ("/ai-agents", S.AI_AGENTS),
        ("/api/ai-agents", S.AI_AGENTS),
        ("/admin/google-export", S.ADMIN_GOOGLE_EXPORT),
        ("/admin/integrations", S.ADMIN_INTEGRATIONS),
        ("/api/admin/integrations", S.ADMIN_INTEGRATIONS),
        ("/admin/sync-stock", S.STOCK_BALANCES),
        ("/admin", S.ADMIN_USERS),
    ):
        if _under(path, prefix):
            return section
    if _under(path, "/sales/unit-economics-1c") or _under(path, "/api/unit-economics-1c"):
        return S.UNIT_ECONOMICS_WB
    if _under(path, "/stock"):
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[2] in {"operations", "operations.xlsx"}:
            return S.STOCK_OPERATIONS
        return S.STOCK_BALANCES
    return None


def _legacy_level(user: User, section: SectionName) -> SectionAccessLevel:
    if user.access_profile is not None:
        if section is S.STOCK:
            return L.WRITE if profile_has_permission(user, ActionPermission.STOCK_BALANCE_VIEW) else L.NONE
        markets = accessible_marketplaces(user)
        if "WB" not in markets:
            return (
                L.READ
                if "YANDEX MARKET" in markets
                and profile_has_permission(user, ActionPermission.UNIT_ECONOMICS_VIEW)
                else L.NONE
            )
        if profile_has_permission(user, ActionPermission.UNIT_ECONOMICS_EDIT):
            return L.WRITE
        return L.READ if profile_has_permission(user, ActionPermission.UNIT_ECONOMICS_VIEW) else L.NONE
    configured = user.section_access.get(section)
    if configured is not None:
        return configured
    return L.READ if section is S.STOCK and not user.can_edit_stock else L.WRITE


def access_limit(user: User | None, section: SectionName) -> SectionAccessLevel:
    user = coerce_user(user)
    if user is None or section not in SECTION_PATHS:
        return L.NONE
    if user.role is Role.SUPERADMIN:
        return L.READ if section in READ_ONLY_SECTIONS else L.WRITE
    if section in SUPERADMIN_SECTIONS:
        return L.NONE
    if section is S.ADMIN_USERS:
        return (L.WRITE if user.can_manage_users else L.READ) if user.role is Role.ADMIN else L.NONE
    if user.access_profile is not None or user.access_scopes:
        marketplace = SECTION_MARKETPLACES.get(section)
        if marketplace and marketplace not in accessible_marketplaces(user):
            return L.NONE
        if user.access_profile is not None and (
            section in SECTION_GROUPS[1][1] or section in SECTION_GROUPS[2][1]
        ):
            if not profile_has_permission(user, ActionPermission.UNIT_ECONOMICS_VIEW):
                return L.NONE
            if not profile_has_permission(user, ActionPermission.UNIT_ECONOMICS_EDIT):
                return L.READ
        if section is S.STOCK_TOTAL and not profile_has_permission(user, ActionPermission.STOCK_TOTAL_VIEW):
            return L.NONE
    return L.READ if section in READ_ONLY_SECTIONS else L.WRITE


def default_access_level(user: User, section: SectionName) -> SectionAccessLevel:
    if section is S.AI_AGENTS:
        return (
            L.WRITE
            if any(has_access(user, item) for _, group in SECTION_GROUPS[:3] for item in group)
            else L.NONE
        )
    if section is S.ADMIN_USERS or section in SUPERADMIN_SECTIONS:
        return access_limit(user, section)
    parent = SECTION_PARENTS.get(section, section)
    return _legacy_level(user, parent)


def access_level(user: User | None, section: SectionName) -> SectionAccessLevel:
    user = coerce_user(user)
    if user is None or section not in SECTION_PATHS:
        return L.NONE
    if user.role is Role.SUPERADMIN:
        return L.WRITE
    if section in {S.STOCK, S.UNIT_ECONOMICS_1C}:
        return _legacy_level(user, section)
    configured = user.section_access.get(section)
    level = configured if configured is not None else default_access_level(user, section)
    return min((level, access_limit(user, section)), key=_ACCESS_LEVEL.get)


def has_access(user: User | None, section: SectionName, required: SectionAccessLevel = L.READ) -> bool:
    return _ACCESS_LEVEL[access_level(user, section)] >= _ACCESS_LEVEL[required]


def section_path(user: User | None, section: SectionName) -> str:
    if section is S.STOCK_OPERATIONS:
        stores = accessible_stores(user)
        return f"/stock/{stores[0]}/operations" if stores else "/access-denied"
    return SECTION_PATHS[section]


def landing_path(user: User | None) -> str:
    for section in (item for _, group in SECTION_GROUPS for item in group):
        if has_access(user, section):
            return section_path(user, section)
    return "/access-denied"


def active_section(active: str) -> SectionName | None:
    return {
        "unit_1c_settings": S.UNIT_ECONOMICS_WB,
        "unit_1c_wb": S.UNIT_ECONOMICS_WB,
        "unit_1c_ozon": S.UNIT_ECONOMICS_OZON,
        "unit_1c_yandex": S.UNIT_ECONOMICS_YANDEX,
        "unit_1c_reports": S.REPORT_UNIT_PROFIT,
        "unit_1c_target_price": S.REPORT_TARGET_PRICE,
        "stock": S.STOCK_BALANCES,
        "stock_total": S.STOCK_TOTAL,
        "stock_supplies": S.STOCK_SUPPLIES,
        "stock_inbound": S.STOCK_INBOUND,
        "stock_randomizer": S.STOCK_RANDOMIZER,
        "stock_cost_report": S.STOCK_COST_REPORT,
        "stock_operations": S.STOCK_OPERATIONS,
        "ai_agents": S.AI_AGENTS,
        "admin": S.ADMIN_USERS,
        "admin_google_export": S.ADMIN_GOOGLE_EXPORT,
        "admin_integrations": S.ADMIN_INTEGRATIONS,
    }.get(active)
