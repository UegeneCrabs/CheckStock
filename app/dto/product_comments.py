from pydantic import Field, field_validator

from app.core.constants import WEB
from app.dto.common import DtoModel


class ProductCommentKey(DtoModel):
    store_slug: str = Field(min_length=1, max_length=WEB.target_max_length)
    article: str = Field(min_length=1, max_length=WEB.product_comment_article_max_length)


class ProductComment(ProductCommentKey):
    text: str = Field(max_length=WEB.product_comment_max_length)

    @field_validator("text", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("Invalid comment text")
            return value.replace("\r\n", "\n").replace("\r", "\n")
        return value


class ProductComments(DtoModel):
    comments: tuple[ProductComment, ...]


class ProductCommentImport(DtoModel):
    comments: tuple[ProductComment, ...] = Field(max_length=WEB.product_comment_import_limit)


class ImportedProductComments(DtoModel):
    accepted: tuple[ProductCommentKey, ...]
