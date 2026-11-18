from collections.abc import Awaitable, Callable
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.access.auth import SESSION_TTL_DAYS
from app.access.resources import accessible_stores
from app.config import LOCAL_UI_ORIGIN, settings
from app.core.constants import UI_AUTH, HTTPStatus, UiAuthError
from app.dto.identity import Credentials, SessionToken, User, UserId
from app.dto.ui import UiSession, UiSessionError
from app.web.dependencies import IdentityServiceDependency


class UiAuthRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                error = UiSessionError(detail=UiAuthError.INVALID_LOGIN_INPUT)
                return JSONResponse(
                    error.model_dump(mode="json"), status_code=HTTPStatus.UNPROCESSABLE_ENTITY
                )

        return handle


router = APIRouter(
    route_class=UiAuthRoute,
    responses={HTTPStatus.NOT_FOUND: {"model": UiSessionError}},
)


def session_view(user: User) -> UiSession:
    return UiSession(
        id=user.id,
        full_name=user.full_name,
        first_store_slug=next(iter(accessible_stores(user)), None),
    )


def session_cookie() -> str:
    return UI_AUTH.local_cookie if settings.ui_origin == LOCAL_UI_ORIGIN else UI_AUTH.secure_cookie


def session_token(request: Request) -> SessionToken | None:
    try:
        return SessionToken(value=request.cookies.get(session_cookie(), ""))
    except ValidationError:
        return None


@router.post(
    UI_AUTH.login_path,
    responses={
        HTTPStatus.UNAUTHORIZED: {"model": UiSessionError},
        HTTPStatus.FORBIDDEN: {"model": UiSessionError},
        HTTPStatus.UNPROCESSABLE_ENTITY: {"model": UiSessionError},
    },
)
def login(credentials: Credentials, response: Response, identities: IdentityServiceDependency) -> UiSession:
    user = identities.authenticate(credentials)
    if user is None:
        raise HTTPException(HTTPStatus.UNAUTHORIZED, UiAuthError.INVALID_CREDENTIALS)

    token = identities.start_session(UserId(user.id))
    response.set_cookie(
        session_cookie(),
        token.value,
        httponly=True,
        secure=settings.ui_origin != LOCAL_UI_ORIGIN,
        samesite="lax",
        max_age=timedelta(days=SESSION_TTL_DAYS) // timedelta(seconds=1),
        path="/",
    )
    return session_view(user)


@router.get(UI_AUTH.session_path, responses={HTTPStatus.UNAUTHORIZED: {"model": UiSessionError}})
def session(request: Request, identities: IdentityServiceDependency) -> UiSession:
    token = session_token(request)
    user = identities.user_for_token(token) if token is not None else None
    if user is None:
        raise HTTPException(HTTPStatus.UNAUTHORIZED, UiAuthError.AUTH_REQUIRED)
    return session_view(user)


@router.post(
    UI_AUTH.logout_path,
    status_code=HTTPStatus.NO_CONTENT,
    responses={HTTPStatus.FORBIDDEN: {"model": UiSessionError}},
)
def logout(request: Request, identities: IdentityServiceDependency) -> Response:
    token = session_token(request)
    if token is not None:
        identities.end_session(token)
    response = Response(status_code=HTTPStatus.NO_CONTENT)
    response.delete_cookie(
        session_cookie(),
        httponly=True,
        secure=settings.ui_origin != LOCAL_UI_ORIGIN,
        samesite="lax",
        path="/",
    )
    return response
