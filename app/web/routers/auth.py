from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app import auth
from app.config import settings
from app.dto.identity import Credentials, SessionToken, UserId
from app.web.dependencies import IdentityServiceDependency
from app.web.templating import fill_template

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, identities: IdentityServiceDependency):
    raw_token = request.cookies.get(auth.SESSION_COOKIE, "")
    try:
        token = SessionToken(value=raw_token)
    except ValidationError:
        user = None
    else:
        user = await run_in_threadpool(identities.user_for_token, token)
    if user is not None:
        return RedirectResponse("/sales", status_code=303)
    return fill_template("login.html", error="")


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    credentials: Annotated[Credentials, Form()],
    identities: IdentityServiceDependency,
):
    user = await run_in_threadpool(identities.authenticate, credentials)
    if user is None:
        page = fill_template(
            "login.html",
            error='<p class="login-error">Неверный логин или пароль</p>',
        )
        return HTMLResponse(page, status_code=401)

    token = await run_in_threadpool(identities.start_session, UserId(user.id))
    response = RedirectResponse("/sales", status_code=303)
    response.set_cookie(
        auth.SESSION_COOKIE,
        token.value,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=auth.SESSION_TTL_DAYS * 24 * 3600,
        path="/",
    )
    return response


@router.get("/logout")
async def logout(request: Request, identities: IdentityServiceDependency):
    raw_token = request.cookies.get(auth.SESSION_COOKIE, "")
    try:
        token = SessionToken(value=raw_token)
    except ValidationError:
        token = None
    if token is not None:
        await run_in_threadpool(identities.end_session, token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response
