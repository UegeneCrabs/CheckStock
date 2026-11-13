from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, computed_field, model_validator

from app.core.constants import FBO_STOCK, TIME
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
    variant: str = ""
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
    destination_cluster: str = ""
    destination_cluster_id: str = ""
    planned_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    checked_at: str = ""
    acceptance_checked_at: str = ""
    note: str = ""
    warning: str = ""
    unavailable: bool = False
    composition_confirmed: bool = True
    items: tuple[InboundItem, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def normalize_saved_stage(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        status = value.get("status")
        if status in (FBO_STOCK.wb_accepted, FBO_STOCK.ozon_accepted, FBO_STOCK.yandex_accepted):
            return {**value, "stage": "completed"}
        if status == "OVERDUE":
            return {**value, "stage": "transit", "status_label": "Просрочена"}
        return value

    @computed_field
    @property
    def direction(self) -> str:
        return self.warehouse or self.destination_cluster or self.destination_cluster_id

    @computed_field
    @property
    def archived(self) -> bool:
        if self.stage in TERMINAL_STAGES:
            return True
        stamp = self.supply_date
        if stamp is None:
            return False
        today = datetime.now(TIME.moscow_timezone).date()
        return stamp.astimezone(TIME.moscow_timezone).date() < today - timedelta(days=FBO_STOCK.history_days)

    @computed_field
    @property
    def supply_date(self) -> datetime | None:
        for value in (self.planned_at, self.created_at, self.updated_at):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=TIME.moscow_timezone)
            except ValueError:
                continue
        return None

    @computed_field
    @property
    def listed_status(self) -> bool:
        return (
            self.status in (FBO_STOCK.wb_active | FBO_STOCK.ozon_active | FBO_STOCK.yandex_active)
            or self.status in (FBO_STOCK.ozon_returned | FBO_STOCK.yandex_returned)
            or self.status == FBO_STOCK.wb_deleted
            or self.status in (FBO_STOCK.wb_accepted, FBO_STOCK.ozon_accepted, FBO_STOCK.yandex_accepted)
        )

    @computed_field
    @property
    def quantity(self) -> int:
        return sum(item.quantity for item in self.items)

    @computed_field
    @property
    def accepted_quantity(self) -> int | None:
        if all(item.accepted_quantity is None for item in self.items):
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
        if self.unavailable or not self.listed_status:
            return None
        if not self.composition_confirmed and self.stage not in TERMINAL_STAGES:
            return None
        return sum(self.remaining_for(item) or 0 for item in self.items)

    def remaining_for(self, item: InboundItem) -> int | None:
        if self.unavailable or not self.listed_status:
            return None
        if self.stage in TERMINAL_STAGES:
            return 0
        return max(item.quantity - (item.accepted_quantity or 0), 0)

    @computed_field
    @property
    def item_remaining_quantities(self) -> tuple[int | None, ...]:
        return tuple(self.remaining_for(item) for item in self.items)


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


class InboundReport(DtoModel):
    ok: bool = True
    targets: tuple[InboundSnapshot, ...]
    stale_before: str
