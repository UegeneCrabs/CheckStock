from app.access.sections import has_access
from app.dto.identity import Role, SectionAccessLevel, SectionName, User, coerce_user

ROLE_LABELS: dict[Role, str] = {
    Role.SUPERADMIN: "Суперадминистратор",
    Role.ADMIN: "Администратор",
    Role.USER: "Сотрудник",
}


def has_role(user: User | None, minimum: Role) -> bool:
    user = coerce_user(user)
    levels = {Role.USER: 1, Role.ADMIN: 2, Role.SUPERADMIN: 3}
    return bool(user and levels[user.role] >= levels[minimum])


def can_edit_stock(user: User | None) -> bool:
    user = coerce_user(user)
    return has_access(user, SectionName.STOCK_BALANCES, SectionAccessLevel.WRITE)


def can_manage_users(user: User | None) -> bool:
    user = coerce_user(user)
    return bool(user and has_role(user, Role.ADMIN) and user.can_manage_users)
