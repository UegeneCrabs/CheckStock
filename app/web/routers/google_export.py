from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import db
from app.access.authentication import is_active_user
from app.core.constants import JOBS, GoogleExportKind, Marketplace, WebErrorCode
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.google_export import (
    GoogleExportMarketplaceResult,
    GoogleExportRunReport,
    GoogleExportRunResult,
    GoogleExportSettings,
    GoogleExportTarget,
    GoogleExportUpdate,
    GoogleExportView,
)
from app.dto.integrations import WebFailure
from app.exports import project_sheet as project_sheet_export
from app.infrastructure.async_read_models import AsyncReadModels
from app.infrastructure.google_export_repository import GoogleExportRepository
from app.infrastructure.integration_repository import IntegrationRepository
from app.jobs.async_locks import hold as hold_async
from app.jobs.locks import SyncJobBusyError
from app.jobs.tracking import run_tracked
from app.repositories.project_sheet_export import MarketplaceExportTarget, ProjectSheetExportSettings
from app.web.templating import read_template

router = APIRouter()

MARKETPLACE_FORM_PREFIXES = {"WB": "wb", "OZON": "ozon", "YANDEX MARKET": "yandex"}
PROJECT_MARKETPLACES = ("YANDEX MARKET", "OZON", "WB")


def _require_active_account(request: Request) -> None:
    if not is_active_user(request.state.user):
        raise HTTPException(status_code=403, detail="Недостаточно прав")


def _value(form, name: str) -> str:
    return str(form.get(name) or "").strip()


@router.get("/admin/google-export", response_class=HTMLResponse)
async def google_export_page(request: Request):
    _require_active_account(request)
    return RedirectResponse("/admin/integrations?tab=google", status_code=303)


