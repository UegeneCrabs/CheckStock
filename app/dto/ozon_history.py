from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import Field

from app.core.constants import OzonHistoryField
from app.dto.common import DtoModel
from app.dto.ozon_advertising import AdvertisingSourceStatus
from app.dto.ozon_economics import OzonTurnoverData
from app.dto.ozon_prices import PriceValue


class OzonHistoryQuery(DtoModel):
    store: str = Field(min_length=1)
    article: str = Field(min_length=1)


class OzonStockDay(DtoModel):
    day: date
    fbo: int | None = None
    fbs: int | None = None
    fulfillment: int | None = None


class OzonPriceDay(DtoModel):
    day: date
    value: PriceValue


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonHistoryEvidence:
    turnover: OzonTurnoverData
    advertising: tuple[AdvertisingSourceStatus, ...]
    stocks: tuple[OzonStockDay, ...]
    prices: tuple[OzonPriceDay, ...]


class OzonHistoryDay(DtoModel):
    date: date
    orders_count: int | None
    turnover_rub: Decimal | None
    advertising_rub: Decimal | None
    drr_percent: float | None
    buyout_percent: float | None
    stock_units: int | None
    fbo_units: int | None
    fbs_units: int | None
    fulfillment_units: int | None
    price_with_spp_rub: Decimal | None
    margin_rub: None = None
    partial_fields: tuple[OzonHistoryField, ...] = ()
    stale_fields: tuple[OzonHistoryField, ...] = ()


class OzonHistory(DtoModel):
    today: date
    period_from: date
    period_to: date
    days: tuple[OzonHistoryDay, ...]
