from datetime import date, datetime

from pydantic import Field, PositiveInt, RootModel, model_validator

from app.dto.common import DtoModel
from app.dto.marketplace import Marketplace


class RnpStrategyRequest(DtoModel):
    store: str = Field(min_length=1, max_length=100)
    marketplace: Marketplace
    article: str = Field(min_length=1, max_length=200)
    strategy: str = Field(min_length=1, max_length=80)
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def validate_period(self) -> "RnpStrategyRequest":
        if self.date_to < self.date_from:
            raise ValueError("Конец стратегии должен быть не раньше начала")
        return self


class RnpStrategy(DtoModel):
    store_slug: str
    marketplace: Marketplace
    article: str
    strategy: str
    date_from: date
    date_to: date
    updated_by: str
    updated_at: datetime


class RnpActionRequest(DtoModel):
    store: str = Field(min_length=1, max_length=100)
    marketplace: Marketplace
    article: str = Field(min_length=1, max_length=200)
    action_date: date
    note: str = Field(min_length=1, max_length=500)


class RnpAction(DtoModel):
    id: PositiveInt
    article: str
    action_date: date
    note: str
    user_name: str
    created_at: datetime


class RnpSyncRequest(DtoModel):
    store: str = Field(min_length=1, max_length=100)
    marketplace: Marketplace
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    articles: tuple[str, ...] | None = None


class RnpArticleQuery(DtoModel):
    store_slug: str
    marketplace: Marketplace
    article: str


class SaveRnpStrategyCommand(DtoModel):
    request: RnpStrategyRequest
    updated_by: str
    updated_at: datetime


class AddRnpActionCommand(DtoModel):
    request: RnpActionRequest
    user_id: PositiveInt | None = None
    user_name: str
    created_at: datetime


class RnpArticleExists(RootModel[bool]):
    root: bool
