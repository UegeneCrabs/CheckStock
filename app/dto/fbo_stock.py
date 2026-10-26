from pydantic import Field

from app.core.constants import FBO_STOCK, FboStockAction, FboStockErrorCode, FboStockIssue, FboStockState
from app.dto.common import DtoModel
from app.dto.inbound_supplies import InboundMarketplace, InboundSupply


class FboTarget(DtoModel):
    store_slug: str
    marketplace: InboundMarketplace


class FboBalance(DtoModel):
    article: str
    fulfillment: str
    quantity: int


class FboAllocation(FboBalance):
    quantity: int = Field(ge=0)
    barcode: str = ""
    name: str = ""
    purchase_price: float | None = None


class FboShortage(DtoModel):
    article: str
    required: int
    available: int
    missing: int


class FboMovement(DtoModel):
    supply: InboundSupply
    state: FboStockState = FboStockState.PENDING
    issue: FboStockIssue = FboStockIssue.NONE
    manual_required: bool = False
    allocations: tuple[FboAllocation, ...] = ()
    shortages: tuple[FboShortage, ...] = ()
    updated_at: str = ""
    posted_at: str = ""
    posted_by: str = ""
    fulfillment: str = ""
    checked_fulfillment: str = ""
    stock_checked: bool = False
    can_post: bool = False
    can_select_fulfillment: bool = False
    processed: bool = False


class FboLegacyBatch(DtoModel):
    id: int
    fulfillment: str
    items: tuple[FboAllocation, ...]
    sent_at: str


class FboEvidence(DtoModel):
    confirmed: bool = True
    supplies: tuple[InboundSupply, ...]
    movements: tuple[FboMovement, ...]
    balances: tuple[FboBalance, ...]
    legacy: tuple[FboLegacyBatch, ...]
    fulfillments: tuple[str, ...] = ()


class FboStockRequest(DtoModel):
    marketplace: InboundMarketplace
    supply_key: str = Field(min_length=1)
    action: FboStockAction = FboStockAction.POST
    batch_ids: tuple[int, ...] = ()
    fulfillment: str = ""


class FboStockCommand(DtoModel):
    target: FboTarget
    change: FboStockRequest
    request_key: str
    user_id: int
    user_name: str


class FboStockResult(DtoModel):
    ok: bool = True
    movement: FboMovement


class FboStockReport(DtoModel):
    ok: bool = True
    movements: tuple[FboMovement, ...]
    legacy: tuple[FboLegacyBatch, ...]
    linked_batch_ids: tuple[int, ...]
    fulfillments: tuple[str, ...] = ()


class FboStockError(Exception):
    def __init__(self, code: FboStockErrorCode) -> None:
        self.code = code
        super().__init__(code)


class FboActor(DtoModel):
    id: int | None = None
    name: str = FBO_STOCK.source