def _project_settings_from_form(form, existing: ProjectSheetExportSettings) -> ProjectSheetExportSettings:
    try:
        weekday = int(_value(form, "weekday") or 0)
    except ValueError as error:
        raise ValueError("Некорректный день недели") from error
    return replace(
        existing,
        enabled=_value(form, "enabled") == "1",
        schedule_kind=_value(form, "schedule_kind"),
        weekday=weekday,
        run_time=_value(form, "run_time"),
        updated_at=datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
        targets=tuple(
            MarketplaceExportTarget(
                marketplace=marketplace,
                spreadsheet_url=_value(form, f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_spreadsheet_url"),
                stock_sheet_name=_value(form, f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_sheet_name"),
                orders_sheet_name=_value(
                    form, f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_fbs_orders_sheet_name"
                ),
                orders_quantity_column=(
                    _value(form, f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_fbs_orders_quantity_column")
                    if f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_fbs_orders_quantity_column" in form
                    else existing.target(marketplace).orders_quantity_column
                )
                .strip()
                .upper(),
                fbo_sheet_name=(
                    _value(form, f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_fbo_sheet_name")
                    if f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_fbo_sheet_name" in form
                    else existing.target(marketplace).fbo_sheet_name
                ),
                fbo_to_customer_column=(
                    _value(form, f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_fbo_to_customer_column")
                    if f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_fbo_to_customer_column" in form
                    else existing.target(marketplace).fbo_to_customer_column
                )
                .strip()
                .upper(),
                fbo_from_customer_column=(
                    _value(form, f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_fbo_from_customer_column")
                    if f"{MARKETPLACE_FORM_PREFIXES[marketplace]}_fbo_from_customer_column" in form
                    else existing.target(marketplace).fbo_from_customer_column
                )
                .strip()
                .upper(),
            )
            for marketplace in PROJECT_MARKETPLACES
        ),
    )


def google_service_account_email() -> str:
    from app.ff_import import google_service_account

    return google_service_account.get_service_account_email()


@router.post("/admin/google-export/settings")
async def save_project_export_settings(request: Request):
    _require_active_account(request)
    form = await request.form()
    existing = await run_in_threadpool(project_sheet_export.get_settings)
    try:
        settings = _project_settings_from_form(form, existing)
        await run_in_threadpool(project_sheet_export.save_settings, settings)
    except ValueError as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
    except SyncJobBusyError:
        return JSONResponse(
            {"ok": False, "error": "Выгрузка уже выполняется. Сохраните настройки после её завершения"},
            status_code=409,
        )
    actor = request.state.user
    await run_in_threadpool(
        db.log_action,
        actor.id,
        actor.full_name,
        "Изменены общие настройки выгрузки",
        f"Все проекты: {settings.schedule_kind} {settings.run_time} МСК",
        datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds"),
    )
    return JSONResponse({"ok": True})


@router.post("/admin/google-export/run", response_model=GoogleExportRunResult)
async def run_project_export(request: Request):
    _require_active_account(request)
    form = await request.form()
    marketplace = _value(form, "marketplace") or None
    export_kind = _value(form, "export_kind") or None
    if (
        (marketplace is None) != (export_kind is None)
        or marketplace is not None
        and marketplace not in Marketplace
        or export_kind is not None
        and export_kind not in GoogleExportKind
    ):
        return JSONResponse(
            WebFailure(code=WebErrorCode.INVALID_TARGET).model_dump(mode="json"), status_code=400
        )
    try:
        report = await run_in_threadpool(
            run_tracked,
            JOBS.stock_sheet_export.name,
            "manual",
            lambda: project_sheet_export.run_export(
                marketplace=marketplace,
                export_kind=export_kind,
            ),
        )
    except SyncJobBusyError:
        return JSONResponse(WebFailure(code=WebErrorCode.JOB_BUSY).model_dump(mode="json"), status_code=409)
    except ValueError:
        return JSONResponse(
            WebFailure(code=WebErrorCode.INVALID_EXPORT_SETTINGS).model_dump(mode="json"), status_code=400
        )
    except Exception:
        return JSONResponse(WebFailure(code=WebErrorCode.JOB_FAILED).model_dump(mode="json"), status_code=502)
    return GoogleExportRunResult(
        report=GoogleExportRunReport(
            marketplaces=tuple(
                GoogleExportMarketplaceResult(
                    marketplace=item["marketplace"],
                    skipped=item.get("skipped", False),
                    updated_cells=item.get("updated_cells", 0),
                    warnings=tuple(item.get("warnings") or ()),
                )
                for item in report["marketplaces"]
            ),
            store_slugs=tuple(report["store_slugs"]),
            last_success_at=report.get("last_success_at"),
            warnings=tuple(report.get("warnings") or ()),
        )
    )


def _retired_store_export_response() -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "error": "Выгрузка по магазинам заменена общей выгрузкой всех проектов. "
            "Обновите страницу и сохраните ссылки на таблицы и листы отдельно для каждой площадки.",
        },
        status_code=410,
    )


@router.post("/admin/google-export/{store_slug}")
async def save_google_export_settings(request: Request, store_slug: str):
    _require_active_account(request)
    return _retired_store_export_response()


@router.post("/admin/google-export/{store_slug}/run")
async def run_google_export(request: Request, store_slug: str):
    _require_active_account(request)
    return _retired_store_export_response()


@router.get("/api/admin/google-export", response_model=GoogleExportView)
async def google_export_data(request: Request) -> GoogleExportView:
    _require_active_account(request)
    settings = await AsyncReadModels(request.app.state.async_resources.database).read(
        lambda: GoogleExportSettings.model_validate(project_sheet_export.get_settings())
    )
    email = await run_in_threadpool(google_service_account_email)
    return GoogleExportView(settings=settings, service_account_email=email)


@router.get("/ui/integrations/google-export", response_class=HTMLResponse)
async def google_export_template(request: Request):
    _require_active_account(request)
    return read_template("integrations/google-export.html")


@router.put("/api/admin/google-export", response_model=GoogleExportSettings)
async def save_google_export_data(request: Request, payload: GoogleExportUpdate):
    _require_active_account(request)
    database = request.app.state.async_resources.database
    repository = GoogleExportRepository(database)
    updated_at = datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds")
    targets = tuple(
        replace(
            MarketplaceExportTarget(**target.model_dump()),
            orders_quantity_column=target.orders_quantity_column.upper(),
            fbo_to_customer_column=target.fbo_to_customer_column.upper(),
            fbo_from_customer_column=target.fbo_from_customer_column.upper(),
        )
        for target in payload.targets
    )
    value = (
        payload.model_copy(
            update={"targets": tuple(GoogleExportTarget.model_validate(target) for target in targets)}
        )
        if targets
        else payload
    )
    candidate = replace(
        project_sheet_export.default_settings(),
        enabled=value.enabled,
        schedule_kind=value.schedule_kind,
        weekday=value.weekday,
        run_time=value.run_time,
        targets=targets,
        updated_at=updated_at,
    )
    try:
        project_sheet_export.validate_settings(candidate)
        async with hold_async(JOBS.stock_sheet_export.name, database):
            await repository.save(value, updated_at)
    except ValueError:
        return JSONResponse({"ok": False, "code": WebErrorCode.INVALID_EXPORT_SETTINGS}, status_code=400)
    except SyncJobBusyError:
        return JSONResponse({"ok": False, "code": WebErrorCode.JOB_BUSY}, status_code=409)
    actor = request.state.user
    await IntegrationRepository(database).audit(
        actor.id, actor.full_name, "google_export_settings", updated_at
    )
    return await repository.settings()
