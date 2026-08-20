from datetime import datetime

from pydantic import Field

from app.dto.common import DtoModel


class ManualSupplyInput(DtoModel):
    delivery_at: datetime
    origin: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=200)
    supply_type: str = Field(min_length=1, max_length=100)
    ready: bool = False


class ManualSupplyReadyInput(DtoModel):
    ready: bool
