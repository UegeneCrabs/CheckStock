from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web.templating import read_template, render_page

router = APIRouter()


@router.get("/supply", response_class=HTMLResponse)
async def supply(request: Request):
    content = read_template("supply_content.html")
    return render_page("CheckStock — Снабжение", "supply", content, request.state.user)
