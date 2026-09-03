from __future__ import annotations

import html
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, SecretStr

from app import (
    auth,
    db,
    ftp_export,
    ftp_export_schedule,
    marketplace_credentials,
    sync_settings,
)
from app.domain import MOSCOW_TIMEZONE
from app.formatting import format_dt
from app.stores import STORES
from app.sync_catalog import job_definitions
from app.sync_tracking import run_tracked, set_next_run
from app.wb import funnel_orders as wb_funnel_orders
from app.wb import token_watch
from app.web.cabinet_settings import render_cabinet_settings
from app.web.templating import fill_template, render_page

router = APIRouter()

MARKETPLACE_LABELS = {
    "wb": "Wildberries",
    "ozon": "Ozon",
    "yandex": "Яндекс Маркет",
}


class CredentialUpdate(BaseModel):
    api_key: SecretStr = Field(min_length=1, max_length=16_384)
    client_id: str = Field(default="", max_length=1_024)


class SyncSettingUpdate(BaseModel):
    enabled: bool
    store_slug: str = Field(default="", max_length=100)
    marketplace: str = Field(default="", max_length=100)


WB_BUYOUT_JOB_NAME = "wb_funnel_weekly_metrics_sync"


def _require_superadmin(request: Request) -> None:
    if not auth.has_role(request.state.user, "superadmin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _validate_target(store_slug: str, marketplace: str) -> None:
    if store_slug not in STORES:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if marketplace not in MARKETPLACE_LABELS:
        raise HTTPException(status_code=404, detail="Маркетплейс не найден")


def _credential_card(store_slug: str, marketplace: str) -> str:
    label = MARKETPLACE_LABELS[marketplace]
    try:
        status = marketplace_credentials.credential_status(store_slug, marketplace)
        configured = bool(status.get("configured"))
        storage_error = ""
    except marketplace_credentials.CredentialStorageError as error:
        configured = False
        status = {}
        storage_error = str(error)
    badge_class = "is-connected" if configured else "is-empty"
    badge_text = "Подключён" if configured else "Ключ не задан"
    detail = ""
    if marketplace == "wb" and status.get("expires_at"):
        detail = f"Действует до {format_dt(str(status['expires_at']))}"
    elif marketplace == "ozon" and status.get("client_id_hint"):
        detail = f"Client ID: {status['client_id_hint']}"
    if storage_error:
        detail = storage_error

    client_field = ""
    if marketplace == "ozon":
        client_field = (
            '<label class="integration-field"><span>Client ID</span>'
            '<input type="text" name="client_id" maxlength="1024" autocomplete="off" '
            'placeholder="Оставьте пустым, чтобы сохранить текущий"></label>'
        )
    return (
        '<form class="integration-key-card" data-credential-form '
        f'data-store="{html.escape(store_slug)}" data-marketplace="{marketplace}">'
        '<div class="integration-key-head"><div>'
        f'<span class="integration-marketplace-code">{html.escape(label)}</span>'
        f'<strong>{html.escape(STORES[store_slug].name)}</strong></div>'
        f'<span class="integration-key-status {badge_class}" data-key-status>{badge_text}</span></div>'
        f'<p class="integration-key-detail">{html.escape(detail) if detail else "API-ключ не показывается после сохранения"}</p>'
        f"{client_field}"
        '<label class="integration-field"><span>Новый API-ключ</span><div class="integration-secret-wrap">'
        '<input type="password" name="api_key" maxlength="16384" autocomplete="new-password" '
        'placeholder="Вставьте ключ для добавления или замены" required>'
        '<button type="button" class="integration-reveal" data-reveal-key aria-label="Показать ключ">Показать</button>'
        '</div></label>'
        '<p class="integration-form-message" data-form-message aria-live="polite"></p>'
        '<div class="integration-key-actions">'
        '<button class="btn-primary" type="submit">Сохранить ключ</button>'
        '<button class="btn-secondary integration-delete" type="button" data-delete-key'
        f'{"" if configured else " disabled"}>Удалить ключ</button></div>'
        "</form>"
    )


def _store_panel(store_slug: str, *, active: bool) -> str:
    store = STORES[store_slug]
    cards = "".join(_credential_card(store_slug, marketplace) for marketplace in MARKETPLACE_LABELS)
    return (
        f'<section class="integration-store-panel" data-integration-store="{store_slug}"'
        f'{"" if active else " hidden"}>'
        '<div class="integration-store-heading">'
        f'<span class="store-dot" style="--store-color:{html.escape(store.color)}"></span>'
        f'<div><small>МАГАЗИН</small><h2>{html.escape(store.name)}</h2></div></div>'
        f'<div class="integration-key-grid">{cards}</div></section>'
    )


def _switch(
    definition,
    *,
    checked: bool,
    label: str,
    store_slug: str = "",
    marketplace: str = "",
) -> str:
    disabled = not definition.enabled
    attributes = (
        f'data-job="{html.escape(definition.name)}" '
        f'data-store="{html.escape(store_slug)}" '
        f'data-marketplace="{html.escape(marketplace)}"'
    )
    return (
        '<label class="integration-sync-toggle">'
        '<input type="checkbox" role="switch" data-sync-setting-toggle '
        f'{attributes}{" checked" if checked else ""}{" disabled" if disabled else ""}>'
        '<span class="integration-sync-toggle-track" aria-hidden="true"></span>'
        f'<span class="integration-sync-toggle-label">{html.escape(label)}</span>'
        '</label>'
    )


def _target_settings(definition, config: dict) -> str:
    if not config["targets"]:
        return ""
    if definition.scope == "stores":
        controls = "".join(
            '<div class="integration-sync-target">'
            + _switch(
                definition,
                checked=bool(target["configured_enabled"]),
                label=str(target["store_name"]),
                store_slug=str(target["store_slug"]),
            )
            + '</div>'
            for target in config["targets"]
        )
    else:
        by_store: dict[str, list[dict]] = {}
        for target in config["targets"]:
            by_store.setdefault(str(target["store_slug"]), []).append(target)
        marketplace_controls = "".join(
            _switch(
                definition,
                checked=bool(item["enabled"]),
                label=f"Все {item['marketplace_name']}",
                marketplace=str(item["marketplace"]),
            )
            for item in config["marketplace_settings"]
        )
        controls = (
            '<section class="integration-sync-marketplace-masters">'
            '<strong>Маркетплейсы целиком</strong>'
            f'<div>{marketplace_controls}</div></section>'
            + "".join(
            '<section class="integration-sync-store-targets">'
            f'<strong>{html.escape(STORES[store_slug].name)}</strong>'
            '<div class="integration-sync-marketplaces">'
            + "".join(
                _switch(
                    definition,
                    checked=bool(target["configured_enabled"]),
                    label=str(target["marketplace_name"]),
                    store_slug=store_slug,
                    marketplace=str(target["marketplace"]),
                )
                for target in targets
            )
            + '</div></section>'
            for store_slug, targets in by_store.items()
            )
        )
    return (
        f'<tr class="integration-sync-target-row" data-sync-targets-row="{html.escape(definition.name)}" hidden>'
        '<td colspan="6"><div class="integration-sync-target-shell">'
        '<div><strong>Где запускать автоматически</strong>'
        '<small>Настройки применяются со следующего запуска по расписанию.</small></div>'
        f'<div class="integration-sync-target-grid">{controls}</div>'
        '<p class="integration-sync-setting-message" data-sync-setting-message aria-live="polite"></p>'
        '</div></td></tr>'
    )


def _sync_row(definition, state: dict | None, config: dict) -> str:
    state = state or {}
    status = str(state.get("status") or "")
    status_labels = {
        "running": ("Выполняется", "is-running"),
        "success": ("Успешно", "is-success"),
        "error": ("Ошибка", "is-error"),
    }
    status_text, status_class = status_labels.get(status, ("Ещё не запускалась", "is-empty"))
    trigger = str(state.get("last_trigger") or "")
    trigger_text = {"manual": "Вручную", "scheduled": "По расписанию"}.get(trigger, "—")
    last_run = state.get("last_finished_at") or state.get("last_started_at")
    next_run = format_dt(state.get("next_run_at")) if state.get("next_run_at") else "Рассчитывается"
    target_button = ""
    if config["targets"]:
        target_button = (
            '<button class="btn-secondary integration-targets-button" type="button" '
            f'data-sync-targets-toggle="{html.escape(definition.name)}" aria-expanded="false">'
            f'Магазины · {config["enabled_target_count"]}/{config["target_count"]}</button>'
        )
    manual_button = ""
    if definition.manual_run:
        manual_button = (
            '<button class="btn-primary integration-run-button" type="button" '
            f'data-sync-run="{html.escape(definition.name)}" '
            f'data-sync-title="{html.escape(definition.title)}"'
            f'{"" if definition.enabled else " disabled"}>Запустить сейчас</button>'
        )
    row = (
        f'<tr data-sync-job="{html.escape(definition.name)}">'
        f'<td><strong>{html.escape(definition.title)}</strong><small>{html.escape(definition.description)}</small></td>'
        f'<td><span class="sync-status {status_class}">{status_text}</span></td>'
        f"<td>{html.escape(format_dt(last_run))}</td>"
        f"<td>{html.escape(trigger_text)}</td>"
        '<td><div class="integration-auto-setting">'
        + _switch(
            definition,
            checked=bool(config["configured_enabled"]),
            label=str(config["summary"]),
        )
        + f'<small>{html.escape(definition.schedule)}</small>'
        + f'<small>Следующая: {html.escape(next_run)}</small>'
        + '<small class="integration-sync-inline-message" data-sync-setting-inline-message></small>'
        + '</div></td>'
        '<td><div class="integration-sync-actions">'
        + target_button
        + manual_button
        + '<button class="btn-secondary integration-history-button" type="button" '
        f'data-sync-history="{html.escape(definition.name)}" '
        f'data-sync-title="{html.escape(definition.title)}">История</button></div></td>'
        "</tr>"
    )
    return row + _target_settings(definition, config)


@router.get("/admin/integrations", response_class=HTMLResponse)
async def integrations_page(request: Request):
    _require_superadmin(request)
    states = {
        state["name"]: state for state in await run_in_threadpool(db.list_sync_job_states)
    }
    definitions = job_definitions()
    configurations = {
        definition.name: await run_in_threadpool(sync_settings.configuration, definition.name)
        for definition in definitions
    }
    store_tabs = "".join(
        '<button type="button" class="integration-store-tab'
        f'{" is-active" if index == 0 else ""}" data-integration-store-tab="{slug}">'
        f"{html.escape(store.name)}</button>"
        for index, (slug, store) in enumerate(STORES.items())
    )
    content = fill_template(
        "integrations_content.html",
        store_tabs=store_tabs,
        cabinet_settings=await run_in_threadpool(render_cabinet_settings, request.state.user),
        store_panels="".join(
            _store_panel(slug, active=index == 0) for index, slug in enumerate(STORES)
        ),
        sync_rows="".join(
            _sync_row(
                definition,
                states.get(definition.name),
                configurations[definition.name],
            )
            for definition in definitions
        ),
    )
    return render_page(
        "CheckStock — API и выгрузки",
        "admin_integrations",
        content,
        request.state.user,
        "content--integrations",
    )


@router.get("/api/admin/integrations/sync-jobs/{job_name}/history")
async def sync_job_history(request: Request, job_name: str, limit: int = 50):
    _require_superadmin(request)
    definitions = {definition.name: definition for definition in job_definitions()}
    definition = definitions.get(job_name)
    if definition is None:
        return JSONResponse({"ok": False, "error": "Выгрузка не найдена"}, status_code=404)

    safe_limit = min(max(limit, 1), 200)
    runs = await run_in_threadpool(db.list_sync_job_runs, job_name, safe_limit)
    states = await run_in_threadpool(db.list_sync_job_states)
    state = next((item for item in states if item["name"] == job_name), None)
    if state and state.get("last_started_at") and not any(
        item.get("started_at") == state["last_started_at"] for item in runs
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
        runs = runs[:safe_limit]
    return {
        "ok": True,
        "job": {"name": definition.name, "title": definition.title},
        "retention_days": 30,
        "runs": runs,
    }


@router.post("/api/admin/integrations/sync-jobs/{job_name}/run")
async def run_sync_job(request: Request, job_name: str):
    _require_superadmin(request)
    definition = next(
        (item for item in job_definitions() if item.name == job_name and item.manual_run),
        None,
    )
    if definition is None:
        return JSONResponse(
            {"ok": False, "error": "Ручной запуск этой выгрузки недоступен"},
            status_code=404,
        )
    if not definition.enabled:
        return JSONResponse(
            {"ok": False, "error": "Выгрузка системно отключена"},
            status_code=409,
        )
    platform = ftp_export.platform_for_job(job_name)
    if platform is not None:
        if ftp_export.is_running(platform):
            return JSONResponse(
                {"ok": False, "error": f"Выгрузка FTP {platform.upper()} уже выполняется"},
                status_code=409,
            )
        try:
            result = await run_in_threadpool(
                run_tracked,
                job_name,
                "manual",
                lambda: ftp_export.run_platform(platform),
            )
        except ftp_export.FTPExportBusyError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=409)
        except Exception as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=502)
        await run_in_threadpool(
            set_next_run,
            job_name,
            ftp_export_schedule.next_delay_seconds(job_name),
        )
        message = f"{definition.title}: выгрузка завершена"
    elif job_name == WB_BUYOUT_JOB_NAME:
        config = await run_in_threadpool(sync_settings.configuration, job_name)
        store_slugs = tuple(
            str(target["store_slug"])
            for target in config["targets"]
            if target["enabled"]
        )
        if not store_slugs:
            return JSONResponse(
                {"ok": False, "error": "Для обновления не выбран ни один магазин"},
                status_code=409,
            )
        try:
            result = await run_in_threadpool(
                run_tracked,
                job_name,
                "manual",
                lambda: wb_funnel_orders.sync_weekly_metrics_all(store_slugs),
            )
        except Exception as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=502)
        succeeded = sum(
            1 for item in result.values() if item.get("status") == "success"
        )
        skipped = sum(
            1 for item in result.values() if item.get("status") == "skipped"
        )
        failed = len(result) - succeeded - skipped
        message = f"Процент выкупа обновлён: {succeeded} из {len(result)} магазинов"
        if skipped:
            message += f"; пропущено: {skipped}"
        if failed:
            message += f"; ошибок: {failed}"
    else:
        return JSONResponse(
            {"ok": False, "error": "Ручной запуск этой выгрузки недоступен"},
            status_code=404,
        )
    actor = request.state.user
    await run_in_threadpool(
        db.log_action,
        actor.id,
        actor.full_name,
        "Запущена выгрузка вручную",
        definition.title,
        datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
    )
    return {"ok": True, "message": message, "result": result}


