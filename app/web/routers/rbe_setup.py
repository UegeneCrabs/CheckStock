"""Dedicated Yandex Market credential screen for the standalone RBE service."""

from __future__ import annotations

import html
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, SecretStr

from app import db
from app.access import auth
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.integrations import credentials
from app.web.templating import fill_template, render_page
from app.yandex import api as yandex_api
from app.yandex import tokens as yandex_tokens

router = APIRouter()


class YandexConnectionPayload(BaseModel):
    store_slug: str = Field(min_length=1, max_length=100)
    business_id: int = Field(ge=1)
    api_key: SecretStr = Field(min_length=1, max_length=16_384)


def _require_superadmin(request: Request) -> None:
    if not auth.has_role(request.state.user, "superadmin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _campaign_ids_for_business(api_key: str, business_id: int) -> list[int]:
    """Prove access before persisting a key and derive its campaign boundary."""

    campaigns = yandex_api.get_campaigns(api_key)
    matches = [
        yandex_api.normalize_campaign(campaign)
        for campaign in campaigns
        if int(yandex_api.normalize_campaign(campaign)["business_id"] or 0) == business_id
    ]
    if not matches:
        raise ValueError("Ключ не предоставляет доступ к указанному Business ID")
    return sorted(
        {
            int(campaign["campaign_id"])
            for campaign in matches
            if campaign.get("campaign_id") is not None and int(campaign["campaign_id"]) > 0
        }
    )


def _connections() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for store_slug, store in STORES.items():
        for account in yandex_tokens.get_accounts(store_slug):
            rows.append(
                {
                    "store_slug": store_slug,
                    "store_name": store.name,
                    "business_id": account["business_id"],
                    "campaign_ids": account.get("campaign_ids") or [],
                }
            )
    return sorted(rows, key=lambda row: (str(row["store_name"]), int(row["business_id"])))


def _connection_rows() -> str:
    items = _connections()
    if not items:
        return '<tr><td colspan="4" class="rbe-connections__empty">Подключений пока нет.</td></tr>'
    return "".join(
        "<tr>"
        f"<td>{html.escape(str(item['store_name']))}</td>"
        f"<td>{int(item['business_id'])}</td>"
        f"<td>{', '.join(map(str, item['campaign_ids'])) or 'Определяются автоматически'}</td>"
        '<td><button type="button" class="rbe-danger" '
        f"data-rbe-remove data-store=\"{html.escape(str(item['store_slug']))}\" "
        f"data-business=\"{int(item['business_id'])}\">Удалить</button></td>"
        "</tr>"
        for item in items
    )


@router.get("/rbe/settings/yandex", response_class=HTMLResponse)
async def rbe_yandex_settings(request: Request):
    _require_superadmin(request)
    stores = "".join(
        f'<option value="{html.escape(slug)}">{html.escape(store.name)}</option>'
        for slug, store in STORES.items()
    )
    content = fill_template(
        "rbe/yandex-settings.html",
        stores=stores,
        connections=_connection_rows(),
    )
    return render_page(
        "RBE — Подключения Яндекс Маркета",
        "rbe_yandex_settings",
        content,
        request.state.user,
        "content--rbe-settings",
    )


@router.post("/api/rbe/yandex-connections")
async def create_yandex_connection(request: Request, payload: YandexConnectionPayload):
    _require_superadmin(request)
    store_slug = payload.store_slug.strip().lower()
    if store_slug not in STORES:
        return JSONResponse({"ok": False, "error": "Выберите магазин из списка"}, status_code=400)
    api_key = payload.api_key.get_secret_value().strip()
    try:
        campaign_ids = await run_in_threadpool(_campaign_ids_for_business, api_key, payload.business_id)
        await run_in_threadpool(
            credentials.save_yandex_account,
            store_slug,
            payload.business_id,
            api_key,
            campaign_ids,
        )
    except (ValueError, credentials.CredentialStorageError, yandex_api.YandexApiError) as error:
        message = error.friendly if isinstance(error, yandex_api.YandexApiError) else str(error)
        return JSONResponse({"ok": False, "error": message}, status_code=400)

    actor = request.state.user
    await run_in_threadpool(
        db.log_action,
        actor.id,
        actor.full_name,
        "Подключён API-ключ Яндекс Маркета",
        f"{STORES[store_slug].name}: Business ID {payload.business_id}",
        datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
    )
    return {
        "ok": True,
        "message": "Доступ к кабинету подтверждён, ключ сохранён",
        "connection": {
            "store_slug": store_slug,
            "store_name": STORES[store_slug].name,
            "business_id": payload.business_id,
            "campaign_ids": campaign_ids,
        },
    }


@router.delete("/api/rbe/yandex-connections/{store_slug}/{business_id}")
async def delete_yandex_connection(request: Request, store_slug: str, business_id: int):
    _require_superadmin(request)
    store_slug = store_slug.strip().lower()
    if store_slug not in STORES or business_id < 1:
        raise HTTPException(status_code=404, detail="Подключение не найдено")
    try:
        await run_in_threadpool(credentials.delete_yandex_account, store_slug, business_id)
    except credentials.CredentialStorageError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
    return {"ok": True, "message": "Подключение удалено"}
