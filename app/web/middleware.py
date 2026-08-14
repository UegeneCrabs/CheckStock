import logging
import time

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError

from app import auth
from app.config import settings
from app.dto.identity import SectionAccessLevel, SessionToken
from app.logging_config import new_request_id, request_id_context
from app.section_access import has_access as has_section_access
from app.section_access import section_for_path
from app.stores import STORES
from app.web.access import has_store_access
from app.web.templating import render_access_denied_page

PUBLIC_PATHS = {"/healthz", "/readyz", "/login", "/logout"}
QUIET_PATH_PREFIXES = ("/static/",)
logger = logging.getLogger(__name__)


async def authentication_middleware(request: Request, call_next):

    path = request.url.path

    if path in PUBLIC_PATHS or path.startswith("/static/"):
        request.state.user = None
        return await call_next(request)

    try:
        token = SessionToken(value=request.cookies.get(auth.SESSION_COOKIE, ""))
    except ValidationError:
        user = None
    else:
        user = await run_in_threadpool(request.app.state.container.identity.user_for_token, token)
    request.state.user = user

    if user is None:
        wants_json = "application/json" in request.headers.get("accept", "") or (
            request.headers.get("x-requested-with") == "fetch"
        )
        if wants_json or request.method != "GET":
            return JSONResponse({"ok": False, "error": "Требуется вход в систему"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    section = section_for_path(path)
    read_only_post_paths = {"/sales/unit-economics/wb-fbs/calculate"}
    required_access = (
        SectionAccessLevel.READ
        if request.method in {"GET", "HEAD", "OPTIONS"} or path in read_only_post_paths
        else SectionAccessLevel.WRITE
    )
    if section is not None and not has_section_access(user, section, required_access):
        wants_json = "application/json" in request.headers.get("accept", "") or (
            request.headers.get("x-requested-with") == "fetch"
        )
        message = (
            "Раздел доступен только для просмотра"
            if required_access is SectionAccessLevel.WRITE
            and has_section_access(user, section, SectionAccessLevel.READ)
            else "Нет доступа к этому разделу"
        )
        if wants_json or request.method != "GET":
            return JSONResponse({"ok": False, "error": message}, status_code=403)
        return HTMLResponse(
            render_access_denied_page(user, section=section),
            status_code=403,
        )

    if path.startswith("/stock/"):
        parts = path.strip("/").split("/")
        store_slug = parts[1].lower() if len(parts) > 1 else ""
        if store_slug in STORES and not has_store_access(user, store_slug):
            wants_json = "application/json" in request.headers.get("accept", "") or (
                request.headers.get("x-requested-with") == "fetch"
            )
            if wants_json or request.method != "GET":
                return JSONResponse({"ok": False, "error": "Нет доступа к этому магазину"}, status_code=403)
            store_name = STORES[store_slug]["name"]
            return HTMLResponse(
                render_access_denied_page(
                    user,
                    heading="Нет доступа к кабинету",
                    description=(
                        f"Кабинет «{store_name}» недоступен для вашей учётной записи. "
                        "Если он нужен для работы, обратитесь к суперадминистратору."
                    ),
                ),
                status_code=403,
            )

    return await call_next(request)


async def request_logging_middleware(request: Request, call_next):
    request_id = new_request_id(request.headers.get("x-request-id"))
    token = request_id_context.set(request_id)
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.exception(
            "request_failed method=%s path=%s duration_ms=%s client=%s",
            request.method,
            request.url.path,
            elapsed_ms,
            request.client.host if request.client else "-",
        )
        raise
    else:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        quiet_path = request.url.path in {"/healthz", "/readyz"} or request.url.path.startswith(
            QUIET_PATH_PREFIXES
        )
        if not quiet_path:
            if response.status_code >= 500:
                log = logger.error
            elif response.status_code >= 400:
                log = logger.warning
            elif request.method not in {"GET", "HEAD", "OPTIONS"}:
                log = logger.info
            elif elapsed_ms >= settings.slow_request_threshold_ms:
                log = logger.info
            else:
                log = logger.debug
            log(
                "request_completed method=%s path=%s status=%s duration_ms=%s client=%s",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                request.client.host if request.client else "-",
            )
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_context.reset(token)