@router.put("/api/admin/integrations/sync-jobs/{job_name}/settings")
async def update_sync_job_setting(
    request: Request,
    job_name: str,
    payload: SyncSettingUpdate,
):
    _require_superadmin(request)
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


@router.put("/api/admin/integrations/{store_slug}/{marketplace}")
async def update_credential(
    request: Request,
    store_slug: str,
    marketplace: str,
    payload: CredentialUpdate,
):
    _require_superadmin(request)
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
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)

    actor = request.state.user
    await run_in_threadpool(
        db.log_action,
        actor.id,
        actor.full_name,
        "Изменён API-ключ",
        f"{STORES[store_slug].name}: {MARKETPLACE_LABELS[marketplace]}",
        datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
    )
    return {"ok": True, "status": marketplace_credentials.credential_status(store_slug, marketplace)}


@router.delete("/api/admin/integrations/{store_slug}/{marketplace}")
async def remove_credential(request: Request, store_slug: str, marketplace: str):
    _require_superadmin(request)
    _validate_target(store_slug, marketplace)
    try:
        await run_in_threadpool(marketplace_credentials.delete_credential, store_slug, marketplace)
        if marketplace == "wb":
            await run_in_threadpool(db.delete_wb_token_info, store_slug)
    except marketplace_credentials.CredentialStorageError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)

    actor = request.state.user
    await run_in_threadpool(
        db.log_action,
        actor.id,
        actor.full_name,
        "Удалён API-ключ",
        f"{STORES[store_slug].name}: {MARKETPLACE_LABELS[marketplace]}",
        datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
    )
    return {"ok": True, "status": marketplace_credentials.credential_status(store_slug, marketplace)}
