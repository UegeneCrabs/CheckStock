from datetime import datetime

from app.core.constants import GOOGLE_STOCK_TEST, GOOGLE_WEEK, TIME
from app.dto.google_stock_report import StockTable, StockTestOrderPeriod
from app.dto.google_week import GoogleSheet
from app.exports import stock_sheet as legacy


def export_timestamp(
    sheet: GoogleSheet,
    now: datetime,
    headers: tuple[str, ...] | None = None,
    *,
    order_period: StockTestOrderPeriod | None = None,
) -> list[dict[str, object]]:
    serial = (
        now.astimezone(TIME.moscow_timezone).replace(tzinfo=None)
        - datetime.fromisoformat(GOOGLE_WEEK.sheet_epoch)
    ).total_seconds() / TIME.seconds_per_day
    cells: list[dict[str, object]] = [
        {"userEnteredValue": {"stringValue": legacy.EXPORT_TIMESTAMP_LABEL}},
        {"userEnteredValue": {"numberValue": serial}},
    ]
    if order_period is not None:
        cells.extend(
            (
                {"userEnteredValue": {"stringValue": GOOGLE_STOCK_TEST.period_label}},
                {
                    "userEnteredValue": {
                        "stringValue": (
                            f"{order_period.start:%d.%m.%Y %H:%M:%S} – {order_period.end:%d.%m.%Y %H:%M:%S}"
                        )
                    }
                },
            )
        )
    if headers is not None:
        total = headers.index(GOOGLE_STOCK_TEST.returns_header if order_period else "ТОТАЛ")
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


def stock_report_requests(
    sheet: GoogleSheet, table: StockTable, now: datetime, *, order_period: StockTestOrderPeriod | None = None
) -> list[dict[str, object]]:
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
    requests.extend(export_timestamp(sheet, now, table.headers, order_period=order_period))
    return requests
