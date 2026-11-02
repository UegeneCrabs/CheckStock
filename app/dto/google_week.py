import re
from datetime import date, datetime
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from app.core.constants import (
    GOOGLE_MARKETPLACE_DEFAULTS,
    GOOGLE_WEEK,
    GoogleExportKind,
    Marketplace,
    ScheduleKind,
    WeekAction,
    WeekIssue,
)


class WeekModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class WeekProblem(WeekModel):
    code: WeekIssue
    cell: str = ""
    detail: str = ""
    status: int | None = None


class WeekError(Exception):
    def __init__(self, code: WeekIssue, *, cell: str = "", detail: str = "", status: int | None = None):
        self.problem = WeekProblem(code=code, cell=cell, detail=detail, status=status)
        super().__init__(code.value)


def document_id(url: str) -> str:
    parsed = urlsplit(url)
    match = re.fullmatch(r"/spreadsheets/d/([a-zA-Z0-9_-]+)(?:/edit)?/?", parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "docs.google.com" or not match:
        raise WeekError(WeekIssue.INVALID_SETTINGS)
    return match[1]


class ExportDestination(WeekModel):
    model_config = ConfigDict(extra="forbid")
    stock_sheet_name: str = ""
    orders_sheet_name: str = ""
    orders_quantity_column: str = "C"
    fbo_sheet_name: str = ""
    fbo_to_customer_column: str = "J"
    fbo_from_customer_column: str = "L"

    @field_validator("stock_sheet_name", "orders_sheet_name", "fbo_sheet_name")
    @classmethod
    def title(cls, value: str) -> str:
        value = value.strip()
        if len(value) > GOOGLE_WEEK.max_sheet_name or any(
            ord(char) < 32 or char in "[]:*?/\\" for char in value
        ):
            raise ValueError(WeekIssue.INVALID_SETTINGS)
        return value

    @field_validator("orders_quantity_column", "fbo_to_customer_column", "fbo_from_customer_column")
    @classmethod
    def column(cls, value: str) -> str:
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{1,3}", value):
            raise ValueError(WeekIssue.INVALID_SETTINGS)
        return value

    @model_validator(mode="after")
    def destinations(self) -> Self:
        if self.fbo_to_customer_column == self.fbo_from_customer_column:
            raise ValueError(WeekIssue.INVALID_SETTINGS)
        if self.stock_sheet_name and self.stock_sheet_name.casefold() in {
            self.orders_sheet_name.casefold(),
            self.fbo_sheet_name.casefold(),
        }:
            raise ValueError(WeekIssue.INVALID_SETTINGS)
        if (
            self.orders_sheet_name
            and self.orders_sheet_name.casefold() == self.fbo_sheet_name.casefold()
            and self.orders_quantity_column in {self.fbo_to_customer_column, self.fbo_from_customer_column}
        ):
            raise ValueError(WeekIssue.INVALID_SETTINGS)
        return self


class WeekSettings(WeekModel):
    model_config = ConfigDict(extra="forbid")
    spreadsheet_url: str = Field(default="", max_length=GOOGLE_WEEK.max_url_length)
    sheet_name: str = GOOGLE_WEEK.default_sheet
    sales_sheet_name: str = ""
    stock_sheet_name: str = GOOGLE_WEEK.default_stock_sheets[0]
    cells: tuple[str, ...] = GOOGLE_WEEK.default_cells
    weekday: int = Field(default=GOOGLE_WEEK.default_weekday, ge=0, lt=GOOGLE_WEEK.days_per_week)
    run_time: str = Field(default=GOOGLE_WEEK.default_time, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    enabled_actions: frozenset[WeekAction] = frozenset()
    schedule_kind: Literal[ScheduleKind.DAILY, ScheduleKind.WEEKLY] = ScheduleKind.WEEKLY
    schedule_managed: bool = Field(
        default=False, validation_alias=AliasChoices("schedule_managed", "wb_schedule_managed")
    )
    export_actions: frozenset[GoogleExportKind] = Field(
        default=frozenset(), validation_alias=AliasChoices("export_actions", "wb_export_actions")
    )
    export_target: ExportDestination = Field(default_factory=ExportDestination)
    freeze_sheet_ids: tuple[int, ...] = ()
    selection_initialized: bool = False

    @classmethod
    def defaults_for(cls, marketplace: Marketplace) -> Self:
        preset = next(value for value in GOOGLE_MARKETPLACE_DEFAULTS if value.marketplace == marketplace)
        return cls(
            spreadsheet_url=preset.spreadsheet_url,
            sheet_name=preset.sheet_name,
            sales_sheet_name=preset.sales_sheet_name,
            stock_sheet_name=preset.stock_sheet_name,
            cells=preset.cells,
            weekday=preset.weekday,
            run_time=preset.run_time,
            schedule_kind=preset.schedule_kind,
            schedule_managed=preset.schedule_managed,
            enabled_actions=preset.enabled_actions,
            export_actions=preset.export_actions,
            export_target=ExportDestination(
                stock_sheet_name=preset.export_stock_sheet_name,
                orders_sheet_name=preset.export_orders_sheet_name,
                orders_quantity_column=preset.export_orders_quantity_column,
                fbo_sheet_name=preset.export_fbo_sheet_name,
                fbo_to_customer_column=preset.export_fbo_to_customer_column,
                fbo_from_customer_column=preset.export_fbo_from_customer_column,
            ),
            freeze_sheet_ids=preset.freeze_sheet_ids,
            selection_initialized=preset.selection_initialized,
        )

    @field_validator("sheet_name", "stock_sheet_name")
    @classmethod
    def sheet_title(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > GOOGLE_WEEK.max_sheet_name or any(ord(char) < 32 for char in value):
            raise ValueError(WeekIssue.INVALID_SETTINGS)
        return value

    @field_validator("sales_sheet_name")
    @classmethod
    def sales_title(cls, value: str) -> str:
        return cls.sheet_title(value) if value.strip() else ""

    @field_validator("cells")
    @classmethod
    def addresses(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cells = tuple(dict.fromkeys(value.strip().upper() for value in values))
        if not 1 <= len(cells) <= GOOGLE_WEEK.max_cells or any(
            not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", cell) for cell in cells
        ):
            raise ValueError(WeekIssue.INVALID_SETTINGS)
        return cells

    @field_validator("freeze_sheet_ids", mode="before")
    @classmethod
    def identifiers(cls, values: object) -> tuple[int, ...]:
        if not isinstance(values, (list, tuple)) or any(
            type(value) is not int or value < 0 for value in values
        ):
            raise ValueError(WeekIssue.SELECTION)
        return tuple(sorted(set(values)))

    @model_validator(mode="after")
    def normalize_url(self) -> Self:
        if self.spreadsheet_url:
            try:
                identifier = document_id(self.spreadsheet_url.strip())
            except WeekError as error:
                raise ValueError(WeekIssue.INVALID_SETTINGS) from error
            self.spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{identifier}/edit"
        return self


class HeaderCell(WeekModel):
    cell: str
    value: str


class HeaderStop(HeaderCell):
    reason: str = "format"


class HeaderMatch(WeekModel):
    cell: str
    range: str
    row: int
    headers: list[HeaderCell]
    columns: dict[str, list[HeaderCell]]
    stop: HeaderStop | None = None


class HeaderSource(HeaderCell):
    matches: list[HeaderMatch] = Field(default_factory=list)
    issue: str | None = None


class WeekPeriod(HeaderCell):
    date_from: date
    date_to: date
    snapshot_day: date | None = None


class ProductIssue(WeekModel):
    row: int = 0
    article: str = ""
    project: str = ""
    reason: str = ""
    cell: str = ""
    week: str = ""
    missing_days: list[date] = Field(default_factory=list)


class FreezeCellSkip(WeekModel):
    cell: str
    reason: WeekIssue


class FreezeSheetResult(WeekModel):
    sheet_id: int
    sheet_name: str
    ranges: list[str] = Field(default_factory=list)
    formula_count: int = 0
    written_cells: int = 0
    issue: str | None = None
    skipped_cells: list[FreezeCellSkip] = Field(default_factory=list)


class SnapshotTime(WeekModel):
    store_slug: str
    day: date
    captured_at: datetime


class ProductExportResult(WeekModel):
    row: int
    article: str
    project: str
    checked_cells: int = 0
    written_cells: int = 0
    unchanged_cells: int = 0


class WeekReport(WeekModel):
    marketplace: Marketplace = Marketplace.WB
    spreadsheet_url: str = ""
    sheet_name: str = ""
    sheet_id: int = 0
    source_sheet_name: str = ""
    source_cells: list[str] = Field(default_factory=list)
    matching_key: str = GOOGLE_WEEK.matching_key
    sources: list[HeaderSource] = Field(default_factory=list)
    total_cells: int = 0
    total_matches: int = 0
    periods: list[WeekPeriod] = Field(default_factory=list)
    issues: list[ProductIssue] = Field(default_factory=list)
    missing_data: list[ProductIssue] = Field(default_factory=list)
    zero_filled: list[ProductIssue] = Field(default_factory=list)
    matched_products: int = 0
    products: list[ProductExportResult] | None = None
    product_rows: int = 0
    complete: bool = True
    processed_at: datetime | None = None
    checked_cells: int = 0
    written_cells: int = 0
    zeroed_cells: int = 0
    unchanged_cells: int = 0
    backup: str | None = None
    snapshot_times: list[SnapshotTime] = Field(default_factory=list)
    sheets: list[FreezeSheetResult] = Field(default_factory=list)
    sheet_ids: list[int] = Field(default_factory=list)
    spreadsheet_id: str = ""
    week: str = ""
    value: str = ""
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)
    total_formulas: int = 0


class WeekActionState(WeekModel):
    activated_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_success_slot: datetime | None = None
    last_error: WeekProblem | None = None
    result: WeekReport | None = None


class SheetOption(WeekModel):
    sheet_id: int
    title: str


class WeekCatalog(WeekModel):
    sheets: list[SheetOption] = Field(default_factory=list)
    updated_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_error: WeekProblem | None = None


class WeekProfile(WeekModel):
    marketplace: Marketplace
    settings: WeekSettings
    saved_at: datetime | None = None
    states: dict[WeekAction, WeekActionState] = Field(
        default_factory=lambda: {a: WeekActionState() for a in WeekAction}
    )
    export_states: dict[GoogleExportKind, WeekActionState] = Field(
        default_factory=lambda: {kind: WeekActionState() for kind in GoogleExportKind},
        validation_alias=AliasChoices("export_states", "wb_export_states"),
    )
    catalog: WeekCatalog = Field(default_factory=WeekCatalog)


class WeekView(WeekModel):
    profile: WeekProfile
    value: str
    next_runs: dict[WeekAction, datetime | None]
    export_next_runs: dict[GoogleExportKind, datetime | None] = Field(default_factory=dict)
    freeze_waiting: bool
    catalog_due: bool
    background_enabled: bool
    busy: bool = False
    preview: WeekReport | None = None


class CellValue(WeekModel):
    numberValue: float | None = None
    stringValue: str | None = None
    boolValue: bool | None = None
    formulaValue: str | None = None
    errorValue: dict[str, JsonValue] | None = None


class GridCell(WeekModel):
    userEnteredValue: CellValue = Field(default_factory=CellValue)
    effectiveValue: CellValue = Field(default_factory=CellValue)
    formattedValue: str = ""
    pivotTable: dict[str, JsonValue] | None = None
    dataSourceTable: dict[str, JsonValue] | None = None
    dataSourceFormula: dict[str, JsonValue] | None = None


class GridRange(WeekModel):
    sheetId: int = 0
    startRowIndex: int = 0
    endRowIndex: int | None = None
    startColumnIndex: int = 0
    endColumnIndex: int | None = None


class GridBounds(WeekModel):
    rowCount: int = 0
    columnCount: int = 0


class SheetProperties(WeekModel):
    sheetId: int
    title: str
    sheetType: str = "GRID"
    gridProperties: GridBounds = Field(default_factory=GridBounds)


class GridProtection(WeekModel):
    range: GridRange | None = None
    namedRangeId: str = ""
    warningOnly: bool = False
    requestingUserCanEdit: bool = False
    unprotectedRanges: list[GridRange] = Field(default_factory=list)


class GridRow(WeekModel):
    values: list[GridCell] = Field(default_factory=list)


class GridData(WeekModel):
    startRow: int = 0
    startColumn: int = 0
    rowData: list[GridRow] = Field(default_factory=list)


class GoogleSheet(WeekModel):
    properties: SheetProperties
    merges: list[GridRange] = Field(default_factory=list)
    protectedRanges: list[GridProtection] = Field(default_factory=list)
    data: list[GridData] = Field(default_factory=list)


class NamedRange(WeekModel):
    namedRangeId: str
    range: GridRange


class GoogleDocument(WeekModel):
    sheets: list[GoogleSheet] = Field(default_factory=list)
    namedRanges: list[NamedRange] = Field(default_factory=list)


class CellWrite(WeekModel):
    cell: str
    value: int | str


class Product(WeekModel):
    store_slug: str
    article: str
    aliases: list[str] = Field(default_factory=list)
    image_url: str = ""


class ProductImageKey(WeekModel):
    article: str
    project: str


class ProductImageRequest(WeekModel):
    items: tuple[ProductImageKey, ...] = Field(max_length=GOOGLE_WEEK.image_batch_size)


class ProductImage(ProductImageKey):
    image_url: str = ""


class MatchedProduct(Product):
    row: int
    project: str


class DailyFact(WeekModel):
    store_slug: str
    article: str | None
    day: date
    quantity: int | None
    captured_at: datetime | None = None


class StoreFacts(WeekModel):
    facts: list[DailyFact] = Field(default_factory=list)
    confirmed: set[date] = Field(default_factory=set)
    error: WeekProblem | None = None


class OzonWeekOrder(WeekModel):
    article: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    accepted_at: AwareDatetime
    ordered_at: AwareDatetime


class ExportPlan(WeekModel):
    report: WeekReport
    writes: list[CellWrite]
    watched: set[str] = Field(default_factory=set)


class FormulaBackup(WeekModel):
    cell: str
    previous: CellValue
    effective_value: CellValue


class FreezeValueRange(WeekModel):
    range: str
    values: list[list[float | str | bool]]


class FreezePlan(WeekModel):
    report: FreezeSheetResult
    ranges: list[GridRange] = Field(default_factory=list)
    formulas: list[FormulaBackup] = Field(default_factory=list)
    signature: str = ""


class WeekJobResult(WeekModel):
    error: str | None = None
