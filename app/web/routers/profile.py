import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.access.authentication import require_active_user
from app.core.formatting import format_dt
from app.web.templating import fill_template, render_page

router = APIRouter()


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = require_active_user(request)
    content = fill_template(
        "account/profile.html",
        full_name=html.escape(user.full_name),
        email=html.escape(user.google_email or "Не указана"),
        login=html.escape(user.login),
        created_at=html.escape(format_dt(user.created_at.isoformat())),
    )
    return render_page("CheckStock — Профиль", "profile", content, user, "content--profile")
