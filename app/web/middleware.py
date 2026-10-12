import logging
import time

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError

from app.access import auth
from app.config import settings
from app.core.logging_config import new_request_id, request_id_context
from app.dto.identity import SessionToken

PUBLIC_PATHS = {"/healthz", "/readyz", "/login", "/logout"}
QUIET_PATH_PREFIXES = ("/static/",)
logger = logging.getLogger(__name__)


async def authentication_middleware(request: Request, call_next):

    path = request.url.path

    from app.web.routers.agent_mcp import PUBLIC_PATHS as MCP_PUBLIC_PATHS

    if path in MCP_PUBLIC_PATHS:
        # These endpoints enforce OAuth/session authentication themselves. Never
        # redirect the MCP client to an HTML login page instead of its 401 challenge.
        request.state.user = None
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    if path.startswith("/api/agent/v1/"):
        request.state.user = None
        if request.method != "GET":
            return JSONResponse({"detail": "Read-only API"}, status_code=405, headers={"Allow": "GET"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

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

    if user is None or not user.is_active:
        wants_json = "application/json" in request.headers.get("accept", "") or (
            request.headers.get("x-requested-with") == "fetch"
        )
        if wants_json or request.method != "GET":
            return JSONResponse({"ok": False, "error": "Требуется вход в систему"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    response = await call_next(request)
    if path.startswith(
        (
            "/finance-reports/",
            "/api/finance-reports/",
            "/analytics/",
            "/api/analytics/",
            "/sales/unit-economics-1c",
            "/api/unit-economics-1c",
            "/admin/integrations",
            "/api/admin/integrations",
        )
    ):
        response.headers["Cache-Control"] = "private, no-store"
    return response


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
        if request.url.path.startswith(
            ("/finance-reports/", "/api/finance-reports/", "/api/admin/integrations/yandex-finance")
        ):
            response.headers["Cache-Control"] = "private, no-store"
        return response
    finally:
        request_id_context.reset(token)
