from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, NonNegativeInt, PositiveInt, RootModel, SecretStr

from app.dto.common import DtoModel


class SectionName(StrEnum):
    """Navigation and report identifiers; paths are defined in app.access.sections."""

    SALES = "sales"
    DECISION_CENTER = "decision_center"
    EPHEMERIDES = "ephemerides"
    RNP = "rnp"
    UNIT_ECONOMICS_1C = "unit_economics_1c"
    SUPPLY = "supply"
    STOCK = "stock"
    STOCK_OVERVIEW = "stock_overview"
    STOCK_TOTAL = "stock_total"
    STOCK_BALANCES = "stock_balances"
    UNIT_ECONOMICS_WB = "unit_economics_wb"
    STOCK_SUPPLIES = "stock_supplies"
    STOCK_INBOUND = "stock_inbound"
    STOCK_ARRIVALS = "stock_arrivals"
    STOCK_RANDOMIZER = "stock_randomizer"
    STOCK_COST_REPORT = "stock_cost_report"
    STOCK_OPERATIONS = "stock_operations"
    ANALYZER = "analyzer"
    ANALYTICS_SALES_API = "analytics_sales_api"
    FINANCE_YANDEX = "finance_yandex"
    UNIT_ECONOMICS_OZON = "unit_economics_ozon"
    UNIT_ECONOMICS_YANDEX = "unit_economics_yandex"
    REPORT_UNIT_PROFIT = "report_unit_profit"
    REPORT_SPP = "report_spp"
    REPORT_TARGET_PRICE = "report_target_price"
    REPORT_WB_CONDITIONS = "report_wb_conditions"
    REPORT_UNIT_PROFIT_YANDEX = "report_unit_profit_yandex"
    REPORT_TARGET_PRICE_YANDEX = "report_target_price_yandex"
    AI_AGENTS = "ai_agents"
    ADMIN_USERS = "admin_users"
    ADMIN_GOOGLE_EXPORT = "admin_google_export"
    ADMIN_INTEGRATIONS = "admin_integrations"


class User(DtoModel):
    id: PositiveInt
    full_name: str = Field(min_length=1, max_length=200)
    google_email: str = Field(default="", max_length=320)
    login: str = Field(min_length=1, max_length=100)
    password_hash: str = Field(default="", repr=False)
    is_active: bool = True
    created_at: datetime


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


class CreateUserCommand(DtoModel):
    full_name: str = Field(min_length=1, max_length=200)
    google_email: str = Field(default="", max_length=320)
    login: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.@+-]+$")
    password_hash: str = Field(min_length=1, repr=False)
    created_at: datetime


class CreateUserForm(DtoModel):
    full_name: str = Field(min_length=1, max_length=200)
    google_email: str = Field(min_length=1, max_length=320)
    login: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.@+-]+$")
    password: SecretStr = Field(min_length=8, max_length=1024)


class InitialUserSeed(DtoModel):
    full_name: str = Field(default="Сотрудник", min_length=1, max_length=200)
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


class UserId(RootModel[PositiveInt]):
    root: PositiveInt


class LoginQuery(DtoModel):
    login: str = Field(min_length=1, max_length=100)


class CreateSessionCommand(DtoModel):
    token: str = Field(min_length=32, max_length=256, repr=False)
    user_id: PositiveInt
    created_at: datetime
    expires_at: datetime


class UserActiveChange(DtoModel):
    user_id: PositiveInt
    is_active: bool


class UserPasswordChange(DtoModel):
    user_id: PositiveInt
    password_hash: str = Field(min_length=1, repr=False)


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
    PASSWORD = "password"


class AuditedUserMutation(DtoModel):
    kind: UserMutationKind
    user_id: PositiveInt
    activity: ActivityCommand
    is_active: bool | None = None
    password_hash: str | None = Field(default=None, repr=False)


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
    raw.setdefault("created_at", datetime(1970, 1, 1, tzinfo=UTC))
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
