"""Mutable working state, confined to snapshot construction."""

from dataclasses import dataclass, field

from pydantic import ConfigDict, Field

from app.dto.wb_standard_costs import DeliveryTerm, ReportRow, StorageDetail


class RowDraft(ReportRow):
    model_config = ConfigDict(frozen=False)
    details: list[StorageDetail] = Field(default_factory=list)


@dataclass
class RowAccumulator:
    row: RowDraft
    stock_terms: list[DeliveryTerm] = field(default_factory=list)
    sales_terms: list[DeliveryTerm] = field(default_factory=list)
    missing: set[str] = field(default_factory=set)
    delivery_sum: float = 0
    delivery_weight: float = 0
    sales_delivery_sum: float = 0
    sales_delivery_weight: float = 0
    bad_delivery: bool = False
    bad_sales_delivery: bool = False
    bad_storage: bool = False
    bad_commission: bool = False
