from pydantic import Field

from app.dto.common import DtoModel
from app.dto.ozon_orders import OzonPayload


class OzonRelatedQuery(DtoModel):
    store: str = Field(min_length=1)
    article: str = Field(min_length=1)


class OzonRelatedItem(DtoModel):
    article: str = Field(min_length=1)
    name: str
    image_url: str | None = None


class OzonRelatedProducts(DtoModel):
    items: tuple[OzonRelatedItem, ...]
    complete: bool = True


class OzonModelInfo(OzonPayload):
    model_id: int = Field(ge=0)


class OzonModelProduct(OzonPayload):
    offer_id: str = Field(min_length=1)
    model_info: OzonModelInfo | None = None


class OzonModelProducts(OzonPayload):
    items: tuple[OzonModelProduct, ...]
