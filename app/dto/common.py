from typing import Self

from pydantic import BaseModel, ConfigDict


class DtoModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        from_attributes=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    def __getitem__(self, key: str) -> object:
        return getattr(self, key)

    def get(self, key: str, default: object = None) -> object:
        return getattr(self, key, default)

    @classmethod
    def from_row(cls, row: object) -> Self:
        if hasattr(row, "_mapping"):
            row = row._mapping
        return cls.model_validate(row)
