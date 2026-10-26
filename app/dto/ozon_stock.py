from datetime import datetime

from app.dto.common import DtoModel


class OzonStockEvidence(DtoModel):
    fbs: int | None = None
    rfbs: int | None = None
    fbo: int | None = None
    catalog_observed_at: datetime | None = None
    source_checked_at: datetime | None = None
    source_ok: bool = False


class OzonStockQuantities(DtoModel):
    fbs: int | None
    fbo: int | None
