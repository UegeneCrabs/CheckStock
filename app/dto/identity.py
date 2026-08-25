from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, NonNegativeInt, PositiveInt, RootModel, SecretStr

from app.dto.common import DtoModel


class Role(StrEnum):
    USER = "user"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


class PermissionName(StrEnum):
    EDIT_STOCK = "can_edit_stock"
    MANAGE_USERS = "can_manage_users"


class SectionName(StrEnum):
    SALES = "sales"
    DECISION_CENTER = "decision_center"
    EPHEMERIDES = "ephemerides"
    RNP = "rnp"
    UNIT_ECONOMICS_1C = "unit_economics_1c"
    SUPPLY = "supply"
    STOCK = "stock"
    STOCK_OVERVIEW = "stock_overview"


class SectionAccessLevel(StrEnum):
    NONE = "none"
    READ = "read"
    WRITE = "write"


class StoreAccess(RootModel[tuple[str, ...]]):
    root: tuple[str, ...] = ()


class User(DtoModel):
    id: PositiveInt
    full_name: str = Field(min_length=1, max_length=200)
    google_email: str = Field(default="", max_length=320)
    login: str = Field(min_length=1, max_length=100)
    password_hash: str = Field(default="", repr=False)
    role: Role
    is_active: bool = True
    can_edit_stock: bool = True
    can_manage_users: bool = True
    created_at: datetime
    store_slugs: tuple[str, ...] = ()
    section_access: dict[SectionName, SectionAccessLevel] = Field(default_factory=dict)


class UserCollection(RootModel[tuple[User, ...]]):
    root: tuple[User, ...] = ()


class Credentials(DtoModel):
    login: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=1, max_length=1024)


class PasswordHashRequest(DtoModel):
    password: SecretStr = Field(min_length=1, max_length=1024)


class PasswordHash(RootModel[str]):
    root: str = Field(min_length=1)


class PasswordVerification(DtoModel):
    password: SecretStr
    stored_hash: str = Field(min_length=1, repr=False)


class AccessDecision(RootModel[bool]):
    root: bool


class RoleCheck(DtoModel):
    user: User | None
    minimum: Role


class CreateUserCommand(DtoModel):
    full_name: str = Field(min_length=1, max_length=200)
    google_email: str = Field(default="", max_length=320)
    login: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.@+-]+$")
    password_hash: str = Field(min_length=1, repr=False)
    role: Role
    created_at: datetime
    store_slugs: tuple[str, ...] = ()


class CreateUserForm(DtoModel):
    full_name: str = Field(min_length=1, max_length=200)
    google_email: str = Field(min_length=1, max_length=320)
    login: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.@+-]+$")
    password: SecretStr = Field(min_length=8, max_length=1024)
    role: Role
    store_slugs: tuple[str, ...] = Field(min_length=1)


class SuperadminSeed(DtoModel):
    full_name: str = Field(default="Суперадминистратор", min_length=1, max_length=200)
    google_email: str = Field(default="", max_length=320)
    login: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.@+-]+$")
    password: SecretStr = Field(min_length=1, max_length=1024)


class SessionData(DtoModel):
    token: str = Field(min_length=32, max_length=256, repr=False)
    user_id: PositiveInt
    created_at: datetime
    expires_at: datetime


class SessionToken(DtoModel):
    value: str = Field(min_length=1, max_length=256, repr=False)


class ActivityHeartbeat(DtoModel):
    path: str = Field(min_length=1, max_length=500)
    active: bool = True
    page_view: bool = False


class UserId(RootModel[PositiveInt]):
    root: PositiveInt


class LoginQuery(DtoModel):
    login: str = Field(min_length=1, max_length=100)


class CreateSessionCommand(DtoModel):
    token: str = Field(min_length=32, max_length=256, repr=False)
    user_id: PositiveInt
    created_at: datetime
    expires_at: datetime


class PermissionChange(DtoModel):
    user_id: PositiveInt
    permission: PermissionName
    allowed: bool


class UserActiveChange(DtoModel):
    user_id: PositiveInt
    is_active: bool


class UserPasswordChange(DtoModel):
    user_id: PositiveInt
    password_hash: str = Field(min_length=1, repr=False)


class UserStoreAccessChange(DtoModel):
    user_id: PositiveInt
    store_slugs: tuple[str, ...]


class UserRoleChange(DtoModel):
    user_id: PositiveInt
    role: Role


class UserSectionAccessChange(DtoModel):
    user_id: PositiveInt
    section_access: dict[SectionName, SectionAccessLevel]


class UserCountQuery(DtoModel):
    exclude_user_id: PositiveInt | None = None


class ExpiredSessionsCommand(DtoModel):
    now: datetime


class ActivityCommand(DtoModel):
    user_id: PositiveInt | None = None
    user_name: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=200)
    details: str = Field(default="", max_length=10_000)
    created_at: datetime


class UserMutationKind(StrEnum):
    DELETE = "delete"
    ACTIVE = "active"
    PERMISSION = "permission"
    STORES = "stores"
    PASSWORD = "password"
    ROLE = "role"
    SECTIONS = "sections"


class AuditedUserMutation(DtoModel):
    kind: UserMutationKind
    user_id: PositiveInt
    activity: ActivityCommand
    is_active: bool | None = None
    permission: PermissionName | None = None
    allowed: bool | None = None
    store_slugs: tuple[str, ...] = ()
    password_hash: str | None = Field(default=None, repr=False)
    role: Role | None = None
    section_access: dict[SectionName, SectionAccessLevel] = Field(default_factory=dict)


class AuditedCreateUser(DtoModel):
    user: CreateUserCommand
    activity: ActivityCommand


class ActivityLogQuery(DtoModel):
    limit: PositiveInt = 200


class WbTokenInfo(DtoModel):
    store_slug: str
    expires_at: datetime | None = None
    checked_at: datetime


class WbTokenInfoCollection(RootModel[tuple[WbTokenInfo, ...]]):
    root: tuple[WbTokenInfo, ...] = ()


class WbTokenInfoCommand(DtoModel):
    store_slug: str
    expires_at: datetime | None = None
    checked_at: datetime


class CountResult(RootModel[NonNegativeInt]):
    root: NonNegativeInt


def coerce_user(value: object) -> User | None:
    if value is None or isinstance(value, User):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("user must be a User DTO")
    raw = dict(value)
    raw.setdefault("id", 1)
    raw.setdefault("full_name", raw.get("login") or "Сотрудник")
    raw.setdefault("google_email", "")
    raw.setdefault("login", f"user-{raw.get('id', 0)}")
    raw.setdefault("password_hash", "")
    raw.setdefault("is_active", True)
    raw.setdefault("can_edit_stock", True)
    raw.setdefault("can_manage_users", True)
    raw.setdefault("created_at", datetime(1970, 1, 1, tzinfo=UTC))
    raw.setdefault("store_slugs", ())
    raw.setdefault("section_access", {})
    return User.model_validate(raw)


class ActivityEntry(DtoModel):
    id: PositiveInt
    user_id: PositiveInt | None = None
    user_name: str
    action: str
    details: str | None = None
    operation_id: PositiveInt | None = None
    created_at: datetime


class ActivityLog(RootModel[tuple[ActivityEntry, ...]]):
    root: tuple[ActivityEntry, ...] = ()
