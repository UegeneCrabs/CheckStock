from pydantic import Field

from app.dto.common import DtoModel


class PricePreview(DtoModel):
    store_slug: str = Field(min_length=1, max_length=100)
    article: str = Field(min_length=1, max_length=255)
    seller_price: float = Field(ge=0.01, le=1_000_000_000, allow_inf_nan=False)


class PriceConfirmation(DtoModel):
    preview_id: str = Field(min_length=1, max_length=64)
