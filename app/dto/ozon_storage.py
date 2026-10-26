from datetime import date, datetime, timedelta
from typing import Self

from pydantic import Field, model_validator

from app.core.constants import OZON_STORAGE, OzonStorageState
from app.dto.common import DtoModel
from app.dto.ozon_logistics import LogisticsAccrued, Money
from app.dto.ozon_orders import Identifier, OzonPayload, Quantity


class StoragePeriod(DtoModel):
    day: date
    start: date
    end: date

    @classmethod
    def for_day(cls, day: date) -> Self:
        return cls(day=day, start=day - timedelta(days=OZON_STORAGE.window_days), end=day - timedelta(days=1))

    @model_validator(mode="after")
    def valid_window(self) -> Self:
        if self.end != self.day - timedelta(days=1) or self.start != self.day - timedelta(
            days=OZON_STORAGE.window_days
        ):
            raise ValueError("Storage requires the previous 30 completed Moscow days")
        return self


class PlacementRequest(DtoModel):
    date_from: date
    date_to: date


class PlacementCode(OzonPayload):
    code: str = Field(min_length=1)


class ReportRequest(DtoModel):
    code: str


class PlacementInfo(OzonPayload):
    status: str = Field(min_length=1)
    report_type: str
    file: str = ""
    error: str = ""


class PlacementInfoPage(OzonPayload):
    result: PlacementInfo


class StorageReportTicket(DtoModel):
    code: str = ""
    attempts: int = 0


class StorageRow(DtoModel):
    day: date
    sku: str
    article: str
    expenses: Money


class StorageReport(DtoModel):
    rows: tuple[StorageRow, ...]
    sha256: str


class StorageAccrualRequest(DtoModel):
    date: date
    last_id: str = ""


class StorageSaleCommission(OzonPayload):
    sale_amount: LogisticsAccrued


class StorageSaleProduct(OzonPayload):
    sku: Identifier
    quantity: Quantity
    commission: StorageSaleCommission | None = None


class StorageSalePosting(OzonPayload):
    delivery_schema: str = Field(min_length=1)
    products: tuple[StorageSaleProduct, ...]


class StorageAccrual(OzonPayload):
    accrual_id: Identifier
    date: date
    unit_number: Identifier = ""
    posting: StorageSalePosting | None = None


class StorageAccrualPage(OzonPayload):
    accruals: tuple[StorageAccrual, ...]
    last_id: str


class StorageSale(DtoModel):
    day: date
    sku: str
    sold: int
    returned: int
    posting_number: str = ""


class StoragePostingRequest(DtoModel):
    posting_number: str


class StoragePostingProduct(OzonPayload):
    sku: Identifier
    offer_id: str


class StoragePosting(OzonPayload):
    posting_number: str
    products: tuple[StoragePostingProduct, ...]


class StoragePostingPage(OzonPayload):
    result: StoragePosting


class StorageArticle(DtoModel):
    sku: str
    article: str


class StorageEvidence(DtoModel):
    period: StoragePeriod
    report: StorageReport
    sales: tuple[StorageSale, ...]
    articles: tuple[StorageArticle, ...]


class StorageDay(DtoModel):
    day: date
    expenses: Money
    sold_units: int
    returned_units: int
    report_rows: int


class StorageSnapshot(DtoModel):
    store_slug: str
    article: str
    period: StoragePeriod
    observed_at: datetime
    version: int = OZON_STORAGE.formula_version
    state: OzonStorageState
    expenses: Money
    sold_units: int
    returned_units: int
    retained_units: int
    per_unit: Money | None
    days: tuple[StorageDay, ...]
    report_sha256: str
    report_days: tuple[date, ...]
    issues: tuple[str, ...] = ()


class StorageData(DtoModel):
    snapshot: StorageSnapshot | None = None
    stale: bool = False
    error: str | None = None
    attempted_at: datetime | None = None


class StorageStoreSnapshot(DtoModel):
    store_slug: str
    period: StoragePeriod
    observed_at: datetime
    products: tuple[StorageSnapshot, ...]


class StorageSyncResult(DtoModel):
    stores: int = 0
    products: int = 0
    skipped: int = 0
    error: str | None = None


class StorageStoreData(DtoModel):
    store_slug: str
    products: tuple[StorageSnapshot, ...] = ()
    attempted_at: datetime | None = None
    error: str | None = None
