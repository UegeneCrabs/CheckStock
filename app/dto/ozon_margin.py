from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import Field

from app.core.constants import OZON_MARGIN
from app.dto.common import DtoModel

MarginNumber = Annotated[Decimal, Field(allow_inf_nan=False)]


class OzonMarginInputs(DtoModel):
    seller_price: MarginNumber | None = None
    purchase: MarginNumber | None = None
    fulfillment: MarginNumber | None = None
    commission_amount: MarginNumber | None = None
    logistics_amount: MarginNumber | None = None
    storage_amount: MarginNumber | None = None
    advertising_amount: MarginNumber | None = None
    acquiring_amount: MarginNumber | None = None
    team_amount: MarginNumber | None = None
    unallocated_amount: MarginNumber | None = Decimal(0)
    vat_amount: MarginNumber | None = None
    usn_amount: MarginNumber | None = None


class OzonMarginResult(DtoModel):
    margin: MarginNumber | None = None
    roi: MarginNumber | None = None
    missing: tuple[str, ...] = ()


class OzonMarginSnapshot(DtoModel):
    store_slug: str
    article: str
    day: date
    observed_at: datetime
    version: int = OZON_MARGIN.version
    inputs: OzonMarginInputs
    buyout_percent: MarginNumber | None = None
    issues: tuple[str, ...] = ()


class OzonMarginDay(DtoModel):
    day: date
    inputs: OzonMarginInputs
    margin: MarginNumber | None = None
    profit: MarginNumber | None = None
    expected_buyouts: MarginNumber | None = None
    purchase_value: MarginNumber | None = None
    orders: int | None = None
    advertising_spend: MarginNumber | None = None
    buyout_percent: MarginNumber | None = None
    roi: MarginNumber | None = None
    missing: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()


class OzonMarginSyncResult(DtoModel):
    products: int = 0
    error: str | None = None


class OzonMarginSource(DtoModel):
    store_slug: str
    article: str
    mp_product_id: str | None
    purchase_price: Decimal | None
    fulfillment_cost: Decimal | None
    team_commission_percent: Decimal | None
