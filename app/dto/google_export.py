from typing import Literal

from pydantic import Field

from app.dto.common import DtoModel


class GoogleExportTarget(DtoModel):
    marketplace: str
    stock_sheet_name: str
    orders_sheet_name: str
    spreadsheet_url: str
    orders_quantity_column: str
    fbo_sheet_name: str
    fbo_to_customer_column: str
    fbo_from_customer_column: str


class GoogleExportSettings(DtoModel):
    enabled: bool
    schedule_kind: str
    weekday: int
    run_time: str
    updated_at: str
    last_attempt_at: str | None
    last_success_at: str | None
    last_error: str | None
    targets: tuple[GoogleExportTarget, ...]


class GoogleExportView(DtoModel):
    ok: bool = True
    settings: GoogleExportSettings
    service_account_email: str


class GoogleExportUpdate(DtoModel):
    enabled: bool
    schedule_kind: Literal["daily", "weekly"]
    weekday: int = Field(ge=0, le=6)
    run_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    targets: tuple[GoogleExportTarget, ...]


class GoogleExportMarketplaceResult(DtoModel):
    marketplace: str
    skipped: bool = False
    updated_cells: int = 0
    warnings: tuple[str, ...] = ()


class GoogleExportRunReport(DtoModel):
    marketplaces: tuple[GoogleExportMarketplaceResult, ...]
    store_slugs: tuple[str, ...]
    last_success_at: str | None = None
    warnings: tuple[str, ...] = ()


class GoogleExportRunResult(DtoModel):
    ok: bool = True
    report: GoogleExportRunReport
