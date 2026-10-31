import asyncio
from datetime import datetime

from app.core.constants import JOBS, GoogleExportKind, Marketplace, WeekIssue
from app.dto.google_export import GoogleExportMarketplaceResult, GoogleExportRunReport
from app.dto.google_week import WeekError, WeekProfile, WeekReport, document_id
from app.jobs import locks
from app.repositories import core


def schedule_managed(marketplace: Marketplace = Marketplace.WB) -> bool:
    with core.get_connection() as connection:
        row = connection.execute(
            "SELECT payload FROM google_week_marketplaces WHERE marketplace=?", (marketplace.value,)
        ).fetchone()
    if row is None:
        return False
    payload = row["payload"]
    if not isinstance(payload, (str, bytes)):
        raise WeekError(WeekIssue.INVALID_SETTINGS)
    return WeekProfile.model_validate_json(payload).settings.schedule_managed


def export(
    kind: GoogleExportKind, url: str, now: datetime, marketplace: Marketplace = Marketplace.WB
) -> WeekReport:
    from app.exports import project_sheet

    with locks.hold(JOBS.stock_sheet_export.name):
        if marketplace == Marketplace.OZON:
            raise WeekError(WeekIssue.INVALID_SETTINGS)
        target = project_sheet.get_settings().target(marketplace.value)
        if document_id(target.spreadsheet_url) != document_id(url):
            raise WeekError(WeekIssue.INVALID_SETTINGS)
        sheet = {
            GoogleExportKind.STOCKS: target.stock_sheet_name,
            GoogleExportKind.FBS_ORDERS: target.orders_sheet_name,
            GoogleExportKind.FBO_TRANSIT: target.fbo_sheet_name,
        }[kind]
        if not sheet.strip():
            raise WeekError(WeekIssue.INVALID_SETTINGS)
        try:
            report = project_sheet.run_export(now, marketplace=marketplace.value, export_kind=kind.value)
        except project_sheet.StockSheetExportError as error:
            raise WeekError(WeekIssue.EXPORT, detail=str(error)) from error
        result = GoogleExportRunReport(
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
    return WeekReport(
        marketplace=marketplace,
        spreadsheet_url=url,
        sheet_name=sheet,
        written_cells=sum(item.updated_cells for item in result.marketplaces),
        warnings=list(
            dict.fromkeys(
                (*result.warnings, *(warning for item in result.marketplaces for warning in item.warnings))
            )
        ),
        processed_at=now,
    )


async def run(
    kind: GoogleExportKind, url: str, now: datetime, marketplace: Marketplace = Marketplace.WB
) -> WeekReport:
    task = asyncio.create_task(asyncio.to_thread(export, kind, url, now, marketplace))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise cancelled
