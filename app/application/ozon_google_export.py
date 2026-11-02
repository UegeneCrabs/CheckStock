import asyncio
from datetime import UTC, datetime, time, timedelta

from pydantic import Field, JsonValue

from app.application.ports import OzonTransitSnapshotStore
from app.core.constants import (
    GOOGLE_WEEK,
    OZON_ANALYTICS,
    TIME,
    GoogleExportKind,
    Marketplace,
    OrderScheme,
    WeekIssue,
)
from app.core.stores import STORES
from app.dto.common import DtoModel
from app.dto.google_week import (
    GoogleSheet,
    GridCell,
    SnapshotTime,
    WeekError,
    WeekProblem,
    WeekProfile,
    WeekReport,
    document_id,
)
from app.exports import project_sheet
from app.exports import stock_sheet as legacy
from app.exports.project_scope import stores_for_marketplace
from app.infrastructure.async_http import store_context
from app.infrastructure.async_read_models import AsyncReadModels
from app.infrastructure.async_resources import AsyncResources
from app.integrations import google_week_plans as plans
from app.integrations.google_week_client import GoogleWeekClient
from app.ozon import tokens
from app.ozon.async_api import AsyncSellerApi


class StockTable(DtoModel):
    headers: tuple[str, ...]
    rows: tuple[tuple[JsonValue, ...], ...]
    warnings: tuple[str, ...]


class AccountQuantities(DtoModel):
    store_slug: str
    to_customer: dict[str, int] = Field(default_factory=dict)
    from_customer: dict[str, int] = Field(default_factory=dict)
    error: WeekProblem | None = None
    observed_at: datetime | None = None


def stock_table() -> StockTable:
    snapshots = project_sheet._stock_snapshot(Marketplace.OZON.value)
    metrics = sorted(
        {
            metric
            for snapshot in snapshots
            for metric in snapshot.values_by_metric
            if metric.startswith(legacy.FF_STOCK_METRIC_PREFIX)
        },
        key=str.casefold,
    )
    headers = (
        *project_sheet.EXPORT_HEADERS,
        *(
            legacy.FF_STOCK_HEADER_PREFIX + metric.removeprefix(legacy.FF_STOCK_METRIC_PREFIX)
            for metric in metrics
        ),
    )
    rows = []
    for snapshot in snapshots:
        catalog = [item for item in snapshot.catalog if str(item.get("article") or "").strip()]
        values = legacy._stock_export_rows(catalog, snapshot.values_by_metric, metrics)
        project = STORES[snapshot.store_slug].name
        for item, values_row in zip(catalog, values, strict=True):
            barcode = str(item.get("barcode") or "").strip().removeprefix("'").strip()
            rows.append((f"{project} {barcode}", project, *values_row))
    return StockTable.model_validate(
        {
            "headers": headers,
            "rows": rows,
            "warnings": tuple(warning for snapshot in snapshots for warning in snapshot.warnings),
        }
    )


def matrix(sheet: GoogleSheet) -> list[list[object]]:
    grid = plans.cells(sheet)
    last_row = max(
        (
            row
            for (row, _), cell in grid.items()
            if cell.userEnteredValue.model_dump(exclude_none=True) or plans.display(cell)
        ),
        default=-1,
    )
    columns = max((col for (_, col) in grid), default=-1) + 1
    return [
        [plans.display(grid.get((row, col), GridCell())) for col in range(columns)]
        for row in range(last_row + 1)
    ]


