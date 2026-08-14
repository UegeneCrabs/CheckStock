from __future__ import annotations

from app.dto.identity import Role, SectionAccessLevel, SectionName, User, coerce_user

SECTION_LABELS: dict[SectionName, str] = {
    SectionName.SALES: "Продажи",
    SectionName.DECISION_CENTER: "Центр решений",
    SectionName.EPHEMERIDES: "Эфемериды",
    SectionName.RNP: "РНП",
    SectionName.UNIT_ECONOMICS: "Юнит-экономика",
    SectionName.SUPPLY: "Снабжение",
    SectionName.STOCK: "Сток · Остатки",
    SectionName.STOCK_OVERVIEW: "Сток · Аналитика остатков",
}

SECTION_PATHS: dict[SectionName, str] = {
    SectionName.SALES: "/sales",
    SectionName.DECISION_CENTER: "/sales/decision-center",
    SectionName.EPHEMERIDES: "/sales/ephemerides",
    SectionName.RNP: "/sales/rnp",
    SectionName.UNIT_ECONOMICS: "/sales/unit-economics",
    SectionName.SUPPLY: "/supply",
    SectionName.STOCK: "/stock",
    SectionName.STOCK_OVERVIEW: "/stock-2",
}

_ACCESS_LEVEL = {
    SectionAccessLevel.NONE: 0,
    SectionAccessLevel.READ: 1,
    SectionAccessLevel.WRITE: 2,
}


def section_for_path(path: str) -> SectionName | None:
    if path.startswith("/api/decision-center") or path.startswith("/sales/decision-center"):
        return SectionName.DECISION_CENTER
    if path.startswith("/sales/ephemerides"):
        return SectionName.EPHEMERIDES
    if path.startswith("/api/rnp") or path.startswith("/sales/rnp"):
        return SectionName.RNP
    if path.startswith("/sales/unit-economics"):
        return SectionName.UNIT_ECONOMICS
    if path.startswith("/api/sales") or path.startswith("/sales"):
        return SectionName.SALES
    if path.startswith("/supply"):
        return SectionName.SUPPLY
    if path.startswith("/stock-2"):
        return SectionName.STOCK_OVERVIEW
    if path == "/stock" or path.startswith("/stock/"):
        return SectionName.STOCK
    return None


def access_level(user: User | None, section: SectionName) -> SectionAccessLevel:
    user = coerce_user(user)
    if user is None:
        return SectionAccessLevel.NONE
    if user.role is Role.SUPERADMIN:
        return SectionAccessLevel.WRITE
    configured = user.section_access.get(section)
    if configured is not None:
        return configured
    if section is SectionName.STOCK and not user.can_edit_stock:
        return SectionAccessLevel.READ
    return SectionAccessLevel.WRITE


def has_access(
    user: User | None,
    section: SectionName,
    required: SectionAccessLevel = SectionAccessLevel.READ,
) -> bool:
    return _ACCESS_LEVEL[access_level(user, section)] >= _ACCESS_LEVEL[required]


def landing_path(user: User | None) -> str:
    for section in SectionName:
        if has_access(user, section):
            return SECTION_PATHS[section]
    normalized = coerce_user(user)
    if normalized is not None and normalized.role in {Role.ADMIN, Role.SUPERADMIN}:
        return "/admin"
    return "/access-denied"


def active_section(active: str) -> SectionName | None:
    mapping = {
        "sales": SectionName.SALES,
        "sales_decision": SectionName.DECISION_CENTER,
        "sales_ephemerides": SectionName.EPHEMERIDES,
        "sales_rnp": SectionName.RNP,
        "sales_unit": SectionName.UNIT_ECONOMICS,
        "sales_wb_fbs": SectionName.UNIT_ECONOMICS,
        "sales_ozon": SectionName.UNIT_ECONOMICS,
        "sales_yandex": SectionName.UNIT_ECONOMICS,
        "supply": SectionName.SUPPLY,
        "stock": SectionName.STOCK,
        "stock2": SectionName.STOCK_OVERVIEW,
    }
    return mapping.get(active)
