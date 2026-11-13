from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from app.access.authentication import require_active_user
from app.config import settings
from app.core.constants import WebErrorCode
from app.core.export_journal import journal_day, oldest_day
from app.dto.integrations import ExportLogCalendar
from app.exports.export_journal import XLSX_MEDIA_TYPE, build_xlsx
from app.infrastructure.integration_repository import IntegrationRepository
from app.web.dependencies import AsyncResourcesDependency
from app.web.downloads import _download_headers

router = APIRouter(prefix="/api/admin/integrations/export-log")


@router.get("", response_model=ExportLogCalendar)
async def calendar(request: Request) -> ExportLogCalendar:
    require_active_user(request)
    today = journal_day()
    return ExportLogCalendar(today=today, oldest=oldest_day(today))


@router.get("/download")
async def download(
    request: Request, resources: AsyncResourcesDependency, day: date | None = None
) -> Response:
    require_active_user(request)
    today = journal_day()
    day = day or today
    if not oldest_day(today) <= day <= today:
        raise HTTPException(status_code=404, detail=WebErrorCode.NOT_FOUND)
    failures = await IntegrationRepository(resources.database).failed_runs_on(day)
    content = await run_in_threadpool(build_xlsx, settings.export_error_log_directory, day, failures)
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            **_download_headers(f"checkstock-export-{day.isoformat()}.xlsx"),
            "Cache-Control": "no-store",
        },
    )
