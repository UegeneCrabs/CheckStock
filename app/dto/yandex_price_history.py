from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class YandexPriceObservation:
    store_slug: str
    article: str
    observed_at: datetime
    business_id: str | None
    market_sku: str | None
    card_id: str | None
    seller_price: float | None
    customer_price_with_spp: float
    spp_percent: float | None
