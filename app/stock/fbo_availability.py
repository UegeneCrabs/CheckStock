from collections import defaultdict

from app.core.constants import FBO_STOCK, FboStockIssue, FboStockState
from app.dto.fbo_stock import FboAllocation, FboBalance, FboEvidence, FboMovement, FboShortage, FboTarget
from app.dto.inbound_supplies import InboundSupply


def supply_shortages(
    supply: InboundSupply,
    balances: tuple[FboBalance, ...],
    allocations: tuple[FboAllocation, ...],
    *,
    fulfillment: str,
) -> tuple[FboShortage, ...]:
    requested: dict[str, int] = defaultdict(int)
    for item in supply.items:
        requested[item.article] += item.quantity
    available: dict[str, int] = defaultdict(int)
    for balance in balances:
        if balance.fulfillment == fulfillment:
            available[balance.article] += max(balance.quantity, 0)
    for allocation in allocations:
        if allocation.fulfillment == fulfillment:
            available[allocation.article] += allocation.quantity
    return tuple(
        FboShortage(
            article=article,
            required=quantity,
            available=available[article],
            missing=quantity - available[article],
        )
        for article, quantity in sorted(requested.items())
        if quantity > available[article]
    )


def movement_shortages(movement: FboMovement, balances: tuple[FboBalance, ...]) -> tuple[FboShortage, ...]:
    fulfillment = movement.fulfillment or movement.checked_fulfillment
    if not fulfillment:
        return ()
    if movement.state in {FboStockState.HISTORICAL, FboStockState.CANCELLED}:
        return ()
    if movement.state == FboStockState.ACCEPTED and not movement.manual_required:
        return ()
    return supply_shortages(movement.supply, balances, movement.allocations, fulfillment=fulfillment)


def is_accepted(target: FboTarget, supply: InboundSupply) -> bool:
    return (
        supply.status
        == {
            "WB": FBO_STOCK.wb_accepted,
            "OZON": FBO_STOCK.ozon_accepted,
            "YANDEX MARKET": FBO_STOCK.yandex_accepted,
        }[target.marketplace]
    )


def is_active(target: FboTarget, supply: InboundSupply) -> bool:
    return (
        supply.status
        in {
            "WB": FBO_STOCK.wb_active,
            "OZON": FBO_STOCK.ozon_active,
            "YANDEX MARKET": FBO_STOCK.yandex_active,
        }[target.marketplace]
    )


def allocations_match(movement: FboMovement) -> bool:
    expected: dict[str, int] = defaultdict(int)
    actual: dict[str, int] = defaultdict(int)
    for item in movement.supply.items:
        expected[item.article] += item.quantity
    for allocation in movement.allocations:
        actual[allocation.article] += allocation.quantity
    return {key: value for key, value in expected.items() if value} == {
        key: value for key, value in actual.items() if value
    }


def movement_availability(target: FboTarget, movement: FboMovement, evidence: FboEvidence) -> FboMovement:
    sources = {row.fulfillment for row in movement.allocations}
    fulfillment = movement.fulfillment or (next(iter(sources)) if len(sources) == 1 else "")
    current = movement.model_copy(update={"fulfillment": fulfillment})
    shortages = movement_shortages(current, evidence.balances)
    valid_items = bool(current.supply.quantity) and all(
        item.article for item in current.supply.items if item.quantity
    )
    can_select = (
        not current.posted_at
        and not current.allocations
        and valid_items
        and is_active(target, current.supply)
    )
    can_post = (
        evidence.confirmed
        and not current.supply.unavailable
        and current.supply.stage not in {FBO_STOCK.stage_unknown, FBO_STOCK.stage_cancelled}
        and bool(fulfillment)
        and fulfillment in evidence.fulfillments
        and (not sources or sources == {fulfillment})
        and valid_items
        and not shortages
        and current.issue not in {FboStockIssue.LEGACY, FboStockIssue.CANCELLED, FboStockIssue.UNCONFIRMED}
        and (
            is_active(target, current.supply)
            or (bool(current.posted_at) and is_accepted(target, current.supply))
        )
        and (not current.posted_at or not allocations_match(current))
    )
    return current.model_copy(
        update={
            "shortages": shortages,
            "stock_checked": bool(fulfillment or current.checked_fulfillment),
            "can_post": can_post,
            "can_select_fulfillment": can_select,
            "processed": bool(fulfillment or current.allocations),
        }
    )
