"""
Авторизация сотрудников: хеширование паролей, сессии, роли.

Пароли хешируются PBKDF2-HMAC-SHA256 из стандартной библиотеки — новых
зависимостей не тянем, а стойкость для внутреннего инструмента достаточная
(200k итераций, случайная соль на каждый пароль). Формат строки в БД:

    pbkdf2_sha256$<итерации>$<соль_hex>$<хеш_hex>

Сессия — случайный токен в httponly-куке, сама сессия лежит в таблице
sessions. Логаут удаляет строку, поэтому "разлогинить" можно и на сервере.

Первый суперадмин создаётся при старте из secrets/admin_seed.json (файл в
.gitignore) — см. seed_superadmin(). Пароль в коде не хранится.
"""

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import db

SEED_PATH = Path(__file__).resolve().parent.parent / "secrets" / "admin_seed.json"

SESSION_COOKIE = "paketa_session"
SESSION_TTL_DAYS = 14

PBKDF2_ITERATIONS = 200_000

# Кто что может. Проверяется через has_role(user, "admin") и т.п.
ROLE_LEVEL = {"user": 1, "admin": 2, "superadmin": 3}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, AttributeError):
        return False
    # сравнение с постоянным временем — чтобы по времени ответа нельзя было подбирать хеш
    return hmac.compare_digest(digest.hex(), digest_hex)


def authenticate(login: str, password: str) -> dict | None:
    """Возвращает пользователя, если логин/пароль верны и он не заблокирован."""
    user = db.get_user_by_login((login or "").strip())
    if user is None or not user["is_active"]:
        return None
    if not verify_password(password or "", user["password_hash"]):
        return None
    return user


def start_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    db.create_session(
        token,
        user_id,
        now.isoformat(),
        (now + timedelta(days=SESSION_TTL_DAYS)).isoformat(),
    )
    return token


def end_session(token: str) -> None:
    if token:
        db.delete_session(token)


def user_for_token(token: str) -> dict | None:
    """Пользователь по куке сессии. Протухшая сессия удаляется сразу."""
    if not token:
        return None
    session = db.get_session(token)
    if session is None:
        return None

    try:
        expires = datetime.fromisoformat(session["expires_at"])
    except ValueError:
        db.delete_session(token)
        return None

    if expires < _now():
        db.delete_session(token)
        return None

    user = db.get_user(session["user_id"])
    if user is None or not user["is_active"]:
        return None
    return user


def has_role(user: dict | None, minimum: str) -> bool:
    if not user:
        return False
    return ROLE_LEVEL.get(user["role"], 0) >= ROLE_LEVEL.get(minimum, 99)


def can_edit_stock(user: dict | None) -> bool:
    """Можно ли этому сотруднику менять остатки.

    Разрешение отдельно от роли: сотрудник может числиться пользователем и при
    этом не иметь права проводить операции, пока его не допустили.
    Отсутствие поля считаем разрешением — так ведут себя учётки, заведённые
    до появления разрешений.
    """
    if not user:
        return False
    try:
        return bool(user["can_edit_stock"])
    except (KeyError, IndexError, TypeError):
        return True


def can_manage_users(user: dict | None) -> bool:
    """Можно ли заводить и править сотрудников.

    Нужна и роль, и разрешение: у тестового стенда роль суперадмина, но
    трогать живых сотрудников он не должен.
    """
    if not has_role(user, "admin"):
        return False
    try:
        return bool(user["can_manage_users"])
    except (KeyError, IndexError, TypeError):
        return True


def seed_superadmin() -> None:
    """Создаёт первого суперадмина из secrets/admin_seed.json, если в базе
    ещё нет ни одного пользователя. Повторные запуски ничего не делают."""
    if db.count_users() > 0:
        return
    if not SEED_PATH.exists():
        return

    try:
        data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    login = (data.get("login") or "").strip()
    password = data.get("password") or ""
    if not login or not password:
        return

    db.create_user(
        full_name=(data.get("full_name") or "Суперадмин").strip(),
        google_email=(data.get("google_email") or "").strip(),
        login=login,
        password_hash=hash_password(password),
        role="superadmin",
        created_at=_now().isoformat(),
    )
