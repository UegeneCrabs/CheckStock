from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app import db
from app.access.authentication import is_active_user
from app.access.economics_preview import PRIVATE_JOB_NAMES, can_preview, require_preview
from app.application.integration_views import IntegrationViews
from app.core.constants import JOBS, CredentialMarketplace, WebErrorCode
from app.core.domain import MOSCOW_TIMEZONE
from app.core.formatting import format_dt
from app.core.stores import STORES
from app.dto.integrations import (
    CredentialList,
    CredentialResult,
    CredentialUpdate,
    SyncSettingUpdate,
    WebFailure,
)
from app.dto.integrations import SyncSettingUpdate as TypedSyncSettingUpdate
from app.infrastructure.integration_repository import IntegrationRepository
from app.integrations import credentials as marketplace_credentials
from app.jobs import async_locks
from app.jobs import manual as manual_sync
from app.jobs import settings as sync_settings
from app.jobs.catalog import job_definitions
from app.jobs.locks import SyncJobBusyError
from app.jobs.tracking import queue_tracked
from app.wb import token_watch
from app.web import ozon_jobs
from app.web.cabinet_settings import render_cabinet_settings
from app.web.client_page import client_page
from app.web.dependencies import get_async_resources
from app.web.integration_route import IntegrationRoute
from app.web.templating import read_template

router = APIRouter(route_class=IntegrationRoute)


def _require_active_account(request: Request) -> None:
    if not is_active_user(request.state.user):
        raise HTTPException(status_code=403, detail=WebErrorCode.FORBIDDEN)


def _visible_jobs(user):
    return tuple(job for job in job_definitions() if job.name not in PRIVATE_JOB_NAMES or can_preview(user))


def _require_job_access(request: Request, job_name: str) -> None:
    if job_name in PRIVATE_JOB_NAMES:
        require_preview(request.state.user)


def _validate_target(store_slug: str, marketplace: str) -> None:
    if store_slug not in STORES:
        raise HTTPException(status_code=404, detail=WebErrorCode.NOT_FOUND)
    if marketplace not in CredentialMarketplace:
        raise HTTPException(status_code=404, detail=WebErrorCode.NOT_FOUND)


def _views(request: Request) -> IntegrationViews:
    resources = request.app.state.async_resources
    return IntegrationViews(
        IntegrationRepository(resources.database),
        resources.jobs,
        partial(async_locks.is_running, database=resources.database),
    )


def _failure(code: WebErrorCode, status: int) -> JSONResponse:
    return JSONResponse(WebFailure(code=code).model_dump(mode="json"), status_code=status)


@router.get("/api/admin/integrations/credentials", response_model=CredentialList)
async def credential_list(request: Request) -> CredentialList:
    _require_active_account(request)
    credentials = await run_in_threadpool(
        lambda: tuple(
            marketplace_credentials.credential_snapshot(slug, code)
            for slug in STORES
            for code in CredentialMarketplace
        )
    )
    return CredentialList(credentials=credentials)


@router.get("/api/admin/integrations/sync-jobs/catalog")
async def sync_job_catalog(request: Request):
    _require_active_account(request)
    return await ozon_jobs.views(get_async_resources(request)).catalog(_visible_jobs(request.state.user))


@router.get("/admin/integrations", response_class=HTMLResponse)
async def integrations_page(request: Request):
    _require_active_account(request)
    return client_page("integrations/index.html")


@router.get("/api/admin/integrations/sync-jobs/{job_name}/history")
async def sync_job_history(request: Request, job_name: str, limit: int = 50):
    _require_active_account(request)
    _require_job_access(request, job_name)
    definitions = {definition.name: definition for definition in job_definitions()}
    definition = definitions.get(job_name)
    if definition is None:
        return JSONResponse({"ok": False, "error": "Выгрузка не найдена"}, status_code=404)
    if ozon_jobs.owns(job_name) or job_name == JOBS.ozon_reputation.name:
        return await ozon_jobs.history(job_name, limit, get_async_resources(request))
    runs = await run_in_threadpool(_sync_history, job_name, min(max(limit, 1), 200))
    return {
        "ok": True,
        "job": {"name": definition.name, "title": definition.title},
        "retention_days": 30,
        "runs": runs,
    }


@router.get("/api/admin/integrations/sync-jobs")
async def sync_job_states(request: Request):
    _require_active_account(request)
    states = await run_in_threadpool(_sync_states, request.state.user)
    service = ozon_jobs.views(get_async_resources(request))
    for definition in _visible_jobs(request.state.user):
        if ozon_jobs.owns(definition.name):
            states.append((await service.state(definition.name)).model_dump(mode="json"))
    return {"ok": True, "states": states}


