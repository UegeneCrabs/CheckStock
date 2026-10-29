from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response

from app.access.economics_preview import require_preview
from app.access.resources import accessible_stores
from app.container import ozon_cabinet_service, ozon_reports_service
from app.core.constants import OZON_REPORTS, TIME, Marketplace
from app.dto.ozon_cabinet import OzonCabinetGoals, OzonCabinetUpdate
from app.dto.ozon_reports import (
    OzonFilterResult,
    OzonGoalSettings,
    OzonGoalSettingsChange,
    OzonProfitQuery,
    OzonProfitReport,
    OzonTargetChange,
    OzonTargetConflict,
    OzonTargetExport,
    OzonTargetPreview,
    OzonTargetRecord,
    OzonTargetReport,
)
from app.infrastructure.ozon_cabinet_repository import OzonCabinetConflict, OzonCabinetRepository
from app.infrastructure.ozon_report_export import build_workbook
from app.web.client_page import client_page
from app.web.dependencies import AsyncResourcesDependency
from app.web.downloads import _download_headers

router = APIRouter()


def stores_for(request: Request, selected: tuple[str, ...] = ()) -> tuple[str, ...]:
    require_preview(request.state.user)
    allowed = accessible_stores(request.state.user, Marketplace.OZON)
    requested = tuple(value.strip().lower() for value in selected if value.strip() and value != "all")
    if any(store not in allowed for store in requested):
        raise HTTPException(403, "forbidden")
    return requested or allowed


@router.get(OZON_REPORTS.pages + "/unit-profit", response_class=HTMLResponse)
async def profit_page(request: Request) -> str:
    stores_for(request)
    return client_page("economics/ozon/unit-profit.html")


@router.get(OZON_REPORTS.pages + "/target-price", response_class=HTMLResponse)
async def target_page(request: Request) -> str:
    stores_for(request)
    return client_page("economics/ozon/target-price.html")


@router.get(OZON_REPORTS.api + "/unit-profit")
async def profit_data(
    request: Request, query: Annotated[OzonProfitQuery, Query()], resources: AsyncResourcesDependency
) -> OzonProfitReport:
    return await ozon_reports_service(resources.database).profit(query, stores_for(request, query.store))


@router.get(OZON_REPORTS.api + "/unit-profit/filters")
async def profit_filters(
    request: Request, query: Annotated[OzonProfitQuery, Query()], resources: AsyncResourcesDependency
) -> OzonFilterResult:
    return await ozon_reports_service(resources.database).filter_options(stores_for(request, query.store))


@router.get(OZON_REPORTS.pages + "/unit-profit.xlsx")
async def profit_export(
    request: Request, query: Annotated[OzonProfitQuery, Query()], resources: AsyncResourcesDependency
) -> Response:
    report = await ozon_reports_service(resources.database).profit(
        query, stores_for(request, query.store), export=True
    )
    body = await run_in_threadpool(build_workbook, report)
    return Response(
        body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(f"ozon_unit_profit_{query.date_from}_{query.date_to}.xlsx"),
    )


@router.get(OZON_REPORTS.api + "/target-price")
async def target_data(
    request: Request, resources: AsyncResourcesDependency, store: str = "", article: str = ""
) -> OzonTargetReport:
    return await ozon_reports_service(resources.database).targets(stores_for(request, (store,)), article)


@router.post(OZON_REPORTS.api + "/target-price/{store}/preview")
async def target_preview(
    request: Request, store: str, payload: OzonTargetPreview, resources: AsyncResourcesDependency
) -> OzonTargetReport:
    report = await ozon_reports_service(resources.database).targets(
        stores_for(request, (store,)), payload.article, payload
    )
    if not report.rows:
        raise HTTPException(404, "not_found")
    return report


@router.put(OZON_REPORTS.api + "/target-price/{store}/targets")
async def target_save(
    request: Request, store: str, payload: OzonTargetChange, resources: AsyncResourcesDependency
) -> OzonTargetRecord:
    stores_for(request, (store,))
    try:
        return await ozon_reports_service(resources.database).save_target(
            store, payload, request.state.user.id, request.state.user.full_name
        )
    except OzonTargetConflict:
        raise HTTPException(409, "settings_conflict") from None
    except LookupError:
        raise HTTPException(404, "not_found") from None


@router.delete(OZON_REPORTS.api + "/target-price/{store}/targets")
async def target_reset(
    request: Request,
    store: str,
    article: Annotated[str, Query(min_length=1)],
    resources: AsyncResourcesDependency,
    revision: str = "",
) -> OzonTargetRecord:
    return await target_save(request, store, OzonTargetChange(article=article, revision=revision), resources)


@router.get(OZON_REPORTS.api + "/target-price/settings/{store}")
async def goal_settings(
    request: Request, store: str, resources: AsyncResourcesDependency
) -> OzonGoalSettings:
    stores_for(request, (store,))
    record = (await OzonCabinetRepository(resources.database).settings((store,)))[0]
    return OzonGoalSettings(revision=record.revision, values=record.values.goals)


@router.put(OZON_REPORTS.api + "/target-price/settings/{store}")
async def goal_save(
    request: Request, store: str, payload: OzonGoalSettingsChange, resources: AsyncResourcesDependency
) -> OzonGoalSettings:
    stores_for(request, (store,))
    try:
        record = await ozon_cabinet_service(resources).save(
            store,
            OzonCabinetUpdate(
                revision=payload.revision,
                goals=OzonCabinetGoals.model_validate(payload.model_dump(exclude={"revision"})),
            ),
            request.state.user.full_name,
        )
    except OzonCabinetConflict:
        raise HTTPException(409, "settings_conflict") from None
    return OzonGoalSettings(revision=record.revision, values=record.values.goals)


@router.post(OZON_REPORTS.api + "/target-price.xlsx")
async def target_export(
    request: Request, payload: OzonTargetExport, resources: AsyncResourcesDependency
) -> Response:
    selected = tuple(dict.fromkeys(row.store_slug for row in payload.rows))
    stores = stores_for(request, selected)
    keys = {(row.store_slug, row.article) for row in payload.rows}
    report = await ozon_reports_service(resources.database).targets(stores)
    report = report.model_copy(
        update={"rows": tuple(row for row in report.rows if (row.store_slug, row.article) in keys)}
    )
    body = await run_in_threadpool(build_workbook, report)
    return Response(
        body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(f"ozon_target_price_{datetime.now(TIME.moscow_timezone).date()}.xlsx"),
    )
