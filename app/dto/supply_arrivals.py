from datetime import date

from pydantic import Field

from app.dto.common import DtoModel


class SupplyArrival(DtoModel):
    row: int = Field(ge=1)
    store_slug: str = ""
    project: str = ""
    order: str = Field(min_length=1)
    group: str = ""
    file: str = ""
    category: str = ""
    warehouse: str = ""
    status: str = ""
    arrival: date | None = None
    volume: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    weight: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    boxes: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    shipping: str = ""
    warnings: tuple[str, ...] = ()
