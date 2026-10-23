from datetime import date

from app.dto.common import DtoModel


class StockTarget(DtoModel):
    store_slug: str
    marketplace: str


class StockPageQuery(DtoModel):
    stores: tuple[str, ...]
    targets: tuple[StockTarget, ...]
    store: str = ""
    marketplace: str = ""
    can_edit: bool = False


class CatalogProduct(DtoModel):
    article: str
    barcode: str | None = None
    name: str | None = None
    image_url: str | None = None
    barcodes: tuple[str, ...] = ()


class StockProduct(CatalogProduct):
    ff_available: int
    transit: int
    schemes: dict[str, int]
    total: int


class StockStoreData(DtoModel):
    ok: bool = True
    store_slug: str
    marketplace: str
    can_edit: bool
    ready: bool
    schemes: tuple[str, ...]
    fulfillments: tuple[str, ...]
    items: tuple[StockProduct, ...]
    totals: dict[str, int]


class StockOverviewStore(DtoModel):
    store_slug: str
    total_stock: int = 0
    sku_count: int = 0
    marketplaces: tuple[str, ...] = ()
    marketplace_count: int = 0


class StockOverview(DtoModel):
    ok: bool = True
    stores: tuple[StockOverviewStore, ...]
    last_sync: str | None
    total_stock: int
    total_sku: int
    total_connections: int


class WarehouseQuantity(CatalogProduct):
    warehouse: str
    quantity: int
    checked: bool = False


class WarehouseData(DtoModel):
    ok: bool = True
    store_slug: str
    marketplace: str
    can_edit: bool
    fbo: tuple[WarehouseQuantity, ...]
    fbs: tuple[WarehouseQuantity, ...]
    fulfillment: tuple[WarehouseQuantity, ...]
    trash: tuple[WarehouseQuantity, ...]


class TotalStockRow(CatalogProduct):
    store_slug: str
    store_slugs: tuple[str, ...]
    locations: tuple[StockTarget, ...]
    articles: tuple[str, ...]
    purchase_price: float | None
    quantities: dict[str, int]


class TotalStockData(DtoModel):
    ok: bool = True
    store: str
    rows: tuple[TotalStockRow, ...]
    value_keys: tuple[str, ...]
    totals: dict[str, int]


class StockOperation(DtoModel):
    id: int
    store_slug: str
    kind: str
    created_at: str
    from_fulfillment: str | None = None
    to_fulfillment: str | None = None
    from_marketplace: str | None = None
    to_marketplace: str | None = None
    source_name: str | None = None
    source_type: str | None = None
    note: str | None = None
    user_name: str | None = None
    units: int = 0
    positions: int = 0
    purchase_cost: float | None = None
    is_fbs_transfer: bool = False


class OperationSummary(DtoModel):
    total: int
    positions: int
    units: int
    employees: int


class OperationData(DtoModel):
    ok: bool = True
    store_slug: str
    kind: str
    page: int
    page_size: int
    counts: dict[str, int]
    summary: OperationSummary
    operations: tuple[StockOperation, ...]


class CostMetric(DtoModel):
    operations: int
    positions: int
    units: int
    cost: float
    missing_units: int


class FbsFormula(DtoModel):
    available: bool
    start_units: int = 0
    moved_units: int = 0
    end_units: int = 0


class CostSummary(DtoModel):
    store_slug: str
    marketplace: str
    deliveries: CostMetric
    moved_out: CostMetric
    moved_in: CostMetric
    shipped: CostMetric
    fbs_sales: CostMetric
    fbs_actual_sales: CostMetric
    fbs_formula: FbsFormula | None


class CostSale(CatalogProduct):
    store_slug: str
    marketplace: str
    quantity: int
    purchase_price: float | None
    purchase_cost: float | None
    start_quantity: int | None = None
    moved_quantity: int | None = None
    end_quantity: int | None = None


class CostReportData(DtoModel):
    ok: bool = True
    date_from: date
    date_to: date
    current_from: date
    current_to: date
    previous_from: date
    previous_to: date
    can_edit: bool
    summary: tuple[CostSummary, ...]
    operations: tuple[StockOperation, ...]
    fbs_sales: tuple[CostSale, ...]
    fbs_actual_sales: tuple[CostSale, ...]


class SupplyPageData(DtoModel):
    ok: bool = True
    min_date: date
    max_date: date
    default_from: date
    default_to: date


class FbsQuantities(DtoModel):
    fbs: dict[str, int]
    rfbs: dict[str, int]


class FulfillmentQuantities(DtoModel):
    ff_available: dict[str, int]


class TransitQuantities(DtoModel):
    transit: dict[str, int]


class ArticleWarehouse(DtoModel):
    name: str
    available: int
    fbs: int


class ArticleTransit(DtoModel):
    transfer_id: int
    from_fulfillment: str
    to_fulfillment: str | None
    sent_at: str
    status: str
    quantity: int


class StockArticleDetail(DtoModel):
    article: str
    marketplace: str
    warehouses: tuple[ArticleWarehouse, ...]
    transit: tuple[ArticleTransit, ...]


class StockClassificationCommand(DtoModel):
    operation_id: int
    is_fbs_transfer: bool
    user_id: int
    updated_at: str


class StockClassificationResult(DtoModel):
    ok: bool = True
    operation_id: int
    is_fbs_transfer: bool
