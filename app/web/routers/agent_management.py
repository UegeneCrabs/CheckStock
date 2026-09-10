from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent_access import create_credential, credential_lock, read_credentials, save_credentials
from app.config import settings
from app.dto.identity import SectionName, User
from app.section_access import has_access
from app.web.templating import fill_template, render_page

router = APIRouter()


def owner(request: Request):
    user = request.state.user
    if user is None or not user.is_active:
        raise HTTPException(401, "Требуется вход в систему")
    if not any(has_access(user, section) for section in SectionName):
        raise HTTPException(403, "Нет доступа к разделам аналитики")
    if request.method != "GET":
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if request.headers.get("x-agent-management") != "1" or (origin and origin != expected):
            raise HTTPException(403, "Обновите страницу и повторите действие")
    return user


Owner = Annotated[User, Depends(owner)]


class KeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    days: int = Field(default=30, ge=1, le=180)


async def storage_call(function):
    try:
        return await run_in_threadpool(function)
    except FileExistsError as error:
        raise HTTPException(409, "Ключи сейчас обновляются. Повторите через несколько секунд.") from error
    except (OSError, ValueError) as error:
        raise HTTPException(503, "Хранилище ключей недоступно. Обратитесь к администратору.") from error


@router.get("/ai-agents", response_class=HTMLResponse)
async def agent_page(request: Request, user: Owner):
    return HTMLResponse(
        render_page("CheckStock — ИИ-агенты", "ai_agents", fill_template("agent_management.html"), user),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/ai-agents/keys")
async def list_keys(response: Response, user: Owner):
    response.headers["Cache-Control"] = "no-store"

    def load():
        now = datetime.now(UTC)
        return {
            "keys": [
                {
                    "id": item.key_id,
                    "name": item.name,
                    "expires_at": item.expires_at.isoformat(),
                    "active": item.expires_at > now,
                }
                for item in read_credentials(settings.agent_tokens_path)
                if item.user_id == user.id
            ]
        }

    return await storage_call(load)


@router.post("/api/ai-agents/keys", status_code=201)
async def issue_key(payload: KeyRequest, response: Response, user: Owner):
    response.headers["Cache-Control"] = "no-store"

    def issue():
        path = settings.agent_tokens_path
        with credential_lock(path):
            records = read_credentials(path)
            now = datetime.now(UTC)
            if sum(item.user_id == user.id and item.expires_at > now for item in records) >= 20:
                raise HTTPException(409, "У вас уже 20 активных ключей. Отзовите ненужный.")
            token, record = create_credential(user.id, now + timedelta(days=payload.days))
            record = record.model_copy(update={"name": payload.name})
            save_credentials(path, [*records, record])
            return {"id": record.key_id, "token": token, "expires_at": record.expires_at.isoformat()}

    return await storage_call(issue)


@router.delete("/api/ai-agents/keys/{key_id}", status_code=204)
async def revoke_key(key_id: str, response: Response, user: Owner):
    response.headers["Cache-Control"] = "no-store"

    def revoke():
        path = settings.agent_tokens_path
        with credential_lock(path):
            records = read_credentials(path)
            remaining = [item for item in records if not (item.key_id == key_id and item.user_id == user.id)]
            if len(remaining) == len(records):
                raise HTTPException(404, "Ключ не найден")
            save_credentials(path, remaining)

    await storage_call(revoke)
