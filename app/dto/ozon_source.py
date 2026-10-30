from dataclasses import dataclass

from pydantic import Field

from app.dto.common import DtoModel

type OzonSourceCell = str | int | float | bool | None


class OzonSourceSheet(DtoModel):
    sheet_id: int = Field(ge=0)
    title: str
    rows: tuple[tuple[OzonSourceCell, ...], ...]


class OzonSourceCatalogItem(DtoModel):
    id: int = Field(gt=0)
    store_slug: str
    article: str
    mp_product_id: str | int | None = None
    mp_sku: str | int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonSourceCatalogIndex:
    by_product_id: dict[str, list[OzonSourceCatalogItem]]
    by_sku: dict[str, list[OzonSourceCatalogItem]]


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonSourceMatch:
    candidates: tuple[OzonSourceCatalogItem, ...]
    by_sku: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonSourceHeader:
    index: int
    columns: dict[str, int]


class OzonSourceTag(DtoModel):
    tag_raw: str | None
    goal_week: float | None
    goal_day: float | None
    stock_status: str | None
    stock_end_week: str | None
    supplier_external_raw: str | None
    abc_code: str | None
    fact_sales: float | None
    plan_sales: float | None


class OzonSourceValues(OzonSourceTag):
    stock_item_id: int
    manager: str | None
    purchase_price: float | None
    fulfillment_cost: float | None
    team_commission_percent: float | None
    source_sheet_id: int
    source_sheet_title: str
    source_row: int

    def equivalent_to(self, other: "OzonSourceValues") -> bool:
        return (
            self.purchase_price,
            self.fulfillment_cost,
            self.team_commission_percent,
            self.tag_raw,
            self.manager,
        ) == (
            other.purchase_price,
            other.fulfillment_cost,
            other.team_commission_percent,
            other.tag_raw,
            other.manager,
        )


class OzonSourceReport(DtoModel):
    rows: tuple[OzonSourceValues, ...]
    team_commissions: dict[str, float]
    sheet_count: int
    source_rows: int
    skipped_not_ozon: int
    matched: int
    unmatched: int
    unmatched_examples: tuple[str, ...]
    ambiguous: int
    duplicates: int
    sku_matched: int
    outside_sheet_store_matched: int
    commission_conflicts: dict[str, dict[float, int]]