class OzonGoogleExport:
    def __init__(self, resources: AsyncResources, transit: OzonTransitSnapshotStore):
        self.resources = resources
        self.google = GoogleWeekClient(resources.http)
        self.transit = transit

    async def _account(self, store: str, kind: GoogleExportKind, now: datetime) -> AccountQuantities:
        context = store_context.set(store)
        try:
            if kind == GoogleExportKind.FBO_TRANSIT:
                snapshot = await self.transit.transit_snapshot(store)
                if snapshot is None:
                    raise WeekError(WeekIssue.SOURCE_MISSING, detail=store)
                if (
                    snapshot.store_slug != store
                    or not snapshot.includes_to_customer
                    or snapshot.observed_at.tzinfo is None
                    or len({item.article for item in snapshot.products}) != len(snapshot.products)
                    or any(
                        item.to_customer is None
                        or item.from_customer is None
                        or item.to_customer < 0
                        or item.from_customer < 0
                        for item in snapshot.products
                    )
                ):
                    raise WeekError(WeekIssue.SOURCE_INCOMPLETE, detail=store)
                age = (now - snapshot.observed_at).total_seconds()
                if not 0 <= age <= OZON_ANALYTICS.transit_max_age_seconds:
                    raise WeekError(WeekIssue.SOURCE_STALE, detail=store)
                return AccountQuantities(
                    store_slug=store,
                    to_customer={item.article: item.to_customer or 0 for item in snapshot.products},
                    from_customer={item.article: item.from_customer or 0 for item in snapshot.products},
                    observed_at=snapshot.observed_at,
                )
            credentials = tokens.get_order_credentials(store)
            end = now.astimezone(TIME.moscow_timezone).date()
            start = end - timedelta(days=GOOGLE_WEEK.export_period_days)
            since = datetime.combine(start, time.min, TIME.moscow_timezone).astimezone(UTC).isoformat()
            until = (
                datetime.combine(end, time.min, TIME.moscow_timezone).astimezone(UTC)
                - timedelta(microseconds=1)
            ).isoformat()
            postings = await AsyncSellerApi(self.resources.http, credentials).postings(
                OrderScheme.FBS, since, until
            )
            quantities: dict[str, int] = {}
            identities: set[tuple[str, str]] = set()
            for posting in postings:
                created = posting.created_at or posting.in_process_at
                if created is None or not start <= created.astimezone(TIME.moscow_timezone).date() < end:
                    raise WeekError(WeekIssue.SOURCE)
                if not posting.products:
                    raise WeekError(WeekIssue.SOURCE)
                for product in posting.products:
                    article = product.offer_id or product.product_id
                    identity = posting.posting_number, product.sku or product.product_id or product.offer_id
                    if not article or not identity[1] or identity in identities:
                        raise WeekError(WeekIssue.SOURCE)
                    identities.add(identity)
                    if posting.status in GOOGLE_WEEK.fbs_export_statuses:
                        quantities[article] = quantities.get(article, 0) + product.quantity
            return AccountQuantities(store_slug=store, to_customer=quantities)
        except asyncio.CancelledError:
            raise
        except WeekError as error:
            return AccountQuantities(store_slug=store, error=error.problem)
        except Exception as error:
            status = getattr(error, "status", None)
            return AccountQuantities(
                store_slug=store,
                error=WeekProblem(
                    code=WeekIssue.SOURCE, detail=store, status=status if isinstance(status, int) else None
                ),
            )
        finally:
            store_context.reset(context)

    async def _quantities(self, kind: GoogleExportKind, now: datetime) -> tuple[AccountQuantities, ...]:
        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(self._account(store, kind, now))
                for store in stores_for_marketplace(Marketplace.OZON.value)
            ]
        accounts = tuple(task.result() for task in tasks)
        for account in accounts:
            if account.error:
                raise WeekError(
                    account.error.code
                    if kind == GoogleExportKind.FBO_TRANSIT
                    else WeekIssue.SOURCE_INCOMPLETE,
                    detail=account.store_slug,
                    status=account.error.status,
                )
        return accounts

    def _timestamp(
        self, sheet: GoogleSheet, now: datetime, headers: tuple[str, ...] | None = None
    ) -> list[dict[str, object]]:
        serial = (
            now.astimezone(TIME.moscow_timezone).replace(tzinfo=None)
            - datetime.fromisoformat(GOOGLE_WEEK.sheet_epoch)
        ).total_seconds() / TIME.seconds_per_day
        cells: list[dict[str, object]] = [
            {"userEnteredValue": {"stringValue": legacy.EXPORT_TIMESTAMP_LABEL}},
            {"userEnteredValue": {"numberValue": serial}},
        ]
        if headers is not None:
            total = headers.index("ТОТАЛ")
            cells.extend({} for _ in range(total - len(cells)))
            cells.extend(
                {
                    "userEnteredValue": {
                        "formulaValue": f"=SUM({legacy._column_letter(column)}3:{legacy._column_letter(column)})"
                    }
                }
                for column in range(total, len(headers))
            )
        return [
            {
                "updateCells": {
                    "start": {"sheetId": sheet.properties.sheetId, "rowIndex": 0, "columnIndex": 0},
                    "rows": [{"values": cells}],
                    "fields": "userEnteredValue",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet.properties.sheetId,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 1,
                        "endColumnIndex": 2,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {"type": "DATE_TIME", "pattern": legacy.EXPORT_TIMESTAMP_FORMAT}
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
        ]

    async def run(self, profile: WeekProfile, kind: GoogleExportKind, now: datetime) -> WeekReport:
        settings = profile.settings
        destination = settings.export_target
        title = {
            GoogleExportKind.STOCKS: destination.stock_sheet_name,
            GoogleExportKind.FBS_ORDERS: destination.orders_sheet_name,
            GoogleExportKind.FBO_TRANSIT: destination.fbo_sheet_name,
        }[kind]
        if not title:
            raise WeekError(WeekIssue.INVALID_SETTINGS)
        doc_id = document_id(settings.spreadsheet_url)
        table = (
            await AsyncReadModels(self.resources.database).read(stock_table)
            if kind == GoogleExportKind.STOCKS
            else None
        )
        accounts = await self._quantities(kind, now) if table is None else ()
        document = await self.google.read(doc_id, [title])
        sheet = next((sheet for sheet in document.sheets if sheet.properties.title == title), None)
        if sheet is None:
            raise WeekError(WeekIssue.SHEET_MISSING, detail=title)
        report = WeekReport(
            marketplace=profile.marketplace,
            spreadsheet_url=settings.spreadsheet_url,
            sheet_name=title,
            sheet_id=sheet.properties.sheetId,
            processed_at=now,
            snapshot_times=[
                SnapshotTime(
                    store_slug=account.store_slug,
                    day=account.observed_at.astimezone(TIME.moscow_timezone).date(),
                    captured_at=account.observed_at,
                )
                for account in accounts
                if account.observed_at is not None
            ],
        )
        if table is not None:
            columns = max(
                sheet.properties.gridProperties.columnCount,
                len(table.headers),
                GOOGLE_WEEK.export_clear_columns,
            )
            rows = max(sheet.properties.gridProperties.rowCount, len(table.rows) + 2)
            requests: list[dict[str, object]] = [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet.properties.sheetId,
                            "gridProperties": {"columnCount": columns, "rowCount": rows},
                        },
                        "fields": "gridProperties.columnCount,gridProperties.rowCount",
                    }
                }
            ]
            requests.extend(
                {"unmergeCells": {"range": region.model_dump(exclude_none=True)}} for region in sheet.merges
            )
            requests.append(
                {
                    "updateCells": {
                        "range": {
                            "sheetId": sheet.properties.sheetId,
                            "startRowIndex": 0,
                            "endRowIndex": rows,
                            "startColumnIndex": 0,
                            "endColumnIndex": columns,
                        },
                        "fields": "userEnteredValue",
                    }
                }
            )
            values = (table.headers, *table.rows)
            for offset in range(0, len(values), GOOGLE_WEEK.export_batch_rows):
                batch = values[offset : offset + GOOGLE_WEEK.export_batch_rows]
                requests.append(
                    {
                        "updateCells": {
                            "start": {
                                "sheetId": sheet.properties.sheetId,
                                "rowIndex": offset + 1,
                                "columnIndex": 0,
                            },
                            "rows": [
                                {
                                    "values": [
                                        {
                                            "userEnteredValue": {"numberValue": value}
                                            if isinstance(value, (int, float))
                                            else {"stringValue": str(value)}
                                        }
                                        for value in row
                                    ]
                                }
                                for row in batch
                            ],
                            "fields": "userEnteredValue",
                        }
                    }
                )
            requests.extend(self._timestamp(sheet, now, table.headers))
            await self.google.batch_update(doc_id, requests)
            report.written_cells = len(values) * len(table.headers) + len(table.headers)
            report.warnings = list(table.warnings)
            return report
        grid = plans.cells(sheet)
        a1 = grid.get((0, 0), GridCell())
        b1 = grid.get((0, 1), GridCell())
        blank = (
            not plans.display(a1)
            and not plans.display(b1)
            and a1.userEnteredValue.formulaValue is None
            and b1.userEnteredValue.formulaValue is None
        )
        timestamp = (
            plans.display(a1) == legacy.EXPORT_TIMESTAMP_LABEL
            and b1.userEnteredValue.formulaValue is None
            and (
                not plans.display(b1)
                or b1.effectiveValue.numberValue is not None
                or b1.userEnteredValue.numberValue is not None
            )
        )
        if not (blank or timestamp) or any(
            plans.contains(region, 0, column, sheet) for region in sheet.merges for column in (0, 1)
        ):
            raise WeekError(WeekIssue.TARGET, cell="A1:B1")
        targets = (
            [(destination.orders_quantity_column, False)]
            if kind == GoogleExportKind.FBS_ORDERS
            else [(destination.fbo_to_customer_column, False), (destination.fbo_from_customer_column, True)]
        )
        prepared = []
        for column, inbound in targets:
            totals = {
                account.store_slug: account.from_customer if inbound else account.to_customer
                for account in accounts
            }
            try:
                prepared.append(
                    project_sheet._plan_quantities(
                        None,
                        doc_id,
                        title,
                        column,
                        totals,
                        sheet.model_dump(exclude_none=True),
                        label=kind.value,
                        preloaded_rows=matrix(sheet),
                    )
                )
            except project_sheet.StockSheetExportError as error:
                raise WeekError(WeekIssue.EXPORT, detail=str(error)) from error
        await self.google.batch_update(
            doc_id, [request for plan in prepared for request in plan.prepare_requests]
        )
        clears = [plan.clear_range for plan in prepared if plan.clear_range]
        if clears:
            await self.google.request("POST", f"{doc_id}/values:batchClear", body={"ranges": clears})
        updates = [item for plan in prepared for item in plan.updates]
        if updates:
            await self.google.request(
                "POST", f"{doc_id}/values:batchUpdate", body={"valueInputOption": "RAW", "data": updates}
            )
        await self.google.batch_update(doc_id, self._timestamp(sheet, now))
        report.written_cells = sum(plan.row_count for plan in prepared)
        report.warnings = list(dict.fromkeys(warning for plan in prepared for warning in plan.warnings))
        return report
