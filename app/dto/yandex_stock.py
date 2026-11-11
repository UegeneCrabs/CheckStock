from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True, slots=True, kw_only=True)
class YandexWarehouseQuantities:
    on_hand: int
    available_for_order: int
    reserved: int

    def combined_with(self, other: Self) -> Self:
        return type(self)(
            on_hand=self.on_hand + other.on_hand,
            available_for_order=self.available_for_order + other.available_for_order,
            reserved=self.reserved + other.reserved,
        )
