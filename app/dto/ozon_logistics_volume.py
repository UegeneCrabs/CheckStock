from datetime import date
from decimal import Decimal
from typing import Annotated, Self

from pydantic import Field, model_validator

from app.core.constants import OZON_LOGISTICS, OZON_SCENARIO, OzonVolumeState
from app.dto.common import DtoModel

Volume = Annotated[
    Decimal,
    Field(
        gt=0,
        le=OZON_LOGISTICS.max_volume_l,
        allow_inf_nan=False,
        max_digits=OZON_LOGISTICS.volume_max_digits,
        decimal_places=OZON_LOGISTICS.volume_decimal_places,
    ),
]
TariffMoney = Annotated[Decimal, Field(ge=0, le=OZON_SCENARIO.max_money, allow_inf_nan=False)]
Cluster = Annotated[str, Field(min_length=1, max_length=OZON_LOGISTICS.cluster_max_length)]


class LogisticsVolumeScenario(DtoModel):
    volume_l: Volume | None = None
    seller_price: TariffMoney | None = None
    origin: Cluster | None = None
    destination: Cluster | None = None
    return_cluster: Cluster | None = None
    forward_manual: bool = False
    reverse_manual: bool = False


class LogisticsVolumeEstimate(DtoModel):
    state: OzonVolumeState
    volume_l: Volume | None = None
    lower_l: Decimal | None = None
    upper_l: Decimal | None = None
    forward: TariffMoney | None = None
    reverse: TariffMoney | None = None
    effective_from: date | None = None
    effective_until: date | None = None


class LogisticsRouteTariff(DtoModel):
    origin: Cluster
    destination: Cluster
    under_300: tuple[TariffMoney, ...]
    over_300: tuple[TariffMoney, ...]


class LogisticsTariffCatalog(DtoModel):
    source_url: str
    source_sha256: str
    effective_from: date
    effective_until: date
    verified_on: date
    upper_l: tuple[Volume | None, ...]
    routes: tuple[LogisticsRouteTariff, ...]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.effective_until < self.effective_from or not self.routes:
            raise ValueError("Invalid logistics tariff period or empty catalog")
        if not self.upper_l or self.upper_l[-1] is not None:
            raise ValueError("Logistics tariffs must include an open last interval")
        previous = Decimal(0)
        for value in self.upper_l[:-1]:
            if value is None or value <= previous:
                raise ValueError("Logistics volume limits must increase")
            previous = value
        identities = set()
        for route in self.routes:
            identity = (route.origin, route.destination)
            if identity in identities:
                raise ValueError("Duplicate logistics route")
            identities.add(identity)
            if len(route.under_300) != len(self.upper_l) or len(route.over_300) != len(self.upper_l):
                raise ValueError("Incomplete logistics route")
        return self


class LogisticsTariffOptions(DtoModel):
    available: bool
    origins: tuple[str, ...] = ()
    destinations: tuple[str, ...] = ()
    effective_from: date | None = None
    effective_until: date | None = None
    source_url: str | None = None
