from dataclasses import dataclass

from app.dto.common import DtoModel
from app.dto.ozon_economics import OzonTurnoverData
from app.dto.ozon_logistics import LogisticsReturn


class OzonBuyoutLine(DtoModel):
    order_key: str
    line_key: str
    article: str
    sold_quantity: int
    cancelled_quantity: int


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonBuyoutEvidence:
    turnover: OzonTurnoverData
    lines: tuple[OzonBuyoutLine, ...]
    returns: tuple[LogisticsReturn, ...]
