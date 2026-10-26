from datetime import date

from pydantic import Field

from app.core.constants import OZON_NEWNESS
from app.dto.common import DtoModel


class OzonActivityStart(DtoModel):
    store_slug: str
    article: str
    day: date


class OzonNewness(DtoModel):
    first_order_day: date | None = None
    first_stock_day: date | None = None
    observed_since: date | None = None
    observed_days: int | None = Field(default=None, ge=0)
    is_new: bool | None = None
    threshold_days: int = OZON_NEWNESS.max_days
