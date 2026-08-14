from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from app import auth
from app.dto.identity import ActivityHeartbeat, SessionToken
from app.section_access import section_for_path
from app.web.dependencies import ContainerDependency
from app.web.templating import render_access_denied_page

router = APIRouter()


@router.post("/api/activity/heartbeat")
async def activity_heartbeat(
    request: Request,
    payload: ActivityHeartbeat,
    container: ContainerDependency,
):
    try:
        token = SessionToken(value=request.cookies.get(auth.SESSION_COOKIE, ""))
    except ValidationError:
        return JSONResponse({"ok": False, "error": "Сессия не найдена"}, status_code=401)
    await run_in_threadpool(
        container.usage.heartbeat,
        token.value,
        request.state.user.id,
        section_for_path(payload.path),
        payload.path,
        active=payload.active,
        page_view=payload.page_view,
    )
    return JSONResponse({"ok": True})


@router.get("/access-denied", response_class=HTMLResponse)
async def access_denied(request: Request):
    return render_access_denied_page(request.state.user)
