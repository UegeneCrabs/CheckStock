from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from app.core.constants import (
    ORDER_NORMALIZATION,
    OZON_ORDERS,
    SCHEDULER,
    Marketplace,
    OrderSyncStage,
)
from app.core.constants import (
    OrderScheme as OrderScheme,
)
from app.dto.common import DtoModel


@dataclass(frozen=True, slots=True)
class OzonCredentials:
    client_id: str
    api_key: str = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonCatalogArticle:
    sku: str
    article: str


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonArticleIndex:
    items: tuple[OzonCatalogArticle, ...]
    by_sku: Mapping[str, frozenset[str]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        articles: dict[str, set[str]] = {}
        for item in self.items:
            if item.sku.strip() and item.article.strip():
                articles.setdefault(item.sku, set()).add(item.article)
        object.__setattr__(
            self, "by_sku", MappingProxyType({sku: frozenset(values) for sku, values in articles.items()})
        )


class OrderPeriod(DtoModel):
    start: date
    end: date

    @model_validator(mode="after")
    def chronological(self) -> Self:
        if self.end <= self.start:
            raise ValueError("Order period must have end > start")
        return self


@dataclass(frozen=True, slots=True)
class OzonOrderProduct:
    key: str
    article: str
    barcode: str
    name: str
    quantity: int
    unit_price: float
    currency: str


@dataclass(frozen=True, slots=True)
class OzonOrderPosting:
    key: str
    external_order_id: str
    status: str
    substatus: str
    cancelled: bool
    delivered: bool
    ordered_at: str
    updated_at: str
    sold_at: str
    products: tuple[OzonOrderProduct, ...]
    raw_json: str


class SalesOrderLine(DtoModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    store_slug: str
    marketplace: Literal[Marketplace.OZON] = Marketplace.OZON
    order_key: str
    line_key: str
    external_order_id: str
    scheme: OrderScheme
    status: str
    substatus: str
    article: str
    barcode: str
    name: str
    ordered_at: str
    source_updated_at: str
    cancelled_at: str | None
    sold_at: str | None
    returned_at: str | None = None
    quantity: int
    cancelled_quantity: int
    sold_quantity: int
    return_quantity: int = 0
    order_amount: float
    cancelled_amount: float
    sale_amount: float
    return_amount: float = 0.0
    currency: str
    raw_json: str


class OrderSyncIssue(DtoModel):
    stage: OrderSyncStage
    error_type: str
    message: str
    scheme: OrderScheme | None = None
    period: OrderPeriod | None = None

    @property
    def description(self) -> str:
        prefix = self.scheme.upper() if self.scheme else self.stage
        if self.period:
            prefix += f" {self.period.start:{ORDER_NORMALIZATION.issue_date_format}}-{self.period.end:{ORDER_NORMALIZATION.issue_date_format}}"
        return f"{prefix}: {self.error_type}: {self.message}"


class StoreOrderSyncResult(DtoModel):
    store_slug: str
    rows: int = Field(ge=0)
    errors: tuple[OrderSyncIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


class OrderSyncResult(DtoModel):
    stores: tuple[StoreOrderSyncResult, ...]

    @property
    def error(self) -> str | None:
        descriptions = (
            f"{store.store_slug}: {issue.description}" for store in self.stores for issue in store.errors
        )
        return "; ".join(dict.fromkeys(descriptions))[: SCHEDULER.max_error_length] or None


class OzonOrderDayState(DtoModel):
    store_slug: str
    scheme: OrderScheme
    day: date
    listed: bool = False
    finalized: bool = False
    ok: bool = False
    attempted_at: datetime
    error: str | None = None


class OzonPendingPosting(DtoModel):
    scheme: OrderScheme
    day: date
    posting_number: str
    order_number: str
    filter_day: date | None = None


class OrderSaveBatch(DtoModel):
    store_slug: str
    lines: tuple[SalesOrderLine, ...]
    attempted_at: datetime
    period: OrderPeriod
    errors: tuple[OrderSyncIssue, ...] = ()
    days: tuple[OzonOrderDayState, ...] = ()
    incremental: bool = False
    report_status: bool = True
    rows_received: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def belongs_to_store(self) -> Self:
        if any(line.store_slug != self.store_slug for line in self.lines):
            raise ValueError("Order lines belong to a different store")
        if any(day.store_slug != self.store_slug for day in self.days):
            raise ValueError("Order day states belong to a different store")
        if self.attempted_at.tzinfo is None:
            raise ValueError("Attempt time must include a timezone")
        return self

    @property
    def error(self) -> str | None:
        return "; ".join(issue.description for issue in self.errors)[: OZON_ORDERS.max_error_length] or None

    @property
    def lookback_days(self) -> int:
        return (self.period.end - self.period.start).days
