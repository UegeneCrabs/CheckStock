from fastapi import HTTPException, Request

from app.dto.identity import User, coerce_user


def is_active_user(user: User | None) -> bool:
    normalized = coerce_user(user)
    return bool(normalized is not None and normalized.is_active)


def require_active_user(request: Request) -> User:
    user = coerce_user(getattr(request.state, "user", None))
    if not is_active_user(user):
        raise HTTPException(401, "Требуется вход в систему")
    return user
