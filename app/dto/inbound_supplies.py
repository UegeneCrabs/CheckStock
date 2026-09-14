from typing import Literal

from pydantic import Field, computed_field

from app.dto.common import DtoModel

InboundMarketplace = Literal["WB", "OZON", "YANDEX MARKET"]
InboundStage = Literal[
    "planned", "transit", "acceptance", "placement", "discrepancy", "completed", "cancelled", "unknown"
]
STAGE_LABELS = {
    "planned": "Запланировано",
    "transit": "В пути / транзит",
    "acceptance": "На приёмке",
    "placement": "Принято, размещается",
    "discrepancy": "Расхождения / спор",
    "completed": "Завершено",
    "cancelled": "Отменено / отказ",
    "unknown": "Статус требует проверки",
}
TERMINAL_STAGES = frozenset({"completed", "cancelled"})


class InboundItem(DtoModel):
    article: str = ""
    barcode: str = ""
    name: str = ""
    sku: str = ""
    vendor_code: str = ""
    quantity: int = Field(ge=0)
    accepted_quantity: int | None = Field(default=None, ge=0)
    ready_quantity: int | None = Field(default=None, ge=0)
    shortage_quantity: int | None = Field(default=None, ge=0)
    surplus_quantity: int | None = Field(default=None, ge=0)
    defect_quantity: int | None = Field(default=None, ge=0)


class InboundSupply(DtoModel):
    key: str = Field(min_length=1)
    supply_id: str = Field(min_length=1)
    order_id: str = ""
    parent_key: str = ""
    number: str = ""
    campaign_id: str = ""
    campaign_name: str = ""
    status: str
    status_label: str
    stage: InboundStage
    warehouse: str = ""
    transit_warehouse: str = ""
    planned_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    checked_at: str = ""
    note: str = ""
    warning: str = ""
    unavailable: bool = False
    items: tuple[InboundItem, ...] = ()

    @computed_field
    @property
    def quantity(self) -> int:
        return sum(item.quantity for item in self.items)

    @computed_field
    @property
    def accepted_quantity(self) -> int | None:
        if any(item.accepted_quantity is None for item in self.items):
            return None
        return sum(item.accepted_quantity or 0 for item in self.items)

    @computed_field
    @property
    def ready_quantity(self) -> int | None:
        if not self.items or any(item.ready_quantity is None for item in self.items):
            return None
        return sum(item.ready_quantity or 0 for item in self.items)

    @computed_field
    @property
    def remaining_quantity(self) -> int | None:
        if self.unavailable or self.stage in {"unknown", "discrepancy"}:
            return None
        if self.stage in TERMINAL_STAGES:
            return 0
        if self.stage == "planned":
            return self.quantity
        if self.stage == "transit":
            return sum(max(item.quantity - (item.ready_quantity or 0), 0) for item in self.items)
        if self.stage == "placement":
            if self.ready_quantity is None:
                return None
            return sum(max(item.quantity - (item.ready_quantity or 0), 0) for item in self.items)
        if self.accepted_quantity is None:
            return None
        return sum(max(item.quantity - (item.accepted_quantity or 0), 0) for item in self.items)


class InboundSnapshot(DtoModel):
    store_slug: str
    marketplace: InboundMarketplace
    status: str = "never"
    last_attempt: str | None = None
    last_success: str | None = None
    last_finished: str | None = None
    error: str = ""
    supplies: tuple[InboundSupply, ...] = ()


class InboundSyncRequest(DtoModel):
    store: str = ""
    marketplace: str = ""
