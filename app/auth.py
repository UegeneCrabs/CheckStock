import logging
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.application.identity import IdentityService
from app.config import settings
from app.dto.identity import (
    CreateUserCommand,
    Credentials,
    PasswordHashRequest,
    PasswordVerification,
    Role,
    SessionToken,
    SuperadminSeed,
    User,
    UserId,
)
from app.identity_policy import can_edit_stock as policy_can_edit_stock
from app.identity_policy import can_manage_users as policy_can_manage_users
from app.identity_policy import has_role as policy_has_role
from app.infrastructure.database import database_for_path
from app.infrastructure.identity_repository import SqlAlchemyIdentityUnitOfWork
from app.repositories import core
from app.security import Pbkdf2PasswordService

logger = logging.getLogger(__name__)

SEED_PATH = settings.admin_seed_path
SESSION_COOKIE = "paketa_session"
SESSION_TTL_DAYS = settings.session_ttl_days
PBKDF2_ITERATIONS = settings.pbkdf2_iterations


def _now() -> datetime:
    return datetime.now(UTC)


def _service() -> IdentityService:
    return IdentityService(
        unit_of_work_factory=lambda: SqlAlchemyIdentityUnitOfWork(
            database_for_path(core.DB_PATH).session_factory
        ),
        password_service=Pbkdf2PasswordService(PBKDF2_ITERATIONS),
        session_ttl=timedelta(days=SESSION_TTL_DAYS),
    )


def hash_password(password: str) -> str:
    request = PasswordHashRequest(password=password)
    return Pbkdf2PasswordService(PBKDF2_ITERATIONS).hash(request).root


def verify_password(password: str, stored: str) -> bool:
    try:
        request = PasswordVerification(password=password, stored_hash=stored)
    except ValidationError:
        return False
    return Pbkdf2PasswordService(PBKDF2_ITERATIONS).verify(request).root


def authenticate(login: str, password: str) -> User | None:
    try:
        credentials = Credentials(login=login, password=password)
    except ValidationError:
        return None
    return _service().authenticate(credentials)


def start_session(user_id: int) -> str:
    return _service().start_session(UserId(user_id)).value


def end_session(token: str) -> None:
    if token:
        _service().end_session(SessionToken(value=token))


def user_for_token(token: str) -> User | None:
    if not token:
        return None
    try:
        session_token = SessionToken(value=token)
    except ValidationError:
        return None
    return _service().user_for_token(session_token)


def has_role(user: User | None, minimum: str) -> bool:
    try:
        required = Role(minimum)
    except ValueError:
        return False
    return policy_has_role(user, required)


def can_edit_stock(user: User | None) -> bool:
    return policy_can_edit_stock(user)


def can_manage_users(user: User | None) -> bool:
    return policy_can_manage_users(user)


def seed_superadmin() -> None:
    service = _service()
    if service.count_users().root > 0:
        return
    if not SEED_PATH.exists():
        logger.warning("superadmin_seed_missing path=%s", SEED_PATH)
        return
    try:
        seed = SuperadminSeed.model_validate_json(SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        logger.exception("superadmin_seed_invalid path=%s", SEED_PATH)
        return

    password_hash = Pbkdf2PasswordService(PBKDF2_ITERATIONS).hash(PasswordHashRequest(password=seed.password))
    user_id = service.create_user(
        CreateUserCommand(
            full_name=seed.full_name,
            google_email=seed.google_email,
            login=seed.login,
            password_hash=password_hash.root,
            role=Role.SUPERADMIN,
            created_at=_now(),
        )
    )
    logger.info("superadmin_seeded user_id=%s login=%s", user_id.root, seed.login)
