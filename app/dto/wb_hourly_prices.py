"""Named contracts for hourly WB price observations."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class WbCatalogItem:
    article: str


@dataclass(frozen=True, slots=True)
class WbStorefrontSize:
    nm_id: str
    size_id: int | None
    tech_size_name: str
    customer_price_with_spp: float
    product_kopecks: int
    logistics_kopecks: int
    price_fields_json: str
    sale_conditions: int


@dataclass(frozen=True, slots=True)
class WbStorefrontResult:
    sizes: tuple[WbStorefrontSize, ...]
    failed_nm_ids: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WbSellerPrice:
    nm_id: str
    size_id: int | None
    tech_size_name: str
    discounted_price: Decimal


@dataclass(frozen=True, slots=True)
class WbHourlyPriceObservation:
    store_slug: str
    article: str
    observed_hour: str
    observed_at: str
    nm_id: str
    size_id: int | None
    tech_size_name: str
    customer_price_with_spp: float
    spp_percent: float | None
    orders_turnover_rub: float | None
    orders_turnover_updated_at: str | None
    customer_price_with_wallet: float | None
    wallet_discount_percent: float | None
    product_kopecks: int
    logistics_kopecks: int
    price_fields_json: str
    sale_conditions: int


@dataclass(frozen=True, slots=True)
class WbHourlyTurnover:
    article: str
    net_orders_amount: float
    updated_at: str


@dataclass(frozen=True, slots=True)
class WbHourlyStoreReport:
    store_slug: str
    ok: bool
    rows: int
    catalog_products: int | None = None
    missing_prices: int | None = None
    failed_nm_ids: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class WbHourlyCollection:
    reports: tuple[WbHourlyStoreReport, ...]

    def for_store(self, store_slug: str) -> WbHourlyStoreReport:
        return next(report for report in self.reports if report.store_slug == store_slug)
