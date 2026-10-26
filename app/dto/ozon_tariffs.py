from datetime import date, datetime
from typing import Annotated, Self

from pydantic import Field, model_validator

from app.dto.common import DtoModel
from app.dto.ozon_commissions import CommissionPercent
from app.dto.ozon_orders import OzonPayload

TariffId = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class OzonTariff(DtoModel):
    id: TariffId
    group: str = Field(min_length=1)
    category: str = Field(min_length=1)
    type_name: str = Field(min_length=1)
    fbo_up_to_100: CommissionPercent
    fbo_up_to_300: CommissionPercent
    fbo_over_300: CommissionPercent
    source_row: int = Field(ge=3)


class OzonTariffCatalog(DtoModel):
    effective_from: date
    verified_on: date
    source_url: str
    source_page: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    worksheet: str
    items: tuple[OzonTariff, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_items(self) -> Self:
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Duplicate tariff identities")
        if self.effective_from > self.verified_on:
            raise ValueError("Future tariffs cannot replace the active catalog")
        return self


class OzonCategoryNode(OzonPayload):
    description_category_id: int = 0
    category_name: str = ""
    type_id: int = 0
    type_name: str = ""
    disabled: bool = False
    children: tuple["OzonCategoryNode", ...] = ()


class OzonCategoryTree(OzonPayload):
    result: tuple[OzonCategoryNode, ...]


class OzonCategoryTreeRequest(DtoModel):
    language: str


class OzonProductCategory(OzonPayload):
    offer_id: str
    description_category_id: int = 0
    type_id: int = 0


class OzonProductCategories(OzonPayload):
    items: tuple[OzonProductCategory, ...]


class OzonCategoryIdentity(DtoModel):
    category_id: int
    category_name: str
    type_id: int
    type_name: str


class OzonCategorySelection(OzonCategoryIdentity):
    tariff_id: TariffId | None = None
    received_at: datetime


class OzonTariffQuery(DtoModel):
    store: str = Field(min_length=1)
    article: str = Field(min_length=1)
