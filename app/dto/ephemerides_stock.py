from datetime import date, datetime

from app.dto.common import DtoModel


class EphemeridesStockProduct(DtoModel):
    article: str
    fbs: int | None
    fbo: int | None
    ff: int
    fromCustomer: int | None


class EphemeridesStockSnapshot(DtoModel):
    store: str
    week: date
    products: tuple[EphemeridesStockProduct, ...]
    captured_at: datetime
