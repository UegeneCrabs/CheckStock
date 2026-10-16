from datetime import date, datetime

from app.core.constants import OrderScheme
from app.dto.common import DtoModel


class OzonOrderDayState(DtoModel):
    store_slug: str
    scheme: OrderScheme
    day: date
    listed: bool = False
    finalized: bool = False
    ok: bool = False
    attempted_at: datetime
    error: str | None = None