@router.post("/api/admin/integrations/sync-jobs/{job_name}/run")
async def run_sync_job(request: Request, job_name: str):
    _require_active_account(request)
    _require_job_access(request, job_name)
    definition = next((item for item in job_definitions() if item.name == job_name and item.manual_run), None)
    if definition is None:
        return JSONResponse({"ok": False, "error": "Выгрузка не найдена"}, status_code=404)
    if ozon_jobs.owns(job_name):
        result = await ozon_jobs.launch(request, job_name, get_async_resources(request))
        return JSONResponse(result.model_dump(mode="json"), status_code=202)
    config = await run_in_threadpool(sync_settings.configuration, job_name)
    if config["targets"] and not config["enabled_target_count"]:
        return JSONResponse(
            {"ok": False, "error": "Для обновления не выбран ни один магазин"}, status_code=409
        )
    try:
        if await run_in_threadpool(manual_sync.is_running, job_name):
            raise SyncJobBusyError()
        if job_name == "yandex_storefront_prices_sync":
            from app.repositories import yandex_storefront

            if pause := await run_in_threadpool(yandex_storefront.cooldown):
                return JSONResponse(pause, status_code=409)
        callback = manual_sync.callback_for(job_name)
        run_id = await run_in_threadpool(queue_tracked, job_name, callback)
    except SyncJobBusyError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=409)
    except Exception as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=502)
    actor = request.state.user
    await run_in_threadpool(
        db.log_action,
        actor.id,
        actor.full_name,
        "Запущена выгрузка вручную",
        definition.title,
        datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
    )
    return JSONResponse(
        {"ok": True, "run_id": run_id, "message": "Выгрузка запущена в фоне"}, status_code=202
    )


@router.put("/api/admin/integrations/sync-jobs/{job_name}/settings")
async def update_sync_job_setting(
    request: Request,
    job_name: str,
    payload: SyncSettingUpdate,
):
    _require_active_account(request)
    _require_job_access(request, job_name)
    if ozon_jobs.owns(job_name):
        return await ozon_jobs.save(
            request,
            job_name,
            TypedSyncSettingUpdate.model_validate(payload.model_dump()),
            get_async_resources(request),
        )
    try:
        config = await run_in_threadpool(
            sync_settings.save_setting,
            job_name,
            enabled=payload.enabled,
            store_slug=payload.store_slug.strip(),
            marketplace=payload.marketplace.strip(),
        )
    except ValueError as error:
        status_code = 404 if str(error) == "Выгрузка не найдена" else 400
        return JSONResponse({"ok": False, "error": str(error)}, status_code=status_code)

    actor = request.state.user
    target = "вся выгрузка"
    if payload.marketplace and not payload.store_slug:
        target = f"все кабинеты / {sync_settings.MARKETPLACE_LABELS[payload.marketplace]}"
    elif payload.store_slug:
        target = STORES[payload.store_slug].name
        if payload.marketplace:
            target += f" / {sync_settings.MARKETPLACE_LABELS[payload.marketplace]}"
    await run_in_threadpool(
        db.log_action,
        actor.id,
        actor.full_name,
        "Изменена автовыгрузка",
        f"{job_name}: {target} — {'включена' if payload.enabled else 'выключена'}",
        datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
    )
    return {"ok": True, "configuration": config}


@router.put("/api/admin/integrations/{store_slug}/{marketplace}", response_model=CredentialResult)
async def update_credential(
    request: Request,
    store_slug: str,
    marketplace: str,
    payload: CredentialUpdate,
):
    _require_active_account(request)
    _validate_target(store_slug, marketplace)
    try:
        await run_in_threadpool(
            marketplace_credentials.save_credential,
            store_slug,
            marketplace,
            api_key=payload.api_key.get_secret_value(),
            client_id=payload.client_id,
        )
        if marketplace == "wb":
            await run_in_threadpool(token_watch.refresh_token_info)
    except (ValueError, marketplace_credentials.CredentialStorageError) as error:
        return _failure(
            WebErrorCode.CREDENTIAL_STORAGE
            if isinstance(error, marketplace_credentials.CredentialStorageError)
            else WebErrorCode.CLIENT_ID_REQUIRED
            if str(error) == WebErrorCode.CLIENT_ID_REQUIRED
            else WebErrorCode.INVALID_CREDENTIAL,
            400,
        )

    actor = request.state.user
    await IntegrationRepository(request.app.state.async_resources.database).audit(
        actor.id,
        actor.full_name,
        "Изменён API-ключ",
        f"{store_slug}:{marketplace}",
    )
    return CredentialResult(
        status=await run_in_threadpool(
            marketplace_credentials.credential_snapshot, store_slug, CredentialMarketplace(marketplace)
        )
    )


