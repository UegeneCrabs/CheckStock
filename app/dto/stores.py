from pydantic import Field, RootModel

from app.dto.common import DtoModel


class Store(DtoModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    initials: str = Field(min_length=1, max_length=4)
    text: str = Field(pattern=r"^#[0-9A-Fa-f]{3,6}$")


class StoreItem(DtoModel):
    slug: str
    store: Store


class StoreCollection(RootModel[tuple[StoreItem, ...]]):
    root: tuple[StoreItem, ...] = ()


class StoreAccessContext(DtoModel):
    slug: str
    store: Store
