from typing import Literal

from pydantic import Field

from app.dto.common import DtoModel


class UnitEconomics1CCabinetValues(DtoModel):
    buyout_period_days: int = Field(default=14, ge=1, le=29)
    default_buyout_percent: float | None = Field(default=None, ge=0.01, le=100)
    acceptance_coefficient: float = Field(default=0, ge=0, le=1_000_000)
    wb_extra_tariff_percent: float = Field(default=0, ge=0, le=1_000_000)
    acquiring_percent: float = Field(default=3.8, ge=0, le=100)
    team_commission_percent: float = Field(default=0, ge=0, le=100)
    vat_percent: float = Field(default=9, ge=0, le=100)
    usn_percent: float = Field(default=0, ge=0, le=100)
    osno_percent: float = Field(default=0, ge=0, le=100)
    tax_system: Literal["usn", "osno"] = "usn"


class UnitEconomics1CCabinetSettingsRequest(UnitEconomics1CCabinetValues):
    pass


class UnitEconomics1CCabinetSettingsWebRequest(DtoModel):
    buyout_period_days: int = Field(default=14, ge=1, le=29)
    default_buyout_percent: float | None = Field(default=None, ge=0.01, le=100)
    acceptance_coefficient: float = Field(default=0, ge=0, le=1_000_000)
    wb_extra_tariff_percent: float = Field(default=0, ge=0, le=1_000_000)
    acquiring_percent: float = Field(default=3.8, ge=0, le=100)
    vat_percent: float = Field(default=9, ge=0, le=100)
    usn_percent: float = Field(default=0, ge=0, le=100)
    osno_percent: float = Field(default=0, ge=0, le=100)
    tax_system: Literal["usn", "osno"] = "usn"


class UnitEconomics1CCabinetSettings(UnitEconomics1CCabinetValues):
    store_slug: str = Field(min_length=1, max_length=100)
    marketplace: Literal["WB"] = "WB"
    updated_at: str | None = None
    updated_by_user_id: int | None = None
    updated_by_name: str | None = None


class UnitEconomics1CProductValues(DtoModel):
    delivery_wb_rub: float = Field(default=0, ge=0, le=1_000_000)
    return_cost_rub: float = Field(default=0, ge=0, le=1_000_000)
    volume_l: float = Field(default=0, ge=0, le=1_000_000)
    storage_wb_rub: float = Field(default=0, ge=0, le=1_000_000)


class UnitEconomics1CProductSettingsRequest(UnitEconomics1CProductValues):
    article: str = Field(min_length=1, max_length=500)


class UnitEconomics1CProductSettings(UnitEconomics1CProductValues):
    store_slug: str = Field(min_length=1, max_length=100)
    article: str = Field(min_length=1, max_length=500)
    marketplace: Literal["WB"] = "WB"
    updated_at: str | None = None
    updated_by_user_id: int | None = None
    updated_by_name: str | None = None


class UnitEconomics1CPriceChange(DtoModel):
    store_slug: str = Field(min_length=1, max_length=100)
    article: str = Field(min_length=1, max_length=500)
    target_price: float = Field(gt=0, le=1_000_000_000)
    target_kind: Literal["retail", "spp", "wallet"] = "retail"


class UnitEconomics1CPriceChangeRequest(DtoModel):
    data: tuple[UnitEconomics1CPriceChange, ...] = Field(min_length=1, max_length=1)


class UnitEconomics1CColumnPreferencesRequest(DtoModel):
    order: tuple[str, ...] = Field(max_length=50)
    hidden: tuple[str, ...] = Field(max_length=50)