@router.delete("/api/admin/integrations/{store_slug}/{marketplace}", response_model=CredentialResult)
async def remove_credential(request: Request, store_slug: str, marketplace: str):
    _require_active_account(request)
    _validate_target(store_slug, marketplace)
    try:
        await run_in_threadpool(marketplace_credentials.delete_credential, store_slug, marketplace)
        if marketplace == "wb":
            await run_in_threadpool(db.delete_wb_token_info, store_slug)
    except marketplace_credentials.CredentialStorageError:
        return _failure(WebErrorCode.CREDENTIAL_STORAGE, 400)

    actor = request.state.user
    await IntegrationRepository(request.app.state.async_resources.database).audit(
        actor.id,
        actor.full_name,
        "Удалён API-ключ",
        f"{store_slug}:{marketplace}",
    )
    return CredentialResult(
        status=await run_in_threadpool(
            marketplace_credentials.credential_snapshot, store_slug, CredentialMarketplace(marketplace)
        )
    )


@router.get("/admin/integrations/wb-yandex/cabinets", response_class=HTMLResponse)
async def wb_yandex_cabinets(request: Request):
    _require_active_account(request)
    return await run_in_threadpool(render_cabinet_settings, request.state.user)


@router.get("/admin/integrations/wb-yandex/week", response_class=HTMLResponse)
async def wb_yandex_week(request: Request):
    _require_active_account(request)
    return HTMLResponse(read_template("integrations/google-week-update.html"))


def _sync_history(job_name: str, limit: int) -> list[dict]:
    """Reconcile retained runs with the latest result and the actual worker lock."""
    runs = [dict(run) for run in db.list_sync_job_runs(job_name, limit)]
    states = db.list_sync_job_states()
    state = next((item for item in states if item["name"] == job_name), None)
    active = manual_sync.is_running(job_name)
    if not active:
        # A worker records its result before releasing the lock. Re-read after
        # the probe so a just-finished run is not mistaken for an interruption.
        states = db.list_sync_job_states()
        state = next((item for item in states if item["name"] == job_name), None)
    if (
        state
        and state.get("last_started_at")
        and not any(item.get("started_at") == state["last_started_at"] for item in runs)
    ):
        runs.insert(
            0,
            {
                "id": f"state:{job_name}",
                "name": job_name,
                "trigger": state.get("last_trigger"),
                "status": state.get("status"),
                "started_at": state.get("last_started_at"),
                "finished_at": state.get("last_finished_at"),
                "duration_ms": state.get("duration_ms"),
                "error": state.get("error"),
            },
        )
        runs = runs[:limit]
    for run in runs:
        if run.get("status") != "running":
            continue
        latest = state and run.get("started_at") == state.get("last_started_at")
        if latest and state.get("status") in {"success", "error"}:
            run.update(
                status=state["status"],
                finished_at=state.get("last_finished_at"),
                duration_ms=state.get("duration_ms"),
                error=state.get("error"),
            )
        elif latest and active:
            continue
        else:
            run.update(
                status="interrupted",
                error=run.get("error")
                or (
                    "Запуск больше не выполняется, но результат не был сохранён. "
                    "Возможно, приложение перезапустили или процесс остановился. "
                    "Точное время завершения неизвестно."
                ),
            )
    return runs


def _storefront_state(state: dict) -> None:
    from app.repositories import yandex_storefront
    from app.yandex.storefront_schedule import next_run_at

    current = datetime.now(UTC)
    pause = yandex_storefront.cooldown(current)
    if pause:
        state["cooldown_until"] = pause["retry_after"]
        state["cooldown_message"] = (
            f"Пауза после ограничения Яндекса до {format_dt(pause['retry_after'])}. "
            "Новые запросы не отправляются. Снятие ограничения пока не подтверждено."
        )
    config = sync_settings.configuration(state["name"])
    if not config["effective_enabled"] or not config["enabled_target_count"]:
        state.update(next_run_at=None, next_run_message="Автовыгрузка выключена")
        return
    try:
        planned = datetime.fromisoformat(state.get("next_run_at") or "")
        if planned.tzinfo is None or planned <= current:
            planned = None
    except ValueError:
        planned = None
    if planned is None:
        state.update(next_run_at=None, next_run_message="Сборщик не подтвердил следующий запуск")
    elif pause and planned < datetime.fromisoformat(pause["retry_after"]):
        state["next_run_at"] = next_run_at(
            datetime.fromisoformat(pause["retry_after"]) - timedelta(microseconds=1)
        ).isoformat()


def _sync_states(user=None) -> list[dict]:
    stored = {state["name"]: state for state in db.list_sync_job_states()}
    states = []
    for definition in _visible_jobs(user):
        if ozon_jobs.owns(definition.name):
            continue
        state = {"name": definition.name, **stored.get(definition.name, {})}
        state["running"] = manual_sync.is_running(definition.name)
        if state["running"]:
            state["status"] = "running"
        elif state.get("status") == "running":
            state.update(status="error", error="Выполнение прервано. Можно запустить выгрузку повторно.")
        if definition.name == "yandex_storefront_prices_sync":
            _storefront_state(state)
        states.append(state)
    return states


@router.get("/admin/integrations/ozon/cabinets", response_class=HTMLResponse)
async def ozon_cabinets(request: Request):
    require_preview(request.state.user)
    return HTMLResponse(read_template("economics/ozon/settings.html"))
