from decimal import Decimal
from typing import Annotated

from pydantic import Field

from app.dto.common import DtoModel
from app.dto.ozon_logistics import LogisticsInputs, LogisticsPeriod, Money
from app.dto.ozon_logistics_volume import Volume
from app.dto.ozon_storage import StoragePeriod


class OzonFboLocality(DtoModel):
    period: LogisticsPeriod
    local: int = Field(default=0, ge=0)
    nonlocal_count: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0)
    covered_days: int = Field(default=0, ge=0)

    @property
    def local_share(self) -> Decimal | None:
        total = self.local + self.nonlocal_count
        return Decimal(self.local) / total if total else None


class OzonNewLogisticsSource(DtoModel):
    volume_l: Annotated[Money, Field(gt=0)] | None = None
    locality: OzonFboLocality


class OzonNewStorageEstimate(DtoModel):
    period: StoragePeriod
    expenses: Money
    retained_units: int = Field(gt=0)

    @property
    def per_unit(self) -> Decimal:
        return self.expenses / self.retained_units


class OzonNewProductCosts(DtoModel):
    logistics: OzonNewLogisticsSource | None = None
    storage: OzonNewStorageEstimate | None = None


class OzonNewLogisticsEstimate(DtoModel):
    inputs: LogisticsInputs
    total: Money | None = None
    modeled: Money | None = None
    local_tariff: Money | None = None
    nonlocal_tariff: Money | None = None
    upper_l: Volume | None = None
    price_limit: Money
    up_to_price_limit: bool | None = None
